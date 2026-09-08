from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
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
    silo_endpoint: str = "localhost:9000"
    silo_root_user: str = "silo-admin"
    silo_root_password: str = "silo-admin"
    silo_bucket: str = "documents"
    silo_secure: bool = False
    docling_serve_base_url: str = "http://localhost:5001"
    docling_serve_api_key: str = ""
    docling_parse_timeout_minutes: int = 15
    docling_do_ocr: bool = True
    docling_table_mode: Literal["fast", "accurate"] = "fast"
    redis_url: str = "redis://localhost:6379/0"
    celery_concurrency: int = 2
    worker_max_tasks_per_child: int = 100
    parsing_poll_interval_seconds: int = 5
    parsing_reconcile_limit: int = 50
    rule_extraction_timeout_minutes: int = Field(default=30, ge=1)
    rule_extraction_queue_timeout_minutes: int = Field(default=5, ge=1)
    rule_extraction_reconcile_interval_seconds: int = Field(default=30, ge=1)
    llm_base_url: str = "https://api.example.com/v1"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "configure-me"
    llm_context_length_limit: int = 128_000

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def json_logs(self) -> bool:
        if self.log_format != "auto":
            return self.log_format == "json"
        return self.environment.lower() not in {"development", "local", "test"}

    @property
    def sync_database_url(self) -> str:
        """Celery worker 用同步驱动，URL 由 async URL 推导，不留第二条手填配置。"""
        return self.database_url.replace(
            "postgresql+asyncpg://", "postgresql+psycopg://"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
