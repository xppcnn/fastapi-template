from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.review_rule import EvaluationMethod, RuleType, ScoringMethod


class ExistsCondition(BaseModel):
    operator: Literal["exists"] = "exists"
    field: str = Field(min_length=1)


class CompareCondition(BaseModel):
    operator: Literal["equals", "gte", "lte"]
    field: str = Field(min_length=1)
    value: str | float | int


class ContainsAllCondition(BaseModel):
    operator: Literal["contains_all"]
    field: str = Field(min_length=1)
    values: list[str] = Field(min_length=1)


Condition = Annotated[
    ExistsCondition | CompareCondition | ContainsAllCondition,
    Field(discriminator="operator"),
]


class ExtractedRule(BaseModel):
    """模型单条规则输出;source_segment_ids 只能引用真实片段 UUID(程序回查并校验页码)。"""

    rule_type: RuleType
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1)
    evaluation_method: EvaluationMethod = EvaluationMethod.SEMANTIC
    condition: Condition | None = None
    scoring_method: ScoringMethod = ScoringMethod.NONE
    max_score: float | None = Field(default=None, ge=0, le=1000)
    evaluation_criterion: str | None = Field(default=None, max_length=2000)
    source_segment_ids: list[UUID] = Field(min_length=1)
    explicitly_stated: bool = True


class BatchExtractionResult(BaseModel):
    batch_order: int = Field(ge=0)
    mentioned: bool = True
    rules: list[ExtractedRule] = []
