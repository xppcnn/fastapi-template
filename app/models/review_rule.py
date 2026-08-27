from __future__ import annotations

from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.models.base import Base
from app.models.document import DocumentVersion
from app.models.identity import Organization, User
from app.models.model_run import ModelRun
from app.models.project import Project


class RuleType(StrEnum):
    DISQUALIFICATION = "disqualification"
    QUALIFICATION = "qualification"
    RESPONSE = "response"
    SCORING = "scoring"


class EvaluationMethod(StrEnum):
    DETERMINISTIC = "deterministic"
    SEMANTIC = "semantic"


class ScoringMethod(StrEnum):
    NONE = "none"
    OBJECTIVE = "objective"
    SUBJECTIVE = "subjective"


class RuleStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    IGNORED = "ignored"


class ReviewRule(Base):
    __tablename__ = "review_rules"

    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tender_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    rule_type: Mapped[RuleType] = mapped_column(
        Enum(
            RuleType,
            name="rule_type",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, comment="要求原文/判定口径")

    evaluation_method: Mapped[EvaluationMethod] = mapped_column(
        Enum(
            EvaluationMethod,
            name="evaluation_method",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        default=EvaluationMethod.SEMANTIC,
        server_default=EvaluationMethod.SEMANTIC.value,
        nullable=False,
    )
    condition: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="受控运算符条件(仅 deterministic 使用)"
    )
    scoring_method: Mapped[ScoringMethod] = mapped_column(
        Enum(
            ScoringMethod,
            name="scoring_method",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        default=ScoringMethod.NONE,
        server_default=ScoringMethod.NONE.value,
        nullable=False,
    )
    max_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    evaluation_criterion: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="评分项扣分判定口径(评价对象之外的评分依据)"
    )

    status: Mapped[RuleStatus] = mapped_column(
        Enum(
            RuleStatus,
            name="rule_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        default=RuleStatus.DRAFT,
        server_default=RuleStatus.DRAFT.value,
        nullable=False,
    )
    needs_review: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        comment="越界/不可溯源产物标记,确认前须人工复核",
    )

    source_segment_ids: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True,
        comment="合并来源(产生该 draft 的 DocumentBlock 公开 UUID 列表)",
    )
    dedup_key: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="类型+判定口径的规范化去重 key"
    )
    model_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_runs.id", ondelete="SET NULL"), nullable=True
    )
    extraction_run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="产生该 draft 的规则提取批次运行(人工规则为空)",
    )
    prompt_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    version: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        default=1,
        server_default="1",
        nullable=False,
    )

    organization: Mapped[Organization] = relationship()
    project: Mapped[Project] = relationship()
    tender_version: Mapped[DocumentVersion] = relationship()
    model_run: Mapped[ModelRun | None] = relationship()
    created_by: Mapped[User | None] = relationship()
