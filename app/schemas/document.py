
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.document import DocType


class CreateDocumentRequest(BaseModel):
    name: str
    doc_type: DocType

class CreateDocumentResponse(BaseModel):
    public_id: UUID
    project_id: UUID
    name: str
    doc_type: DocType
    active_version: int | None = None
    created_at: datetime
    updated_at: datetime