"""add upload session idempotency key.

Revision ID: c1e9f4a2b8d7
Revises: a34eb0615892
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c1e9f4a2b8d7"
down_revision: str | Sequence[str] | None = "a34eb0615892"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "upload_sessions",
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_upload_sessions_document_idempotency_key",
        "upload_sessions",
        ["document_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_upload_sessions_document_idempotency_key",
        "upload_sessions",
        type_="unique",
    )
    op.drop_column("upload_sessions", "idempotency_key")