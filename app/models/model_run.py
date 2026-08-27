from __future__ import annotations

from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.identity import Organization
from app.models.project import Project


class ModelRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ModelRun(Base):
    """一次模型调用尝试;相同 operation_id 的多条记录组成同一模型操作的重试链(attempt 递增)。

    规则提取发生在审核运行之前,因此 review_run_id 关联可选(此处仅预留列,Task 9 迁移再加 FK)。
    """

    __tablename__ = "model_runs"

    __table_args__ = (
        UniqueConstraint(
            "operation_id", "attempt", name="uq_model_runs_operation_attempt"
        ),
        Index("ix_model_runs_operation_id", "operation_id"),
    )

    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    review_run_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="预留:Task 9 审核运行表落地后加 FK"
    )

    operation_id: Mapped[str] = mapped_column(String(120), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    operation_name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[ModelRunStatus] = mapped_column(
        Enum(
            ModelRunStatus,
            name="model_run_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        default=ModelRunStatus.RUNNING,
        server_default=ModelRunStatus.RUNNING.value,
    )

    model: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model_input_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="规范化输入消息的 SHA-256,用于追溯"
    )
    raw_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    organization: Mapped[Organization] = relationship()
    project: Mapped[Project] = relationship()
