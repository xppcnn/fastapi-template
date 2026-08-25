from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.database import async_session_factory, engine
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestIDMiddleware
from app.services.parsing import find_stuck_parsing_versions, resume_parse

settings = get_settings()
configure_logging(settings.log_level, json_output=settings.json_logs)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with async_session_factory() as session:
        stuck = await find_stuck_parsing_versions(session)
    for version_id in stuck:
        resume_parse(version_id)
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
