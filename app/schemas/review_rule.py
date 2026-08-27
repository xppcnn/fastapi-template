from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

from app.models.review_rule import (
    EvaluationMethod,
    RuleStatus,
    RuleType,
    ScoringMethod,
)
from app.models.rule_extraction import RuleExtractionRunStatus
from app.schemas.llm import Condition


class RuleListQuery(BaseModel):
    page: Annotated[int, Query(ge=1)] = 1
    page_size: Annotated[int, Query(ge=1, le=100)] = 20
    rule_type: RuleType | None = None
    status: RuleStatus | None = None


class RuleExtractRequest(BaseModel):
    tender_version_id: UUID


class RuleCreateRequest(BaseModel):
    """人工补充规则:无 ModelRun 关联,归属当前招标版本。"""

    rule_type: RuleType
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=4000)
    evaluation_method: EvaluationMethod = EvaluationMethod.SEMANTIC
    condition: Condition | None = None
    scoring_method: ScoringMethod = ScoringMethod.NONE
    max_score: float | None = Field(default=None, ge=0, le=1000)
    evaluation_criterion: str | None = Field(default=None, max_length=2000)


class RuleUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, min_length=1, max_length=4000)
    rule_type: RuleType | None = None
    evaluation_method: EvaluationMethod | None = None
    condition: Condition | None = None
    scoring_method: ScoringMethod | None = None
    max_score: float | None = Field(default=None, ge=0, le=1000)
    evaluation_criterion: str | None = Field(default=None, max_length=2000)
    version: int = Field(ge=1, description="乐观锁版本号")


class RuleConfirmRequest(BaseModel):
    rule_ids: list[UUID] | None = None


class ReviewRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: UUID
    rule_type: RuleType
    title: str
    description: str
    evaluation_method: EvaluationMethod
    condition: dict | None
    scoring_method: ScoringMethod
    max_score: float | None
    evaluation_criterion: str | None
    status: RuleStatus
    needs_review: bool
    source_segment_ids: list[UUID]
    model_run_id: int | None
    prompt_version: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class RuleListResponse(BaseModel):
    items: list[ReviewRuleResponse]
    total: int
    page: int
    page_size: int


class ConfirmResponse(BaseModel):
    confirmed: int
    pending: int


class RuleExtractionRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: UUID
    status: RuleExtractionRunStatus
    total_batches: int
    completed_batches: int
    failed_batches: int
    error_message: str | None
    created_at: datetime
