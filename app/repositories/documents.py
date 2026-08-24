from uuid import UUID

from sqlalchemy import false, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import DocType, Document
from app.repositories.pagination import paginate
from app.schemas.document import DocumentListQuery


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
