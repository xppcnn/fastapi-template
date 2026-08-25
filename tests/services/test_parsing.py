import asyncio
from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base
from app.models.document import DocumentBlock, DocumentVersion, ParseStatus
from app.services.parsing import extract_blocks, run_parse


@pytest.fixture
def session_factory() -> Generator[async_sessionmaker, None, None]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async def create_schema() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(create_schema())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    asyncio.run(engine.dispose())


SAMPLE_DOCLING_JSON = {
    "body": {
        "children": [
            {"$ref": "#/texts/0"},
            {"$ref": "#/texts/1"},
            {"$ref": "#/tables/0"},
            {"$ref": "#/texts/2"},
        ]
    },
    "texts": [
        {"text": "第一章 招标公告", "label": "title", "prov": [{"page_no": 1}]},
        {"text": "本项目为示例。", "label": "paragraph", "prov": [{"page_no": 1}]},
        {"text": "预算：100万", "label": "paragraph", "prov": [{"page_no": 2}]},
    ],
    "tables": [
        {
            "label": "table",
            "prov": [{"page_no": 2}],
            "data": {
                "num_rows": 2,
                "num_cols": 2,
                "table_cells": [
                    {
                        "text": "序号",
                        "start_row_offset_idx": 0,
                        "end_row_offset_idx": 1,
                        "start_col_offset_idx": 0,
                        "end_col_offset_idx": 1,
                    },
                    {
                        "text": "项目",
                        "start_row_offset_idx": 0,
                        "end_row_offset_idx": 1,
                        "start_col_offset_idx": 1,
                        "end_col_offset_idx": 2,
                    },
                    {
                        "text": "1",
                        "start_row_offset_idx": 1,
                        "end_row_offset_idx": 2,
                        "start_col_offset_idx": 0,
                        "end_col_offset_idx": 1,
                    },
                    {
                        "text": "信息化平台",
                        "start_row_offset_idx": 1,
                        "end_row_offset_idx": 2,
                        "start_col_offset_idx": 1,
                        "end_col_offset_idx": 2,
                    },
                ],
            },
        }
    ],
}


def test_extract_blocks_follows_body_order() -> None:
    blocks = extract_blocks(SAMPLE_DOCLING_JSON)
    assert len(blocks) == 4
    assert [b["block_type"] for b in blocks] == [
        "title",
        "paragraph",
        "table",
        "paragraph",
    ]
    assert blocks[2]["page_no"] == 2
    assert "| 序号 | 项目 |" in blocks[2]["text"]
    assert "| 1 | 信息化平台 |" in blocks[2]["text"]
    assert blocks[3]["text"] == "预算：100万"


def test_extract_blocks_falls_back_without_body() -> None:
    no_body = {
        k: v for k, v in SAMPLE_DOCLING_JSON.items() if k != "body"
    }
    blocks = extract_blocks(no_body)
    assert [b["block_type"] for b in blocks] == [
        "title",
        "paragraph",
        "paragraph",
        "table",
    ]
    assert blocks[3]["block_type"] == "table"


def test_run_parse_success_persists_blocks_and_status(
    session_factory: async_sessionmaker,
) -> None:
    async def run() -> None:
        async with session_factory() as session:
            version = DocumentVersion(
                document_id=1,
                version_number=1,
                object_key="raw/x/a.pdf",
                file_name="a.pdf",
                content_type="application/pdf",
                size_bytes=1,
                sha256="0" * 64,
                parse_status=ParseStatus.PARSING,
            )
            session.add(version)
            await session.flush()
            await session.commit()
            version_id = version.id

        class FakeDoc:
            def export_to_markdown(self) -> str:
                return "# 第一章 招标公告"

            def export_to_dict(self) -> dict:
                return SAMPLE_DOCLING_JSON

        class FakeJob:
            task_id = "task-1"

            async def result(self, timeout=None):
                return type(
                    "R",
                    (),
                    {"status": "success", "errors": [], "document": FakeDoc()},
                )()

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            async def submit(self, source, options=None, target=None):
                return FakeJob()

        with (
            patch("app.services.parsing.async_session_factory", session_factory),
            patch(
                "app.services.parsing.fget_object",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.services.parsing.put_object",
                new=AsyncMock(return_value=object()),
            ),
            patch("app.services.parsing.open_client", return_value=FakeClient()),
        ):
            await run_parse(version_id)

        async with session_factory() as session:
            version = await session.get(DocumentVersion, version_id)
            assert version.parse_status == ParseStatus.PARSED
            assert version.parsed_at is not None
            assert version.parsed_object_key is not None
            assert version.parsed_markdown_object_key is not None

            from sqlalchemy import select

            blocks = (await session.execute(select(DocumentBlock))).scalars().all()
            assert len(blocks) == 4

    asyncio.run(run())


def test_run_parse_failure_marks_failed(
    session_factory: async_sessionmaker,
) -> None:
    async def run() -> None:
        async with session_factory() as session:
            version = DocumentVersion(
                document_id=1,
                version_number=1,
                object_key="raw/x/b.pdf",
                file_name="b.pdf",
                content_type="application/pdf",
                size_bytes=1,
                sha256="0" * 64,
                parse_status=ParseStatus.PARSING,
            )
            session.add(version)
            await session.flush()
            await session.commit()
            version_id = version.id

        class Boom:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            async def submit(self, source, options=None, target=None):
                raise RuntimeError("server down")

        with (
            patch("app.services.parsing.async_session_factory", session_factory),
            patch(
                "app.services.parsing.fget_object",
                new=AsyncMock(return_value=None),
            ),
            patch("app.services.parsing.open_client", return_value=Boom()),
        ):
            await run_parse(version_id)

        async with session_factory() as session:
            version = await session.get(DocumentVersion, version_id)
            assert version.parse_status == ParseStatus.FAILED
            assert "server down" in version.parse_error

    asyncio.run(run())
