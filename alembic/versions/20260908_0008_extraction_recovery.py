"""Fence rule extraction executions across retries.

Revision ID: b8e091080001
Revises: a1b2c3d4e5f6
"""

import sqlalchemy as sa

from alembic import op

revision = "b8e091080001"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rule_extraction_runs",
        sa.Column(
            "execution_token", sa.String(36), nullable=False, server_default="legacy"
        ),
    )
    op.execute(
        "UPDATE rule_extraction_runs SET execution_token = CAST(public_id AS VARCHAR(36))"
    )
    with op.batch_alter_table("rule_extraction_runs") as batch:
        batch.alter_column(
            "execution_token", existing_type=sa.String(36), server_default=None
        )


def downgrade() -> None:
    with op.batch_alter_table("rule_extraction_runs") as batch:
        batch.drop_column("execution_token")
