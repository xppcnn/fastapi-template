import asyncio
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import cast

import httpx
import structlog
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.database import async_session_factory
from app.core.docling_service import (
    ParsedConversion,
    open_client,
    submit_document,
    to_parsed_conversion,
)
from app.core.object_storage import (
    fget_object,
    generate_object_key,
    put_object,
    utcnow_naive,
)
from app.models.document import DocumentBlock, DocumentVersion, ParseStatus
from app.repositories.documents import get_version_by_id

logger = structlog.get_logger(__name__)

_background_tasks: set[asyncio.Task] = set()


class ParseError(Exception):
    """解析失败(可转 parse_error 落库)。"""


def _table_to_markdown(data: dict) -> str:
    """把 DoclingDocument 序列化后的表格(table_cells)组装成 Markdown。

    TableData 序列化没有 html 字段,只有 table_cells(单元格文本 + 行列偏移),
    按偏移填入网格再输出为 Markdown 管道表格。
    """
    cells = data.get("table_cells") or []
    try:
        num_rows = int(data.get("num_rows") or 0)
        num_cols = int(data.get("num_cols") or 0)
    except (TypeError, ValueError):
        return ""
    if num_rows <= 0 or num_cols <= 0:
        return ""

    grid: list[list[str]] = [[""] * num_cols for _ in range(num_rows)]
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        text = str(cell.get("text") or "").strip()
        r1 = int(cell.get("start_row_offset_idx") or 0)
        c1 = int(cell.get("start_col_offset_idx") or 0)
        r2 = int(cell.get("end_row_offset_idx") or r1 + 1)
        c2 = int(cell.get("end_col_offset_idx") or c1 + 1)
        r1, r2 = max(0, min(r1, num_rows)), max(0, min(r2, num_rows))
        c1, c2 = max(0, min(c1, num_cols)), max(0, min(c2, num_cols))
        for r in range(r1, r2):
            for c in range(c1, c2):
                grid[r][c] = text

    lines = ["| " + " | ".join(row) + " |" for row in grid]
    return "\n".join(lines)


def extract_blocks(document_json: dict) -> list[dict]:
    """把 DoclingDocument JSON 抽成 blocks。

    真实结构:body.children 是深度嵌套树——texts 节点(section_header 等)
    和 groups 节点都可能带 children(引用 texts/groups/tables),表格往往
    挂在某个 section 的 children 里;DOCX 无 prov 坐标。遍历时把 text 与
    group 节点都当作树节点递归展开(先父后子,即标题在前内容在后),
    同一节点仅访问一次;全部解析失败时回退为先文本后表格。
    """
    text_items = document_json.get("texts") or []
    table_items = document_json.get("tables") or []
    group_items = document_json.get("groups") or []
    blocks: list[dict] = []
    index = 0

    def add(item: dict, kind: str) -> None:
        nonlocal index
        if kind == "table":
            text = _table_to_markdown(item.get("data") or {})
            block_type = "table"
        else:
            text = (item.get("text") or "").strip()
            if not text:
                return
            block_type = item.get("label") or "text"
        page_no = ((item.get("prov") or [{}])[0] or {}).get("page_no")
        index += 1
        blocks.append(
            {
                "order_index": index,
                "block_type": block_type,
                "text": text,
                "page_no": page_no,
            }
        )

    def handle_ref(ref: dict) -> None:
        target = ref.get("$ref") or ""
        if target.startswith("#/texts/"):
            idx = int(target.rsplit("/", 1)[1])
            if idx in visited_texts or idx >= len(text_items):
                return
            visited_texts.add(idx)
            item = text_items[idx]
            add(item, "text")
            for child in item.get("children") or []:
                if isinstance(child, dict):
                    handle_ref(child)
        elif target.startswith("#/tables/"):
            idx = int(target.rsplit("/", 1)[1])
            if 0 <= idx < len(table_items):
                add(table_items[idx], "table")
        elif target.startswith("#/groups/"):
            idx = int(target.rsplit("/", 1)[1])
            if idx in visited_groups or idx >= len(group_items):
                return
            visited_groups.add(idx)
            for child in group_items[idx].get("children") or []:
                if isinstance(child, dict):
                    handle_ref(child)

    visited_texts: set[int] = set()
    visited_groups: set[int] = set()

    body = document_json.get("body")
    refs = (body.get("children") or []) if isinstance(body, dict) else []
    if refs:
        for ref in refs:
            if isinstance(ref, dict):
                handle_ref(ref)
    if blocks:
        return blocks

    # 回退:无 body 引用(或全部无法解析)时,先文本后表格
    for item in text_items:
        add(item, "text")
    for item in table_items:
        add(item, "table")
    return blocks


def spawn_parse(version_id: int) -> None:
    task = asyncio.create_task(run_parse(version_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def run_parse(version_id: int) -> None:
    """后台任务:下载原文件 → 提交 docling → 等待结果 → 写产物与状态。"""
    settings = get_settings()
    try:
        await _run_parse(version_id, settings)
    except Exception as exc:
        logger.exception("parse_failed", version_id=version_id, error=str(exc))
        await _mark_failed(version_id, str(exc)[:2000])


async def _run_parse(version_id: int, settings) -> None:
    async with async_session_factory() as session:
        version = await get_version_by_id(session, version_id=version_id)
        if version is None or version.parse_status != ParseStatus.PARSING:
            return
        file_name = version.file_name
        object_key = version.object_key

    async with open_client(settings) as client:
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = Path(tmp_dir) / file_name
            await fget_object(object_key, str(local_path))
            job = await submit_document(
                client,
                file_path=str(local_path),
                file_name=file_name,
                settings=settings,
            )

        async with async_session_factory() as session:
            version = await get_version_by_id(session, version_id=version_id)
            if version is None:
                raise ParseError("版本不存在")
            version.parse_job_id = job.task_id
            await session.commit()

        await _await_and_persist(version_id, job, settings)


async def _await_and_persist(version_id: int, job, settings) -> None:
    """等待 Job 终态,再落到 MinIO + blocks + 状态。"""
    timeout_seconds = settings.docling_parse_timeout_minutes * 60.0
    conversion = await job.result(timeout=timeout_seconds)
    parsed = to_parsed_conversion(conversion)
    await _persist_result(version_id, parsed)


async def _persist_result(version_id: int, parsed) -> None:
    """把解析产物写 MinIO + blocks 入库 + 状态回写(成功/partial 共用)。"""
    if parsed.status not in ("success", "partial_success"):
        raise ParseError(f"docling 转换失败: status={parsed.status}")

    async with async_session_factory() as session:
        version = await get_version_by_id(session, version_id=version_id)
        if version is None:
            return
        version_public_id = version.public_id

    md_key = generate_object_key(f"parsed/{version_public_id}", "parsed.md")
    json_key = generate_object_key(f"parsed/{version_public_id}", "parsed.json")

    md_bytes = parsed.markdown.encode("utf-8")
    await put_object(md_key, md_bytes, len(md_bytes), content_type="text/markdown")
    json_bytes = json.dumps(parsed.document_json, ensure_ascii=False).encode("utf-8")
    await put_object(
        json_key, json_bytes, len(json_bytes), content_type="application/json"
    )

    blocks = extract_blocks(parsed.document_json)
    partial_note = "; ".join(parsed.errors)[:2000] if parsed.errors else None

    async with async_session_factory() as session:
        version = await get_version_by_id(session, version_id=version_id)
        if version is None:
            return
        version.parse_status = ParseStatus.PARSED
        version.parsed_object_key = json_key
        version.parsed_markdown_object_key = md_key
        version.parsed_at = utcnow_naive()
        version.parse_error = (
            partial_note if parsed.status == "partial_success" else None
        )
        await session.execute(
            delete(DocumentBlock).where(DocumentBlock.version_id == version_id)
        )
        for block in blocks:
            session.add(DocumentBlock(version_id=version_id, **block))
        await session.commit()


async def _mark_failed(version_id: int, message: str) -> None:
    async with async_session_factory() as session:
        version = await get_version_by_id(session, version_id=version_id)
        if version is None:
            return
        version.parse_status = ParseStatus.FAILED
        version.parse_error = message
        await session.commit()


_POLL_WAIT_SECONDS = 5.0


async def find_stuck_parsing_versions(session) -> list[int]:
    """启动对账:捞所有 parse_status=PARSING 的版本 id。"""
    rows = await session.scalars(
        select(DocumentVersion.id).where(
            DocumentVersion.parse_status == ParseStatus.PARSING
        )
    )
    return list(rows)


def resume_parse(version_id: int) -> None:
    """对账续跑:任务在 serve 侧还活着就等它完成。"""
    task = asyncio.create_task(_resume_parse(version_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _resume_parse(version_id: int) -> None:
    settings = get_settings()
    try:
        async with async_session_factory() as session:
            version = await get_version_by_id(session, version_id=version_id)
            if version is None or version.parse_status != ParseStatus.PARSING:
                return
            task_id = version.parse_job_id
            started_at = version.parsing_started_at or utcnow_naive()
        if task_id is None:
            raise ParseError("解析任务 id 缺失，请重新触发解析")

        deadline = started_at + timedelta(
            minutes=settings.docling_parse_timeout_minutes
        )

        async with open_client(settings) as client:
            while True:
                if utcnow_naive() > deadline:
                    raise ParseError("解析超时")
                status = await client._poll_task_status(
                    task_id, wait=_POLL_WAIT_SECONDS
                )
                if status.task_status == "success":
                    break
                if status.task_status in ("failure", "skipped"):
                    raise ParseError(f"docling 转换失败: status={status.task_status}")
                await asyncio.sleep(_POLL_WAIT_SECONDS)
            # __aenter__ 后 _async_client 必已创建;显式 cast 满足类型检查
            async_client: httpx.AsyncClient = cast(
                httpx.AsyncClient, client._async_client
            )
            payload = await client._fetch_convert_result_payload(
                task_id, last_status=status, async_client=async_client
            )

        parsed = _payload_to_parsed_conversion(payload)
        await _persist_result(version_id, parsed)
    except Exception as exc:
        logger.exception("resume_parse_failed", version_id=version_id, error=str(exc))
        await _mark_failed(version_id, str(exc)[:2000])


def _payload_to_parsed_conversion(payload) -> ParsedConversion:
    """把 docling-serve 的 ConvertDocumentResponse 响应体映射为项目内类型。

    仅在 resume 路径使用(官方客户端无公开的按 task_id 重建 Job 接口,
    这里走其私有 _poll_task_status/_fetch_convert_result_payload)。
    """
    document = payload.document
    json_content = document.json_content
    return ParsedConversion(
        status=getattr(payload.status, "value", str(payload.status)),
        markdown=document.md_content or "",
        document_json=json_content if isinstance(json_content, dict) else {},
        errors=[
            getattr(e, "error_message", None) or str(e) for e in (payload.errors or [])
        ],
    )
