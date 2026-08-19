from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.identity import Organization, User
from app.models.project import Project


class DocType(StrEnum):
    TENDER = "tender"
    BID = "bid"


class ParseStatus(StrEnum):
    UPLOADED = "uploaded"
    PARSING = "parsing"
    PARSED = "parsed"
    FAILED = "failed"


class UploadSessionStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    EXPIRED = "expired"


class Document(Base):
    __tablename__ = "documents"

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "name",
            "doc_type",
            "deleted_at",
            name="uq_documents_project_name_type_deleted_at",
        ),
    )

    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    doc_type: Mapped[DocType] = mapped_column(
        Enum(
            DocType,
            name="doc_type",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        default=DocType.TENDER,
        server_default=DocType.TENDER.value,
    )

    active_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    organization: Mapped[Organization] = relationship()
    project: Mapped[Project] = relationship()
    created_by: Mapped[User | None] = relationship()
    active_version: Mapped[DocumentVersion | None] = relationship(
        foreign_keys=[active_version_id]
    )
    versions: Mapped[list[DocumentVersion]] = relationship(
        back_populates="document",
        foreign_keys="DocumentVersion.document_id",
        passive_deletes=True,
    )
    upload_sessions: Mapped[list[UploadSession]] = relationship(
        back_populates="document", passive_deletes=True
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_document_version"),
    )

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    object_key: Mapped[str] = mapped_column(String(512), comment="对象存储中的对象键")

    file_name: Mapped[str] = mapped_column(String(255), comment="原始文件名")

    content_type: Mapped[str] = mapped_column(String(255), comment="MIME 类型")

    size_bytes: Mapped[int] = mapped_column(BigInteger)

    sha256: Mapped[str] = mapped_column(String(64))

    parse_status: Mapped[ParseStatus] = mapped_column(
        Enum(
            ParseStatus,
            name="parse_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        default=ParseStatus.UPLOADED,
        server_default=ParseStatus.UPLOADED.value,
    )

    parse_job_id: Mapped[int | None] = mapped_column(
        nullable=True, comment="解析任务 id；待 jobs 表创建后补充外键"
    )

    document: Mapped[Document] = relationship(
        back_populates="versions", foreign_keys=[document_id]
    )


class UploadSession(Base):
    __tablename__ = "upload_sessions"

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    object_key: Mapped[str] = mapped_column(String(512), comment="对象存储中的对象键")

    file_name: Mapped[str] = mapped_column(String(255), comment="原始文件名")

    content_type: Mapped[str] = mapped_column(String(255), comment="MIME 类型")

    size_bytes: Mapped[int] = mapped_column(BigInteger)

    status: Mapped[UploadSessionStatus] = mapped_column(
        Enum(
            UploadSessionStatus,
            name="upload_session_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        default=UploadSessionStatus.PENDING,
        server_default=UploadSessionStatus.PENDING.value,
    )

    expires_at: Mapped[datetime] = mapped_column(DateTime)

    completed_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id"), nullable=True
    )

    document: Mapped[Document] = relationship(back_populates="upload_sessions")
