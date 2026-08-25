from uuid import UUID

from sqlalchemy import false, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.repositories.pagination import paginate
from app.schemas.project import ProjectUpdateRequest


async def insert_project(
    session: AsyncSession,
    *,
    organization_id: int,
    created_by_id: int,
    name: str,
    description: str | None,
) -> Project:
    project = Project(
        organization_id=organization_id,
        created_by_id=created_by_id,
        name=name,
        description=description,
    )
    session.add(project)
    await session.flush()
    return project


async def get_by_public_id(
    session: AsyncSession, *, organization_id: int, public_id: UUID
) -> Project | None:
    result = await session.execute(
        select(Project).where(
            Project.organization_id == organization_id,
            Project.public_id == public_id,
            Project.is_deleted == false(),
        )
    )
    return result.scalar_one_or_none()


async def list_projects(
    session: AsyncSession,
    *,
    organization_id: int,
    page: int,
    page_size: int,
) -> tuple[list[Project], int]:
    stmt = (
        select(Project)
        .where(
            Project.organization_id == organization_id,
            Project.is_deleted == false(),
        )
        .order_by(Project.id.desc())
    )
    items, total = await paginate(session, stmt, page=page, page_size=page_size)
    return list(items), total


async def update_project(
    session: AsyncSession, *, project: Project, payload: ProjectUpdateRequest
) -> Project:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    project.version += 1
    await session.flush()
    await session.refresh(project)
    return project
