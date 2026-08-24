from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.document import Document
from app.repositories.documents import (
    get_document_by_public_id,
    get_document_lists,
    insert_document,
    repo_delete_document,
)
from app.repositories.projects import get_by_public_id
from app.schemas.document import CreateDocumentRequest, DocumentListQuery


@dataclass(frozen=True)
class DocumentPage:
    items: list[Document]
    total: int
    page: int
    page_size: int


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
) -> DocumentPage:
    project = await get_by_public_id(
        session, organization_id=organization_id, public_id=project_public_id
    )
    if project is None:
        raise AppError("Project not found", code=404)

    items, total = await get_document_lists(
        session, organization_id=organization_id, project_id=project.id, query=query
    )
    return DocumentPage(
        items=items, total=total, page=query.page, page_size=query.page_size
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
