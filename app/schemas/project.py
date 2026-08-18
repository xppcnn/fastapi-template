from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.project import ProjectStatus


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)


class ProjectResponse(BaseModel):
    public_id: UUID
    name: str
    description: str | None
    status: ProjectStatus
    version: int
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(BaseModel):
    items: list[ProjectResponse]
    total: int
    page: int
    page_size: int

class ProjectUpdateRequest(ProjectCreateRequest):
    status: ProjectStatus | None = None

    @field_validator("status")
    @classmethod
    def status_not_null(cls, value: ProjectStatus | None) -> ProjectStatus:
        if value is None:
            raise ValueError("status cannot be null")
        return value
