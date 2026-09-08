"""Immutable review inputs and confirmed rule snapshots.

Revision ID: b8e091080002
Revises: b8e091080001
"""

import sqlalchemy as sa

from alembic import op

revision = "b8e091080002"
down_revision = "b8e091080001"
branch_labels = None
depends_on = None


def _base_columns():
    return [
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("public_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "review_runs",
        *_base_columns(),
        sa.Column(
            "organization_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column(
            "tender_version_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "bid_version_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_by_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(5), server_default="ready", nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=True),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("rule_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "project_id", "idempotency_key", name="uq_review_runs_project_idempotency"
        ),
    )
    for column in ("public_id", "organization_id", "project_id"):
        op.create_index(
            f"ix_review_runs_{column}",
            "review_runs",
            [column],
            unique=column == "public_id",
        )
    op.create_table(
        "review_run_rules",
        *_base_columns(),
        sa.Column(
            "run_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.ForeignKey("review_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_rule_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.ForeignKey("review_rules.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_rule_version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "run_id", "source_rule_id", name="uq_review_run_rules_source"
        ),
    )
    for column in ("public_id", "run_id"):
        op.create_index(
            f"ix_review_run_rules_{column}",
            "review_run_rules",
            [column],
            unique=column == "public_id",
        )


def downgrade() -> None:
    op.drop_table("review_run_rules")
    op.drop_table("review_runs")
