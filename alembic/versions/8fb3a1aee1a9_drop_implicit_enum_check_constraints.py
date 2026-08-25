"""drop implicit enum check constraints

Revision ID: 8fb3a1aee1a9
Revises: 85e3c58e5d9e
Create Date: 2026-08-25 16:07:01.189491

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8fb3a1aee1a9'
down_revision: Union[str, Sequence[str], None] = '85e3c58e5d9e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Enum(native_enum=False, create_constraint=True) 生成的 CHECK 约束是
    # implicit 的,不在模型元数据中,导致 alembic check 永远误报;
    # 主动 drop 后 alembic check 恢复干净。枚举值校验由应用层 StrEnum 承担。
    op.drop_constraint(op.f('parse_status'), 'document_versions', type_='check')
    op.drop_constraint(op.f('doc_type'), 'documents', type_='check')
    op.drop_constraint(op.f('upload_session_status'), 'upload_sessions', type_='check')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_check_constraint(op.f('upload_session_status'), 'upload_sessions', "status::text = ANY (ARRAY['pending'::character varying, 'completed'::character varying, 'expired'::character varying]::text[])")
    op.create_check_constraint(op.f('doc_type'), 'documents', "doc_type::text = ANY (ARRAY['tender'::character varying, 'bid'::character varying]::text[])")
    op.create_check_constraint(op.f('parse_status'), 'document_versions', "parse_status::text = ANY (ARRAY['uploaded'::character varying, 'parsing'::character varying, 'parsed'::character varying, 'failed'::character varying]::text[])")
