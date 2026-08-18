from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def paginate(
    session: AsyncSession,
    stmt: Select,
    *,
    page: int,
    page_size: int,
) -> tuple[Sequence[Any], int]:
    total = await session.scalar(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    )
    result = await session.execute(stmt.offset((page - 1) * page_size).limit(page_size))
    return result.scalars().all(), total or 0
