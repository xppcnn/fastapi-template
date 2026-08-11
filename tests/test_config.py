import os

from app.core.config import Settings, get_settings


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


def test_logging_defaults_to_info_and_text_in_development(monkeypatch) -> None:
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    settings = Settings(_env_file=None)

    assert settings.log_level == "INFO"
    assert settings.log_format == "auto"
    assert settings.json_logs is False


def test_auto_logging_format_uses_json_in_production(monkeypatch) -> None:
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    settings = Settings(environment="production", _env_file=None)

    assert settings.json_logs is True


def test_logging_environment_overrides(monkeypatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LOG_FORMAT", "json")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.log_level == "DEBUG"
        assert settings.json_logs is True
    finally:
        get_settings.cache_clear()


def test_default_development_jwt_secret_is_at_least_32_bytes(monkeypatch) -> None:
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)

    settings = Settings(_env_file=None)

    assert len(settings.jwt_secret_key.encode()) >= 32
