"""Add users, organizations, and memberships.

Revision ID: 20260810_0001
Revises: 3ee78dcbffbb
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260810_0001"
down_revision: str | Sequence[str] | None = "3ee78dcbffbb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("public_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "users",
        *_base_columns(),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_public_id", "users", ["public_id"], unique=True)

    op.create_table(
        "organizations",
        *_base_columns(),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "disabled",
                name="organization_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="active",
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_organizations_public_id",
        "organizations",
        ["public_id"],
        unique=True,
    )

    op.create_table(
        "memberships",
        *_base_columns(),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "owner",
                "member",
                name="membership_role",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="member",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "organization_id",
            name="uq_memberships_user_organization",
        ),
    )
    op.create_index(
        "ix_memberships_organization_id",
        "memberships",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_memberships_public_id", "memberships", ["public_id"], unique=True
    )
    op.create_index(
        "ix_memberships_user_id", "memberships", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_memberships_user_id", table_name="memberships")
    op.drop_index("ix_memberships_public_id", table_name="memberships")
    op.drop_index("ix_memberships_organization_id", table_name="memberships")
    op.drop_table("memberships")
    op.drop_index("ix_organizations_public_id", table_name="organizations")
    op.drop_table("organizations")
    op.drop_index("ix_users_public_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
