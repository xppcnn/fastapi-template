from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.project import Project
from app.repositories import projects as project_repository
from app.repositories.projects import get_by_public_id, insert_project
from app.schemas.project import (
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdateRequest,
)


async def create_project(
    session: AsyncSession,
    *,
    payload: ProjectCreateRequest,
    organization_id: int,
    created_by_id: int,
) -> Project:
    return await insert_project(
        session,
        organization_id=organization_id,
        created_by_id=created_by_id,
        name=payload.name,
        description=payload.description,
    )


async def get_project(
    session: AsyncSession, *, organization_id: int, public_id: UUID
) -> Project:
    project = await get_by_public_id(
        session, organization_id=organization_id, public_id=public_id
    )
    if project is None:
        raise AppError("Project not found", code=404)
    return project


async def list_projects(
    session: AsyncSession,
    *,
    organization_id: int,
    page: int,
    page_size: int,
) -> ProjectListResponse:
    items, total = await project_repository.list_projects(
        session,
        organization_id=organization_id,
        page=page,
        page_size=page_size,
    )
    return ProjectListResponse(
        items=[ProjectResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


async def delete_project(
    session: AsyncSession, *, organization_id: int, public_id: UUID
) -> None:
    result = await session.execute(
        select(Project).where(
            Project.organization_id == organization_id,
            Project.public_id == public_id,
        )
    )
    project = result.scalar_one_or_none()

    if project is None:
        raise AppError("Project not found", code=404)
    if project.is_deleted:
        raise AppError("Project already deleted", code=400)
    project.is_deleted = True
    await session.flush()

async def update_project(
    session: AsyncSession, *, organization_id: int, public_id: UUID, payload: ProjectUpdateRequest
) -> Project:
    project = await get_project(
        session=session, organization_id=organization_id, public_id=public_id
    )
    return await project_repository.update_project(
        session, project=project, payload=payload
    )
    