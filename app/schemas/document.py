from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.document import DocType, DocumentVersion, ParseStatus

UPLOAD_FILE_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


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


class UploadRequest(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    content_type: str
    size_bytes: int = Field(ge=1, le=50 * 1024 * 1024)

    @model_validator(mode="after")
    def content_type_matches_extension(self) -> "UploadRequest":
        ext = self.file_name.rsplit(".", 1)[-1].lower() if "." in self.file_name else ""
        expected = UPLOAD_FILE_TYPES.get(ext)
        if expected is None:
            raise ValueError("file extension must be pdf or docx")
        if self.content_type != expected:
            raise ValueError(f"content_type must be {expected} for .{ext} files")
        return self


class UploadResponse(BaseModel):
    upload_id: UUID
    object_key: str
    upload_url: str
    expires_at: datetime


class CompleteUploadRequest(BaseModel):
    etag: str = Field(min_length=1, max_length=300)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


class DocumentVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: UUID
    version_number: int
    file_name: str
    content_type: str
    size_bytes: int
    parse_status: ParseStatus
    created_at: datetime


class CompleteUploadResponse(BaseModel):
    version: DocumentVersionResponse
    is_active: bool


class DocumentVersionListQuery(BaseModel):
    page: Annotated[int, Query(ge=1)] = 1
    page_size: Annotated[int, Query(ge=1, le=100)] = 20


class DocumentVersionListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: UUID
    version_number: int
    file_name: str
    content_type: str
    size_bytes: int
    parse_status: ParseStatus
    is_active: bool = False
    created_at: datetime

    @classmethod
    def from_orm_model(
        cls,
        version: DocumentVersion,
        active_version_id: int | None,
    ) -> "DocumentVersionListItem":
        return cls.model_validate(version).model_copy(
            update={"is_active": version.id == active_version_id}
        )


class DocumentVersionListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    items: list[DocumentVersionListItem]
    total: int
    page: int
    page_size: int
