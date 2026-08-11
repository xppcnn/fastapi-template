import time
import uuid
from uuid import UUID

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import bind_request_id, reset_request_id

logger = structlog.get_logger("app.http")


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        header = request.headers.get("X-Request-ID")
        try:
            request_id = str(UUID(header)) if header else str(uuid.uuid4())
        except ValueError:
            request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        token = bind_request_id(request_id)
        started_at = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            logger.info(
                "http_request_completed",
                message="HTTP request completed",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_ms=duration_ms,
            )
            reset_request_id(token)
