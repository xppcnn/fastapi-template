from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import DocType, Document


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
