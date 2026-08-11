import asyncio
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.database import get_db
from app.main import app
from app.models import Base


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def create_schema() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    asyncio.run(create_schema())
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app, base_url="https://testserver") as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)
        asyncio.run(engine.dispose())


def test_register_creates_default_organization(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "Owner@Example.COM",
            "password": "correct horse battery staple",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "owner@example.com"
    assert body["organization"]["role"] == "owner"
    assert body["organization"]["status"] == "active"
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    set_cookie = response.headers["set-cookie"].lower()
    assert "refresh_token=" in set_cookie
    assert "httponly" in set_cookie
    assert "secure" in set_cookie
    assert "samesite=lax" in set_cookie


def test_register_rejects_duplicate_normalized_email(client: TestClient) -> None:
    payload = {
        "email": "owner@example.com",
        "password": "correct horse battery staple",
    }
    first_response = client.post("/api/v1/auth/register", json=payload)
    duplicate_response = client.post(
        "/api/v1/auth/register",
        json={**payload, "email": " Owner@Example.COM "},
    )

    assert first_response.status_code == 201
    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["message"] == "Email already registered"


def test_login_returns_access_token_and_refresh_cookie(client: TestClient) -> None:
    credentials = {
        "email": "owner@example.com",
        "password": "correct horse battery staple",
    }
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201

    response = client.post("/api/v1/auth/login", json=credentials)

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == credentials["email"]
    assert body["organization"]["role"] == "owner"
    set_cookie = response.headers["set-cookie"].lower()
    assert "refresh_token=" in set_cookie
    assert "httponly" in set_cookie
    assert "secure" in set_cookie
    assert "samesite=lax" in set_cookie


def test_login_rejects_incorrect_password(client: TestClient) -> None:
    credentials = {
        "email": "owner@example.com",
        "password": "correct horse battery staple",
    }
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201

    response = client.post(
        "/api/v1/auth/login",
        json={**credentials, "password": "this password is incorrect"},
    )

    assert response.status_code == 401
    assert response.json()["message"] == "Invalid email or password"


def test_refresh_rotates_cookie_and_returns_access_token(
    client: TestClient,
) -> None:
    registration_response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "owner@example.com",
            "password": "correct horse battery staple",
        },
    )
    assert registration_response.status_code == 201
    old_refresh_token = client.cookies["refresh_token"]

    response = client.post("/api/v1/auth/refresh")

    assert response.status_code == 200
    assert response.json()["access_token"]
    assert client.cookies["refresh_token"] != old_refresh_token


def test_me_returns_current_user_and_organization(client: TestClient) -> None:
    registration = client.post(
        "/api/v1/auth/register",
        json={
            "email": "owner@example.com",
            "password": "correct horse battery staple",
            "organization_name": "Acme Review",
        },
    )
    access_token = registration.json()["access_token"]

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "owner@example.com"
    assert response.json()["organization"]["name"] == "Acme Review"
    assert response.json()["organization"]["role"] == "owner"


def test_me_rejects_expired_access_token(client: TestClient) -> None:
    registration = client.post(
        "/api/v1/auth/register",
        json={
            "email": "owner@example.com",
            "password": "correct horse battery staple",
        },
    ).json()
    settings = get_settings()
    now = datetime.now(UTC)
    expired_token = jwt.encode(
        {
            "sub": registration["user"]["public_id"],
            "org": registration["organization"]["public_id"],
            "type": "access",
            "iat": now - timedelta(minutes=2),
            "exp": now - timedelta(minutes=1),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )

    assert response.status_code == 401
    assert response.json()["message"] == "Invalid or expired access token"


def test_refresh_requires_cookie(client: TestClient) -> None:
    response = client.post("/api/v1/auth/refresh")

    assert response.status_code == 401
    assert response.json()["message"] == "Invalid or expired refresh token"
