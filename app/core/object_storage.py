import asyncio
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any
from uuid import uuid4

from minio import Minio
from minio.error import S3Error
from minio.helpers import ObjectWriteResult

from app.core.config import get_settings
from app.core.exceptions import AppError


def _bucket() -> str:
    return get_settings().silo_bucket


@lru_cache
def get_client() -> Minio:
    settings = get_settings()
    return Minio(
        settings.silo_endpoint,
        access_key=settings.silo_root_user,
        secret_key=settings.silo_root_password,
        secure=settings.silo_secure,
    )


def ensure_bucket() -> None:
    """Create the default bucket if missing. Idempotent."""
    client = get_client()
    if not client.bucket_exists(_bucket()):
        client.make_bucket(_bucket())


async def put_object(
    object_key: str,
    data: Any,
    length: int,
    *,
    content_type: str = "application/octet-stream",
) -> ObjectWriteResult:
    return await asyncio.to_thread(
        lambda: get_client().put_object(
            _bucket(), object_key, data, length, content_type=content_type
        )
    )


async def get_object(object_key: str) -> Any:
    """Return the object response; caller must read and release_conn()."""
    return await asyncio.to_thread(get_client().get_object, _bucket(), object_key)


async def fget_object(object_key: str, file_path: str) -> None:
    await asyncio.to_thread(get_client().fget_object, _bucket(), object_key, file_path)


async def remove_object(object_key: str) -> None:
    await asyncio.to_thread(get_client().remove_object, _bucket(), object_key)


def stat_object(object_key: str) -> Any:
    """Short HEAD call; wrap with asyncio.to_thread in hot paths if needed."""
    return get_client().stat_object(_bucket(), object_key)


def presigned_put_url(
    object_key: str, expires: timedelta = timedelta(minutes=15)
) -> str:
    return get_client().presigned_put_object(_bucket(), object_key, expires=expires)


def presigned_get_url(object_key: str, expires: timedelta = timedelta(hours=1)) -> str:
    return get_client().presigned_get_object(_bucket(), object_key, expires=expires)


def utcnow_naive() -> datetime:
    """UTC now without tzinfo, matching DB DateTime columns."""
    return datetime.now(UTC).replace(tzinfo=None)


def generate_object_key(prefix: str, file_name: str) -> str:
    """Build an object key: "<prefix>/<8-hex-random>/<file_name>".

    The random middle segment keeps keys unpredictable; final segment
    is the original file name for readability.
    """
    return f"{prefix.rstrip('/')}/{uuid4().hex[:8]}/{file_name}"


def create_presigned_upload(
    object_key: str, *, expires: timedelta
) -> tuple[str, datetime]:
    """Return (presigned PUT URL, naive-UTC expiry) for an object upload."""
    url = get_client().presigned_put_object(_bucket(), object_key, expires=expires)
    return url, utcnow_naive() + expires


async def verify_object_size(object_key: str, expected_size: int) -> None:
    """HEAD the object; raise AppError(400) if missing or size mismatch."""
    try:
        stat = await asyncio.to_thread(stat_object, object_key)
    except S3Error as exc:
        raise AppError("对象存储中对象不存在", code=400) from exc
    if stat.size != expected_size:
        raise AppError("对象大小与声明不一致", code=400)
