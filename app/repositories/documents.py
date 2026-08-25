from datetime import datetime
from uuid import UUID

from sqlalchemy import false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import (
    DocType,
    Document,
    DocumentVersion,
    UploadSession,
    UploadSessionStatus,
)
from app.repositories.pagination import paginate
from app.schemas.document import DocumentListQuery, DocumentVersionListQuery


async def insert_document(
    session: AsyncSession,
    *,
    organization_id: int,
    created_by_id: int,
    name: str,
    doc_type: DocType,
    project_id: int,
) -> Document:
    document = Document(
        organization_id=organization_id,
        created_by_id=created_by_id,
        name=name,
        doc_type=doc_type,
        project_id=project_id,
    )
    session.add(document)
    await session.flush()
    return document


async def get_document_lists(
    session: AsyncSession,
    *,
    organization_id: int,
    query: DocumentListQuery,
    project_id: int,
) -> tuple[list[Document], int]:
    stmt = select(Document).where(
        Document.organization_id == organization_id,
        Document.project_id == project_id,
        Document.is_deleted == false(),
    )
    if query.doc_type is not None:
        stmt = stmt.where(Document.doc_type == query.doc_type)
    stmt = stmt.order_by(Document.id.desc())
    items, total = await paginate(
        session, stmt, page=query.page, page_size=query.page_size
    )
    return list(items), total


async def get_document_by_public_id(
    session: AsyncSession, *, organization_id: int, project_id: int, document_p_id: UUID
):
    stmt = select(Document).where(
        Document.organization_id == organization_id,
        Document.project_id == project_id,
        Document.public_id == document_p_id,
        Document.is_deleted == false(),
    )
    document = await session.execute(stmt)
    return document.scalar_one_or_none()


async def repo_delete_document(session: AsyncSession, *, document: Document):
    document.is_deleted = True
    document.active_version = None
    await session.flush()


async def insert_upload_session(
    session: AsyncSession,
    *,
    document_id: int,
    file_name: str,
    content_type: str,
    size_bytes: int,
    object_key: str,
    expires_at: datetime,
) -> UploadSession:
    upload = UploadSession(
        document_id=document_id,
        object_key=object_key,
        file_name=file_name,
        content_type=content_type,
        size_bytes=size_bytes,
        expires_at=expires_at,
    )
    session.add(upload)
    await session.flush()
    return upload


async def get_upload_session_by_public_id(
    session: AsyncSession, *, document_id: int, public_id: UUID
) -> UploadSession | None:
    stmt = select(UploadSession).where(
        UploadSession.document_id == document_id,
        UploadSession.public_id == public_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_version_by_id(
    session: AsyncSession, *, version_id: int
) -> DocumentVersion | None:
    return await session.get(DocumentVersion, version_id)


async def get_idempotent_version(
    session: AsyncSession, *, document_id: int, idempotency_key: str
) -> DocumentVersion | None:
    stmt = (
        select(DocumentVersion)
        .join(
            UploadSession,
            UploadSession.completed_version_id == DocumentVersion.id,
        )
        .where(
            UploadSession.document_id == document_id,
            UploadSession.idempotency_key == idempotency_key,
        )
        .order_by(DocumentVersion.id.desc())
    )
    return (await session.execute(stmt)).scalars().first()


async def next_version_number(session: AsyncSession, *, document_id: int) -> int:
    max_number = await session.scalar(
        select(func.max(DocumentVersion.version_number)).where(
            DocumentVersion.document_id == document_id
        )
    )
    return (max_number or 0) + 1


async def insert_document_version(
    session: AsyncSession,
    *,
    document_id: int,
    version_number: int,
    object_key: str,
    file_name: str,
    content_type: str,
    size_bytes: int,
    sha256: str,
) -> DocumentVersion:
    version = DocumentVersion(
        document_id=document_id,
        version_number=version_number,
        object_key=object_key,
        file_name=file_name,
        content_type=content_type,
        size_bytes=size_bytes,
        sha256=sha256,
    )
    session.add(version)
    await session.flush()
    return version


async def complete_upload_session(
    session: AsyncSession,
    *,
    upload: UploadSession,
    completed_version_id: int,
    idempotency_key: str | None,
) -> None:
    upload.status = UploadSessionStatus.COMPLETED
    upload.completed_version_id = completed_version_id
    upload.idempotency_key = idempotency_key
    await session.flush()


async def document_version_list(
    session: AsyncSession, *, document: Document, query: DocumentVersionListQuery
):
    stmt = (
        select(DocumentVersion)
        .where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.is_deleted == false(),
        )
        .order_by(DocumentVersion.version_number.desc())
    )
    items, total = await paginate(
        session=session, stmt=stmt, page=query.page, page_size=query.page_size
    )
    return list(items), total


async def get_document_version(
    session: AsyncSession, *, document: Document, version_id: UUID
):
    stmt = select(DocumentVersion).where(
        DocumentVersion.public_id == version_id,
        DocumentVersion.document_id == document.id,
        DocumentVersion.is_deleted == false(),
    )
    version = (await session.execute(stmt)).scalar_one_or_none()
    return version
