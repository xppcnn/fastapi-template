from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.core.object_storage import (
    create_presigned_upload,
    generate_object_key,
    presigned_get_url,
    utcnow_naive,
    verify_object_size,
)
from app.models.document import (
    Document,
    DocumentVersion,
    ParseStatus,
    UploadSession,
    UploadSessionStatus,
)
from app.repositories.documents import (
    claim_version_for_parsing,
    complete_upload_session,
    document_version_list,
    get_document_by_public_id,
    get_document_lists,
    get_document_version,
    get_idempotent_version,
    get_upload_session_by_public_id,
    get_version_by_id,
    insert_document,
    insert_document_version,
    insert_upload_session,
    next_version_number,
    repo_delete_document,
)
from app.repositories.projects import get_by_public_id
from app.schemas.document import (
    CompleteUploadRequest,
    CreateDocumentRequest,
    DocumentItemResponse,
    DocumentListQuery,
    DocumentListResponse,
    DocumentVersionListItem,
    DocumentVersionListQuery,
    DocumentVersionListResponse,
    ParsedResultResponse,
    UploadRequest,
)
from app.services.parsing import spawn_parse

UPLOAD_URL_EXPIRE_MINUTES = 10


async def create_document(
    session: AsyncSession,
    *,
    payload: CreateDocumentRequest,
    organization_id: int,
    created_by_id: int,
    project_public_id: UUID,
) -> Document:
    project = await get_by_public_id(
        session, organization_id=organization_id, public_id=project_public_id
    )
    if project is None:
        raise AppError("Project not found", code=404)

    document = await session.scalar(
        select(Document).where(
            Document.project_id == project.id,
            Document.name == payload.name,
            Document.doc_type == payload.doc_type,
            Document.deleted_at.is_(None),
        )
    )
    if document:
        raise AppError("名称不得相同", code=409)
    return await insert_document(
        session,
        organization_id=organization_id,
        created_by_id=created_by_id,
        name=payload.name,
        doc_type=payload.doc_type,
        project_id=project.id,
    )


async def document_lists(
    session: AsyncSession,
    *,
    query: DocumentListQuery,
    organization_id: int,
    project_public_id: UUID,
) -> DocumentListResponse:
    project = await get_by_public_id(
        session, organization_id=organization_id, public_id=project_public_id
    )
    if project is None:
        raise AppError("Project not found", code=404)

    items, total = await get_document_lists(
        session, organization_id=organization_id, project_id=project.id, query=query
    )
    return DocumentListResponse(
        items=[DocumentItemResponse.model_validate(item) for item in items],
        total=total,
        page=query.page,
        page_size=query.page_size,
    )


async def document_detail(
    session: AsyncSession,
    *,
    project_public_id: UUID,
    document_public_id: UUID,
    organization_id: int,
):
    project = await get_by_public_id(
        session, organization_id=organization_id, public_id=project_public_id
    )
    if project is None:
        raise AppError("Project not found", code=404)
    document = await get_document_by_public_id(
        session=session,
        project_id=project.id,
        document_p_id=document_public_id,
        organization_id=organization_id,
    )
    if document is None:
        raise AppError("文档不存在", 404)
    return document


async def delete_document(
    session: AsyncSession,
    *,
    project_public_id: UUID,
    document_public_id: UUID,
    organization_id: int,
):
    project = await get_by_public_id(
        session, organization_id=organization_id, public_id=project_public_id
    )
    if project is None:
        raise AppError("Project not found", code=404)
    document = await get_document_by_public_id(
        session=session,
        project_id=project.id,
        document_p_id=document_public_id,
        organization_id=organization_id,
    )
    if document is None:
        raise AppError("文档不存在", 404)
    await repo_delete_document(session=session, document=document)


async def _require_document(
    session: AsyncSession,
    *,
    organization_id: int,
    project_public_id: UUID,
    document_public_id: UUID,
) -> Document:
    project = await get_by_public_id(
        session, organization_id=organization_id, public_id=project_public_id
    )
    if project is None:
        raise AppError("项目不存在", code=404)
    document = await get_document_by_public_id(
        session=session,
        project_id=project.id,
        document_p_id=document_public_id,
        organization_id=organization_id,
    )
    if document is None:
        raise AppError("文档不存在", code=404)
    return document


async def initiate_upload(
    session: AsyncSession,
    *,
    project_public_id: UUID,
    document_public_id: UUID,
    organization_id: int,
    payload: UploadRequest,
) -> tuple[UploadSession, str]:
    document = await _require_document(
        session,
        organization_id=organization_id,
        project_public_id=project_public_id,
        document_public_id=document_public_id,
    )
    object_key = generate_object_key(
        f"projects/{project_public_id}/documents/{document_public_id}/raws",
        payload.file_name,
    )
    upload_url, expires_at = create_presigned_upload(
        object_key, expires=timedelta(minutes=UPLOAD_URL_EXPIRE_MINUTES)
    )
    upload = await insert_upload_session(
        session,
        document_id=document.id,
        file_name=payload.file_name,
        content_type=payload.content_type,
        size_bytes=payload.size_bytes,
        object_key=object_key,
        expires_at=expires_at,
    )
    return upload, upload_url


async def complete_upload(
    session: AsyncSession,
    *,
    project_public_id: UUID,
    document_public_id: UUID,
    upload_public_id: UUID,
    organization_id: int,
    payload: CompleteUploadRequest,
    idempotency_key: str | None,
) -> tuple[DocumentVersion, bool]:
    document = await _require_document(
        session,
        organization_id=organization_id,
        project_public_id=project_public_id,
        document_public_id=document_public_id,
    )
    if idempotency_key is not None:
        existing = await get_idempotent_version(
            session, document_id=document.id, idempotency_key=idempotency_key
        )
        if existing is not None:
            return existing, True

    upload = await get_upload_session_by_public_id(
        session, document_id=document.id, public_id=upload_public_id
    )
    if upload is None:
        raise AppError("上传会话不存在", code=404)
    if upload.status == UploadSessionStatus.COMPLETED:
        raise AppError("上传会话已完成", code=400)
    if upload.expires_at < utcnow_naive():
        raise AppError("上传会话已过期", code=400)

    await verify_object_size(upload.object_key, upload.size_bytes)

    version_number = await next_version_number(session, document_id=document.id)
    version = await insert_document_version(
        session,
        document_id=document.id,
        version_number=version_number,
        object_key=upload.object_key,
        file_name=upload.file_name,
        content_type=upload.content_type,
        size_bytes=upload.size_bytes,
        sha256=payload.sha256,
    )
    await complete_upload_session(
        session,
        upload=upload,
        completed_version_id=version.id,
        idempotency_key=idempotency_key,
    )
    document.active_version_id = version.id
    await session.flush()
    return version, True


async def document_versions(
    session: AsyncSession,
    *,
    project_public_id: UUID,
    document_public_id: UUID,
    organization_id: int,
    query: DocumentVersionListQuery,
) -> DocumentVersionListResponse:
    document = await _require_document(
        session,
        organization_id=organization_id,
        project_public_id=project_public_id,
        document_public_id=document_public_id,
    )

    items, total = await document_version_list(
        session=session, document=document, query=query
    )
    return DocumentVersionListResponse(
        items=[
            DocumentVersionListItem.from_orm_model(version, document.active_version_id)
            for version in items
        ],
        total=total,
        page=query.page,
        page_size=query.page_size,
    )


async def parse_document(
    session: AsyncSession,
    *,
    project_public_id: UUID,
    document_public_id: UUID,
    version_public_id: UUID,
    organization_id: int,
):
    document = await _require_document(
        session,
        organization_id=organization_id,
        project_public_id=project_public_id,
        document_public_id=document_public_id,
    )
    version = await get_document_version(
        session, document=document, version_id=version_public_id
    )
    if version is None:
        raise AppError("当前版本不存在", code=404)
    claimed = await claim_version_for_parsing(session, version_id=version.id)
    if not claimed:
        raise AppError("当前版本正在解析", code=409)
    await session.commit()
    spawn_parse(version.id)
    return version


async def parsed_result_urls(session, *, version_id: int) -> tuple[str, str]:
    version = await get_version_by_id(session, version_id=version_id)
    if version is None:
        raise AppError("版本不存在", code=404)
    if version.parse_status != ParseStatus.PARSED:
        raise AppError("版本尚未解析完成", code=400)
    if not version.parsed_markdown_object_key or not version.parsed_object_key:
        raise AppError("解析产物缺失", code=409)
    md_url = presigned_get_url(version.parsed_markdown_object_key)
    json_url = presigned_get_url(version.parsed_object_key)
    return md_url, json_url


async def parsed_document_result(
    session,
    *,
    project_public_id: UUID,
    document_public_id: UUID,
    version_public_id: UUID,
    organization_id: int,
) -> ParsedResultResponse:
    document = await _require_document(
        session,
        organization_id=organization_id,
        project_public_id=project_public_id,
        document_public_id=document_public_id,
    )
    version = await get_document_version(
        session, document=document, version_id=version_public_id
    )
    if version is None:
        raise AppError("当前版本不存在", code=404)
    md_url, json_url = await parsed_result_urls(session, version_id=version.id)
    return ParsedResultResponse(
        markdown_url=md_url,
        json_url=json_url,
        parse_error=version.parse_error,
    )
