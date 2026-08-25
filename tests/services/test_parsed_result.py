import asyncio
from collections.abc import Generator
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.exceptions import AppError
from app.models import Base
from app.models.document import DocumentVersion, ParseStatus
from app.services.documents import parsed_result_urls


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


def test_parsed_result_returns_presigned_urls(
    session_factory: async_sessionmaker,
) -> None:
    async def run() -> None:
        async with session_factory() as session:
            version = DocumentVersion(
                document_id=1,
                version_number=1,
                object_key="raw/a.pdf",
                file_name="a.pdf",
                content_type="application/pdf",
                size_bytes=1,
                sha256="0" * 64,
                parse_status=ParseStatus.PARSED,
                parsed_markdown_object_key="parsed/x/parsed.md",
                parsed_object_key="parsed/x/parsed.json",
            )
            session.add(version)
            await session.flush()
            await session.commit()

            with patch(
                "app.services.documents.presigned_get_url",
                side_effect=lambda key: f"http://minio/{key}?sig=abc",
            ):
                md_url, json_url = await parsed_result_urls(
                    session, version_id=version.id
                )

        assert md_url == "http://minio/parsed/x/parsed.md?sig=abc"
        assert json_url == "http://minio/parsed/x/parsed.json?sig=abc"

    asyncio.run(run())


def test_parsed_result_unparsed_raises(session_factory: async_sessionmaker) -> None:
    async def run() -> None:
        async with session_factory() as session:
            version = DocumentVersion(
                document_id=1,
                version_number=1,
                object_key="raw/a.pdf",
                file_name="a.pdf",
                content_type="application/pdf",
                size_bytes=1,
                sha256="0" * 64,
                parse_status=ParseStatus.UPLOADED,
            )
            session.add(version)
            await session.flush()
            await session.commit()
            try:
                await parsed_result_urls(session, version_id=version.id)
            except AppError as exc:
                assert exc.code == 400
                return
            raise AssertionError("expected AppError 400")

    asyncio.run(run())