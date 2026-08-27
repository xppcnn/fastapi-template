from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.document import DocumentVersion
from app.models.identity import Organization
from app.models.project import Project


class RuleExtractionRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class RuleExtractBatchStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RuleExtractionRun(Base):
    """一次规则提取:按章节分批,按批 checkpoint,失败批次单独重跑。"""

    __tablename__ = "rule_extraction_runs"

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
    status: Mapped[RuleExtractionRunStatus] = mapped_column(
        Enum(
            RuleExtractionRunStatus,
            name="rule_extraction_run_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        default=RuleExtractionRunStatus.QUEUED,
        server_default=RuleExtractionRunStatus.QUEUED.value,
        nullable=False,
    )
    total_batches: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_batches: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_batches: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    organization: Mapped[Organization] = relationship()
    project: Mapped[Project] = relationship()
    tender_version: Mapped[DocumentVersion] = relationship()
    batches: Mapped[list[RuleExtractBatch]] = relationship(
        back_populates="run", passive_deletes=True
    )


class RuleExtractBatch(Base):
    """单批(单章)提取的 checkpoint;批次成功后才落 draft 规则。"""

    __tablename__ = "rule_extract_batches"

    __table_args__ = (
        UniqueConstraint(
            "run_id", "order_index", name="uq_rule_extract_batches_run_order"
        ),
    )

    run_id: Mapped[int] = mapped_column(
        ForeignKey("rule_extraction_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[RuleExtractBatchStatus] = mapped_column(
        Enum(
            RuleExtractBatchStatus,
            name="rule_extract_batch_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        default=RuleExtractBatchStatus.PENDING,
        server_default=RuleExtractBatchStatus.PENDING.value,
        nullable=False,
    )
    first_order_index: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="该批覆盖的 DocumentBlock order_index 范围起点"
    )
    last_order_index: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="该批覆盖的 DocumentBlock order_index 范围终点"
    )
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    run: Mapped[RuleExtractionRun] = relationship(back_populates="batches")
