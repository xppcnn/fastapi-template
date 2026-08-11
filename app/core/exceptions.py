from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.response import fail

logger = structlog.get_logger(__name__)


class AppError(Exception):
    def __init__(self, message: str, code: int = 500) -> None:
        self.message = message
        self.code = code
        super().__init__(message)


def _error_body(code: int, message: str, request: Request) -> dict[str, Any]:
    return fail(code, message, request.state.request_id)


def _error_response(code: int, message: str, request: Request) -> JSONResponse:
    return JSONResponse(
        status_code=code,
        content=_error_body(code, message, request),
        headers={"X-Request-ID": request.state.request_id},
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return _error_response(exc.code, exc.message, request)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = [
            f"{'.'.join(str(loc) for loc in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        ]
        message = "; ".join(errors[:2]) or "Request validation failed"
        return _error_response(422, message, request)

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return _error_response(exc.status_code, str(exc.detail), request)

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_error",
            message="Unhandled error",
            exc_info=exc,
            request_id=request.state.request_id,
        )
        return _error_response(500, "Internal Server Error", request)
