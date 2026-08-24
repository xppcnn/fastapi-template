import asyncio
from collections.abc import Generator

import pytest
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.exceptions import AppError
from app.models import Base
from app.repositories.users import get_user_by_email
from app.schemas.auth import RegisterRequest
from app.services.auth import register_user


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


def test_register_user_commits_and_persists(
    session_factory: async_sessionmaker,
) -> None:
    async def run() -> None:
        async with session_factory() as session:
            result = await register_user(
                session,
                payload=RegisterRequest(
                    email="Owner@Example.COM",
                    password="correct horse battery staple",
                ),
            )
            await session.commit()
        async with session_factory() as verify_session:
            user = await get_user_by_email(
                verify_session, email="owner@example.com"
            )
            assert user is not None
            assert user.public_id == result.user.public_id

    asyncio.run(run())


def test_register_user_rolls_back_on_duplicate_email(
    session_factory: async_sessionmaker,
) -> None:
    async def run() -> None:
        async with session_factory() as session:
            await register_user(
                session,
                payload=RegisterRequest(
                    email="owner@example.com",
                    password="correct horse battery staple",
                ),
            )
            await session.commit()
            with pytest.raises(AppError) as exc_info:
                await register_user(
                    session,
                    payload=RegisterRequest(
                        email=" Owner@Example.COM ",
                        password="another correct password",
                    ),
                )
            assert exc_info.value.code == 409

    asyncio.run(run())
