from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.review import ReviewRunStatus


class ReviewCreateRequest(BaseModel):
    tender_version_id: UUID
    bid_version_id: UUID


class ReviewRunRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    public_id: UUID
    source_rule_version: int
    snapshot: dict


class ReviewSummary(BaseModel):
    public_id: UUID
    status: ReviewRunStatus
    tender_version_id: UUID
    bid_version_id: UUID
    snapshot_hash: str
    rule_count: int
    created_at: datetime


class ReviewDetail(ReviewSummary):
    rules: list[ReviewRunRuleResponse]


class ReviewListResponse(BaseModel):
    items: list[ReviewSummary]
    total: int
    page: int
    page_size: int


class EvidenceCandidate(BaseModel):
    segment_id: UUID
    document_version_id: UUID
    page_no: int | None
    text: str
    score: float = Field(ge=0)


class EvidenceCandidatesResponse(BaseModel):
    retrieval_version: str = "keyword-v1"
    items: list[EvidenceCandidate]
