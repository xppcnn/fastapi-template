from fastapi import APIRouter
from pydantic import BaseModel

from app.core.exceptions import AppError
from app.core.response import ok

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return ok({"status": "ok"})


class EchoRequest(BaseModel):
    name: str


@router.post("/echo")
def echo(body: EchoRequest) -> dict:
    return ok({"name": body.name})


@router.get("/test-error")
def test_error() -> dict:
    raise AppError("演示冲突", code=409)
