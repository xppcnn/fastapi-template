from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Header, Query, status

from app.api.dependencies import CurrentPrincipalDep
from app.core.database import DbSession
from app.core.response import ApiResponse, ok
from app.models.document import Document
from app.models.project import Project
from app.schemas.document import (
    CompleteUploadRequest,
    CompleteUploadResponse,
    CreateDocumentRequest,
    CreateDocumentResponse,
    DocumentItemResponse,
    DocumentListQuery,
    DocumentListResponse,
    DocumentVersionResponse,
    UploadRequest,
    UploadResponse,
)
from app.schemas.project import (
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdateRequest,
)
from app.services.documents import (
    complete_upload as complete_upload_service,
)
from app.services.documents import (
    create_document,
    document_detail,
    document_lists,
)
from app.services.documents import (
    delete_document as delete_document_service,
)
from app.services.documents import (
    initiate_upload as initiate_upload_service,
)
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


@router.get("/{project_id}/documents", response_model=ApiResponse[DocumentListResponse])
async def document_list(
    project_id: UUID,
    principal: CurrentPrincipalDep,
    session: DbSession,
    query: Annotated[DocumentListQuery, Query()],
):
    result = await document_lists(
        session=session,
        project_public_id=project_id,
        organization_id=principal.organization_id,
        query=query,
    )
    return ok(result)


@router.get(
    "/{project_id}/documents/{document_id}",
    response_model=ApiResponse[DocumentItemResponse],
)
async def get_document_detail(
    project_id: UUID,
    document_id: UUID,
    principal: CurrentPrincipalDep,
    session: DbSession,
):
    result = await document_detail(
        session=session,
        project_public_id=project_id,
        organization_id=principal.organization_id,
        document_public_id=document_id,
    )
    return ok(result)


@router.delete("/{project_id}/documents/{document_id}")
async def delete_document(
    project_id: UUID,
    document_id: UUID,
    principal: CurrentPrincipalDep,
    session: DbSession,
):
    await delete_document_service(
        session=session,
        organization_id=principal.organization_id,
        project_public_id=project_id,
        document_public_id=document_id,
    )
    return ok()


@router.post(
    "/{project_id}/documents/{document_id}/versions/uploads",
    response_model=ApiResponse[UploadResponse],
    status_code=status.HTTP_201_CREATED,
)
async def initiate_upload(
    project_id: UUID,
    document_id: UUID,
    payload: UploadRequest,
    principal: CurrentPrincipalDep,
    session: DbSession,
):
    upload, upload_url = await initiate_upload_service(
        session=session,
        project_public_id=project_id,
        document_public_id=document_id,
        organization_id=principal.organization_id,
        payload=payload,
    )
    return ok(
        UploadResponse(
            upload_id=upload.public_id,
            object_key=upload.object_key,
            upload_url=upload_url,
            expires_at=upload.expires_at,
        )
    )


@router.post(
    "/{project_id}/documents/{document_id}/versions/uploads/{upload_id}/complete",
    response_model=ApiResponse[CompleteUploadResponse],
    status_code=status.HTTP_201_CREATED,
)
async def complete_upload(
    project_id: UUID,
    document_id: UUID,
    upload_id: UUID,
    payload: CompleteUploadRequest,
    principal: CurrentPrincipalDep,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(max_length=64)] = None,
):
    version, is_active = await complete_upload_service(
        session=session,
        project_public_id=project_id,
        document_public_id=document_id,
        upload_public_id=upload_id,
        organization_id=principal.organization_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return ok(
        CompleteUploadResponse(
            version=DocumentVersionResponse.model_validate(version),
            is_active=is_active,
        )
    )
