import asyncio
from collections.abc import Generator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base
from app.models.document import DocumentVersion, ParseStatus
from app.repositories.documents import claim_version_for_parsing


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


def test_claim_sets_parsing_and_guards_reentry(
    session_factory: async_sessionmaker,
) -> None:
    async def run() -> None:
        async with session_factory() as session:
            version = DocumentVersion(
                document_id=1,
                version_number=1,
                object_key="k",
                file_name="a.pdf",
                content_type="application/pdf",
                size_bytes=1,
                sha256="0" * 64,
            )
            session.add(version)
            await session.flush()

            claimed = await claim_version_for_parsing(session, version_id=version.id)
            assert claimed is True
            await session.refresh(version)
            assert version.parse_status == ParseStatus.PARSING
            assert version.parsing_started_at is not None

            again = await claim_version_for_parsing(session, version_id=version.id)
            assert again is False

    asyncio.run(run())