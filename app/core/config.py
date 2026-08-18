from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "fastapi-study"
    environment: str = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_format: Literal["auto", "text", "json"] = "auto"
    jwt_secret_key: str = "development-only-change-me-at-least-32-bytes"
    jwt_algorithm: Literal["HS256"] = "HS256"
    access_token_expire_minutes: int = 24 * 60
    refresh_token_expire_days: int = 7
    refresh_cookie_name: str = "refresh_token"
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/fastapi_template"
    )

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def json_logs(self) -> bool:
        if self.log_format != "auto":
            return self.log_format == "json"
        return self.environment.lower() not in {"development", "local", "test"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
