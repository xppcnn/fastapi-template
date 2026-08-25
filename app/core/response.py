from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse[T](BaseModel):
    data: T | None = None
    code: int = 200
    message: str = "ok"


def ok(data: Any = None, message: str = "ok") -> dict:
    return {"data": data, "code": 200, "message": message}


def fail(code: int, message: str, request_id: str) -> dict:
    return {"data": None, "code": code, "message": message, "request_id": request_id}
