import asyncio
import json
import tempfile
from pathlib import Path

import structlog
from sqlalchemy import delete

from app.core.config import get_settings
from app.core.database import async_session_factory
from app.core.docling_service import (
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
from app.models.document import DocumentBlock, ParseStatus
from app.repositories.documents import get_version_by_id

logger = structlog.get_logger(__name__)

_background_tasks: set[asyncio.Task] = set()


class ParseError(Exception):
    """解析失败(可转 parse_error 落库)。"""


def extract_blocks(document_json: dict) -> list[dict]:
    """把 DoclingDocument JSON 抽成 blocks(顺序:先文本后表格,各按文档内顺序)。"""
    blocks: list[dict] = []
    index = 0
    for item in document_json.get("texts") or []:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        index += 1
        page_no = ((item.get("prov") or [{}])[0] or {}).get("page_no")
        blocks.append(
            {
                "order_index": index,
                "block_type": item.get("label") or "text",
                "text": text,
                "page_no": page_no,
            }
        )
    for item in document_json.get("tables") or []:
        data = item.get("data") or {}
        index += 1
        page_no = ((item.get("prov") or [{}])[0] or {}).get("page_no")
        blocks.append(
            {
                "order_index": index,
                "block_type": "table",
                "text": data.get("html") or "",
                "page_no": page_no,
            }
        )
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
        public_id = version.public_id

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

        await _await_and_persist(version_id, public_id, job, settings)


async def _await_and_persist(
    version_id: int,
    version_public_id,
    job,
    settings,
) -> None:
    """等待 Job 终态,把产物写 MinIO + blocks 入库 + 状态回写。"""
    timeout_seconds = settings.docling_parse_timeout_minutes * 60.0
    conversion = await job.result(timeout=timeout_seconds)
    parsed = to_parsed_conversion(conversion)

    if parsed.status not in ("success", "partial_success"):
        raise ParseError(f"docling 转换失败: status={parsed.status}")

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
