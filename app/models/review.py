from __future__ import annotations

from enum import StrEnum

from sqlalchemy import JSON, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.document import DocumentVersion


class ReviewRunStatus(StrEnum):
    READY = "ready"


class ReviewRun(Base):
    """固定输入的审核过程；ready 表示输入已冻结，尚未执行逐项审核。"""

    __tablename__ = "review_runs"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "idempotency_key", name="uq_review_runs_project_idempotency"
        ),
    )

    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    tender_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT")
    )
    bid_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT")
    )
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[ReviewRunStatus] = mapped_column(
        Enum(
            ReviewRunStatus,
            native_enum=False,
            values_callable=lambda e: [v.value for v in e],
        ),
        default=ReviewRunStatus.READY,
        server_default="ready",
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    rule_count: Mapped[int] = mapped_column(Integer)

    tender_version: Mapped[DocumentVersion] = relationship(
        foreign_keys=[tender_version_id], lazy="raise"
    )
    bid_version: Mapped[DocumentVersion] = relationship(
        foreign_keys=[bid_version_id], lazy="raise"
    )
    rules: Mapped[list[ReviewRunRule]] = relationship(
        back_populates="run", order_by="ReviewRunRule.id", lazy="raise"
    )


class ReviewRunRule(Base):
    __tablename__ = "review_run_rules"
    __table_args__ = (
        UniqueConstraint("run_id", "source_rule_id", name="uq_review_run_rules_source"),
    )

    run_id: Mapped[int] = mapped_column(
        ForeignKey("review_runs.id", ondelete="CASCADE"), index=True
    )
    source_rule_id: Mapped[int] = mapped_column(
        ForeignKey("review_rules.id", ondelete="RESTRICT")
    )
    source_rule_version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    run: Mapped[ReviewRun] = relationship(back_populates="rules")
