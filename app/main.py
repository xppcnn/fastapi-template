from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.database import engine
from app.core.exceptions import register_exception_handlers
from app.core.middleware import RequestIDMiddleware

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(RequestIDMiddleware)
register_exception_handlers(app)
app.include_router(api_router, prefix=settings.api_v1_prefix)
