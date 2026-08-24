
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

from app.models.document import DocType


class DocumentListQuery(BaseModel):
    page: Annotated[int, Query(ge=1)] = 1
    page_size: Annotated[int, Query(ge=1, le=100)] = 20
    doc_type: DocType | None = None


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


class DocumentItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: UUID
    name: str
    doc_type: DocType
    active_version: int | None = Field(
        default=None, validation_alias="active_version_id"
    )
    created_at: datetime
    updated_at: datetime


class DocumentListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[DocumentItemResponse]
    total: int
    page: int
    page_size: int