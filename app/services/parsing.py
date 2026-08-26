import asyncio
import io
import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import structlog
from docling.service_client.job import AsyncConversionJob
from sqlalchemy import CursorResult, delete, select, update

from app.core.config import Settings, get_settings
from app.core.database import sync_session_factory
from app.core.docling_service import (
    ParsedConversion,
    open_client,
    submit_document,
)
from app.core.object_storage import generate_object_key, get_client, utcnow_naive
from app.models.document import DocumentBlock, DocumentVersion, ParseStatus

logger = structlog.get_logger(__name__)

# 认领(PARSING)到 job_id 落库之间的宽限期:submit 任务可能在途(上传/排队),
# 此窗口内没有 job_id 不算卡死;超过则视为 submit 任务丢失,由对账标失败。
_JOB_ID_GRACE_SECONDS = 120

_POLL_WAIT_SECONDS = 2.0


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


def find_stuck_parsing_versions(session, *, limit: int | None = None) -> list[int]:
    """捞所有 parse_status=PARSING 的版本 id(对账扫描入口)。"""
    stmt = select(DocumentVersion.id).where(
        DocumentVersion.parse_status == ParseStatus.PARSING
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


def submit_parse(version_id: int) -> None:
    """worker 任务:校验版本 → MinIO 下载 → 提交 docling → 存 task_id;失败标 FAILED。"""
    settings = get_settings()
    try:
        with sync_session_factory() as session:
            version = session.get(DocumentVersion, version_id)
            if version is None or version.parse_status != ParseStatus.PARSING:
                return
            file_name = version.file_name
            object_key = version.object_key

        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = Path(tmp_dir) / file_name
            get_client().fget_object(settings.silo_bucket, object_key, str(local_path))
            job = asyncio.run(_submit_to_docling(str(local_path), file_name, settings))
            task_id = job.task_id

        with sync_session_factory() as session:
            session.execute(
                update(DocumentVersion)
                .where(
                    DocumentVersion.id == version_id,
                    DocumentVersion.parse_status == ParseStatus.PARSING,
                )
                .values(parse_job_id=task_id)
            )
            session.commit()
    except Exception as exc:  # worker 任务,任何异常都落库为 failed
        logger.exception("parse_submit_failed", version_id=version_id, error=str(exc))
        _mark_failed(version_id, str(exc)[:2000])


async def _submit_to_docling(
    file_path: str, file_name: str, settings
) -> AsyncConversionJob:
    """薄桥:docling 官方 async 客户端在任务内独立循环中一次性提交,无跨循环状态。"""
    async with open_client(settings) as client:
        return await submit_document(
            client,
            file_path=file_path,
            file_name=file_name,
            settings=settings,
        )


@dataclass(slots=True)
class PollOutcome:
    version_id: int
    status: str  # success / failure / skipped / running / poll_error
    payload: object | None = None
    error: str | None = None


def reconcile_parse() -> None:
    """beat 定时对账:扫 PARSING → 并行 poll docling 一次 → 终态落库。

    - success → 取结果写 MinIO + blocks + PARSED
    - failure/skipped/poll_error → FAILED
    - running → 保持 PARSING,下一轮再查
    - 无 job_id(超宽限期)或超时 → FAILED
    """
    settings = get_settings()
    with sync_session_factory() as session:
        ids = find_stuck_parsing_versions(
            session, limit=settings.parsing_reconcile_limit
        )
        rows: list[tuple[int, str | None, datetime]] = []
        for version_id in ids:
            version = session.get(DocumentVersion, version_id)
            if version is None:
                continue
            rows.append(
                (
                    version_id,
                    version.parse_job_id,
                    version.parsing_started_at or utcnow_naive(),
                )
            )

    pending: list[tuple[int, str]] = []
    for version_id, job_id, started_at in rows:
        deadline = started_at + timedelta(
            minutes=settings.docling_parse_timeout_minutes
        )
        if utcnow_naive() > deadline:
            _mark_failed(version_id, "解析超时")
        elif job_id is None:
            # 宽限期内的空 job_id 视为 submit 仍在途,跳过;超过则 submit 任务丢失
            if utcnow_naive() > started_at + timedelta(seconds=_JOB_ID_GRACE_SECONDS):
                _mark_failed(version_id, "解析任务 id 缺失，请重新触发解析")
        else:
            pending.append((version_id, job_id))

    if not pending:
        return

    outcomes = asyncio.run(_poll_batch(pending, settings))
    for outcome in outcomes:
        if outcome.status == "success":
            parsed = _payload_to_parsed_conversion(outcome.payload)
            try:
                _persist_result(
                    version_id=outcome.version_id, parsed=parsed, settings=settings
                )
            except Exception as exc:  # worker 任务,任何异常都落库为 failed
                logger.exception(
                    "parse_persist_failed",
                    version_id=outcome.version_id,
                    error=str(exc),
                )
                _mark_failed(outcome.version_id, str(exc)[:2000])
        elif outcome.status in ("failure", "skipped"):
            _mark_failed(
                outcome.version_id, f"docling 转换失败: status={outcome.status}"
            )
        elif outcome.error:
            # poll_error(404 任务丢失/服务不可达等)
            _mark_failed(outcome.version_id, outcome.error[:2000])


async def _poll_batch(
    pending: list[tuple[int, str]], settings: Settings
) -> list[PollOutcome]:
    """薄桥:单次 asyncio.run 内并发 poll 所有卡住版本,避免串行阻塞对账轮次。"""

    async def poll_one(version_id: int, task_id: str) -> PollOutcome:
        try:
            async with open_client(settings) as client:
                status = await client._poll_task_status(
                    task_id, wait=_POLL_WAIT_SECONDS
                )
                terminal = status.task_status
                if terminal != "success":
                    return PollOutcome(version_id, status=terminal)
                # __aenter__ 后 _async_client 必已创建;显式 cast 满足类型检查
                async_client: httpx.AsyncClient = cast(
                    httpx.AsyncClient, client._async_client
                )
                payload = await client._fetch_convert_result_payload(
                    task_id, last_status=status, async_client=async_client
                )
                return PollOutcome(version_id, status="success", payload=payload)
        except Exception as exc:  # noqa: BLE001
            return PollOutcome(version_id, status="poll_error", error=str(exc)[:2000])

    return await asyncio.gather(*(poll_one(v, t) for v, t in pending))


def _persist_result(version_id: int, parsed: ParsedConversion, settings) -> None:
    """解析产物写 MinIO + blocks 入库 + 状态回写(成功/partial 共用)。"""
    if parsed.status not in ("success", "partial_success"):
        raise ParseError(f"docling 转换失败: status={parsed.status}")

    with sync_session_factory() as session:
        version = session.get(DocumentVersion, version_id)
        if version is None:
            return
        version_public_id = version.public_id

    md_key = generate_object_key(f"parsed/{version_public_id}", "parsed.md")
    json_key = generate_object_key(f"parsed/{version_public_id}", "parsed.json")

    md_bytes = parsed.markdown.encode("utf-8")
    get_client().put_object(
        settings.silo_bucket,
        md_key,
        io.BytesIO(md_bytes),
        len(md_bytes),
        content_type="text/markdown",
    )
    json_bytes = json.dumps(parsed.document_json, ensure_ascii=False).encode("utf-8")
    get_client().put_object(
        settings.silo_bucket,
        json_key,
        io.BytesIO(json_bytes),
        len(json_bytes),
        content_type="application/json",
    )

    blocks = extract_blocks(parsed.document_json)
    partial_note = "; ".join(parsed.errors)[:2000] if parsed.errors else None

    with sync_session_factory() as session:
        result: CursorResult = cast(
            CursorResult,
            session.execute(
                update(DocumentVersion)
                .where(
                    DocumentVersion.id == version_id,
                    DocumentVersion.parse_status == ParseStatus.PARSING,
                )
                .values(
                    parse_status=ParseStatus.PARSED,
                    parsed_object_key=json_key,
                    parsed_markdown_object_key=md_key,
                    parsed_at=utcnow_naive(),
                    parse_error=(
                        partial_note if parsed.status == "partial_success" else None
                    ),
                )
            ),
        )
        if result.rowcount == 0:
            session.rollback()
            return
        session.execute(
            delete(DocumentBlock).where(DocumentBlock.version_id == version_id)
        )
        for block in blocks:
            session.add(DocumentBlock(version_id=version_id, **block))
        session.commit()


def _mark_failed(version_id: int, message: str) -> None:
    """条件 UPDATE 守卫:只有仍处 PARSING 的版本才迁移到 FAILED(幂等)。"""
    with sync_session_factory() as session:
        session.execute(
            update(DocumentVersion)
            .where(
                DocumentVersion.id == version_id,
                DocumentVersion.parse_status == ParseStatus.PARSING,
            )
            .values(parse_status=ParseStatus.FAILED, parse_error=message)
        )
        session.commit()


def _payload_to_parsed_conversion(payload) -> ParsedConversion:
    """把 docling-serve 的 ConvertDocumentResponse 响应体映射为项目内类型。

    仅在 poll 路径使用(官方客户端无公开的按 task_id 重建 Job 接口,
    这里走其私有 _poll_task_status/_fetch_convert_result_payload)。
    serve 响应中 json_content 是 DoclingDocument 模型对象(非 dict),
    需经 export_to_dict() 导出,与 submit 路径产物格式保持一致。
    """
    document = payload.document
    json_content = document.json_content
    if isinstance(json_content, dict):
        document_json = json_content
    elif hasattr(json_content, "export_to_dict"):
        document_json = json_content.export_to_dict()
    else:
        document_json = {}
    return ParsedConversion(
        status=getattr(payload.status, "value", str(payload.status)),
        markdown=document.md_content or "",
        document_json=document_json,
        errors=[
            getattr(e, "error_message", None) or str(e) for e in (payload.errors or [])
        ],
    )
