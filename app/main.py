from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.database import engine
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestIDMiddleware
from app.tasks.parsing import parsing_reconcile

logger = structlog.get_logger(__name__)

settings = get_settings()
configure_logging(settings.log_level, json_output=settings.json_logs)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        parsing_reconcile.delay()
    except Exception:  # redis 不可达不阻塞启动;卡住的版本由 beat 对账兜底
        logger.exception("reconcile_enqueue_failed")
    yield
    await engine.dispose()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(RequestIDMiddleware)
register_exception_handlers(app)
app.include_router(api_router, prefix=settings.api_v1_prefix)
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent.parent / "static"),
    name="static",
)
