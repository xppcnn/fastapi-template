import asyncio
from datetime import timedelta
from functools import lru_cache
from typing import Any

from minio import Minio
from minio.helpers import ObjectWriteResult

from app.core.config import get_settings


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


def presigned_get_url(
    object_key: str, expires: timedelta = timedelta(hours=1)
) -> str:
    return get_client().presigned_get_object(_bucket(), object_key, expires=expires)