from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.document import Document
from app.repositories.documents import insert_document
from app.repositories.projects import get_by_public_id
from app.schemas.document import CreateDocumentRequest


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
