from uuid import UUID

import structlog
from fastapi import APIRouter, Query, status

from app.api.dependencies import CurrentPrincipalDep
from app.core.database import DbSession
from app.core.response import ApiResponse, ok
from app.models.document import Document
from app.models.project import Project
from app.schemas.document import (
    CreateDocumentRequest,
    CreateDocumentResponse,
)
from app.schemas.project import (
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdateRequest,
)
from app.services.documents import create_document
from app.services.projects import (
    create_project,
    delete_project,
    get_project,
    list_projects,
    update_project,
)

router = APIRouter(prefix="/projects")
logger = structlog.get_logger(__name__)


def _project_response(project: Project) -> ProjectResponse:
    return ProjectResponse(
        public_id=project.public_id,
        name=project.name,
        description=project.description,
        status=project.status,
        version=project.version,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("/", response_model=ApiResponse[ProjectListResponse])
async def list(
    principal: CurrentPrincipalDep,
    session: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    result = await list_projects(
        session,
        organization_id=principal.organization_id,
        page=page,
        page_size=page_size,
    )
    return ok(
        ProjectListResponse(
            items=[_project_response(p) for p in result.items],
            total=result.total,
            page=result.page,
            page_size=result.page_size,
        )
    )


@router.get("/{project_id}", response_model=ApiResponse[ProjectResponse])
async def get(
    project_id: UUID,
    principal: CurrentPrincipalDep,
    session: DbSession,
) -> dict:
    project = await get_project(
        session,
        organization_id=principal.organization_id,
        public_id=project_id,
    )
    return ok(_project_response(project))


@router.post(
    "/",
    response_model=ApiResponse[ProjectResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create(
    payload: ProjectCreateRequest,
    principal: CurrentPrincipalDep,
    session: DbSession,
) -> dict:
    project = await create_project(
        session,
        payload=payload,
        organization_id=principal.organization_id,
        created_by_id=principal.user_id,
    )
    return ok(_project_response(project))


@router.delete("/{project_id}", response_model=ApiResponse[None])
async def delete(
    project_id: UUID, principal: CurrentPrincipalDep, session: DbSession
) -> dict:
    await delete_project(
        session,
        organization_id=principal.organization_id,
        public_id=project_id,
    )
    return ok(None)


@router.put("/{project_id}", response_model=ApiResponse[ProjectResponse])
async def update(
    project_id: UUID,
    payload: ProjectUpdateRequest,
    principal: CurrentPrincipalDep,
    session: DbSession,
) -> dict:
    project = await update_project(
        session,
        organization_id=principal.organization_id,
        public_id=project_id,
        payload=payload,
    )
    return ok(project)


def _document_response(
    document: Document, project_public_id: UUID
) -> CreateDocumentResponse:
    return CreateDocumentResponse(
        public_id=document.public_id,
        project_id=project_public_id,
        name=document.name,
        doc_type=document.doc_type,
        active_version=document.active_version_id,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


@router.post(
    "/{project_id}/documents",
    response_model=ApiResponse[CreateDocumentResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_documents(
    project_id: UUID,
    payload: CreateDocumentRequest,
    principal: CurrentPrincipalDep,
    session: DbSession,
) -> dict:
    document = await create_document(
        session,
        payload=payload,
        organization_id=principal.organization_id,
        created_by_id=principal.user_id,
        project_public_id=project_id,
    )
    return ok(_document_response(document, project_id))
