import os

from app.core.config import get_settings


def test_database_url_override(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pw@db.example:5432/other")
    get_settings.cache_clear()
    try:
        assert get_settings().database_url == (
            "postgresql+asyncpg://user:pw@db.example:5432/other"
        )
    finally:
        get_settings.cache_clear()


def test_database_url_default() -> None:
    if "DATABASE_URL" in os.environ:
        os.environ.pop("DATABASE_URL")
    get_settings.cache_clear()
    try:
        assert get_settings().database_url.startswith("postgresql+asyncpg://")
    finally:
        get_settings.cache_clear()
