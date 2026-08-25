import asyncio
from collections.abc import Generator
from typing import ClassVar
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base
from app.models.document import DocumentVersion, ParseStatus
from app.services.parsing import (
    _resume_parse,
    find_stuck_parsing_versions,
)


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


def _make_version(session, *, parse_status, parse_job_id=None, version_number=1) -> int:
    version = DocumentVersion(
        document_id=1,
        version_number=version_number,
        object_key="raw/a.pdf",
        file_name="a.pdf",
        content_type="application/pdf",
        size_bytes=1,
        sha256="0" * 64,
        parse_status=parse_status,
        parse_job_id=parse_job_id,
    )
    session.add(version)
    return version


def test_find_stuck_parsing_versions(session_factory: async_sessionmaker) -> None:
    async def run() -> None:
        async with session_factory() as session:
            stuck = _make_version(
                session, parse_status=ParseStatus.PARSING, parse_job_id="t1"
            )
            also_stuck = _make_version(
                session,
                parse_status=ParseStatus.PARSING,
                parse_job_id=None,
                version_number=2,
            )
            done = _make_version(
                session, parse_status=ParseStatus.PARSED, version_number=3
            )
            await session.flush()
            await session.commit()

        async with session_factory() as session:
            ids = await find_stuck_parsing_versions(session)
        assert stuck.id in ids
        assert also_stuck.id in ids
        assert done.id not in ids

    asyncio.run(run())


def test_resume_parse_completes_stuck_version(
    session_factory: async_sessionmaker,
) -> None:
    """对账续跑:有 task_id 的版本通过轮询等到成功并落库。"""

    async def run() -> None:
        async with session_factory() as session:
            version = _make_version(
                session, parse_status=ParseStatus.PARSING, parse_job_id="t1"
            )
            await session.flush()
            await session.commit()
            version_id = version.id

        class FakeStatus:
            task_status = "success"

        class FakePayload:
            class Document:
                md_content = "# 已恢复"
                json_content: ClassVar[dict] = {
                    "texts": [
                        {
                            "text": "已恢复",
                            "label": "paragraph",
                            "prov": [{"page_no": 1}],
                        }
                    ]
                }

            document = Document()
            status = "success"
            errors = ()

        class FakeClient:
            _async_client = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            async def _poll_task_status(self, task_id, wait):
                return FakeStatus()

            async def _fetch_convert_result_payload(
                self, task_id, last_status=None, async_client=None
            ):
                return FakePayload()

        with (
            patch("app.services.parsing.async_session_factory", session_factory),
            patch(
                "app.services.parsing.put_object",
                new=AsyncMock(return_value=object()),
            ),
            patch("app.services.parsing.open_client", return_value=FakeClient()),
        ):
            await _resume_parse(version_id)

        async with session_factory() as session:
            version = await session.get(DocumentVersion, version_id)
            assert version.parse_status == ParseStatus.PARSED
            assert version.parsed_at is not None

    asyncio.run(run())


def test_resume_parse_marks_failed_when_task_id_missing(
    session_factory: async_sessionmaker,
) -> None:
    async def run() -> None:
        async with session_factory() as session:
            version = _make_version(
                session, parse_status=ParseStatus.PARSING, parse_job_id=None
            )
            await session.flush()
            await session.commit()
            version_id = version.id

        with (
            patch("app.services.parsing.async_session_factory", session_factory),
        ):
            await _resume_parse(version_id)

        async with session_factory() as session:
            version = await session.get(DocumentVersion, version_id)
            assert version.parse_status == ParseStatus.FAILED
            assert "重新触发" in version.parse_error

    asyncio.run(run())
