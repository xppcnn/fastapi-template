"""add documents table

Revision ID: a34eb0615892
Revises: aa048987aa10
Create Date: 2026-08-18 17:47:21.011737

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a34eb0615892'
down_revision: Union[str, Sequence[str], None] = 'aa048987aa10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('documents',
    sa.Column('organization_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('project_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('doc_type', sa.Enum('tender', 'bid', name='doc_type', native_enum=False, create_constraint=True), server_default='tender', nullable=False),
    sa.Column('active_version_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('created_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('deleted_at', sa.DateTime(), nullable=True),
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('public_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('is_deleted', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'name', 'doc_type', 'deleted_at', name='uq_documents_project_name_type_deleted_at')
    )
    op.create_index(op.f('ix_documents_organization_id'), 'documents', ['organization_id'], unique=False)
    op.create_index(op.f('ix_documents_project_id'), 'documents', ['project_id'], unique=False)
    op.create_index(op.f('ix_documents_public_id'), 'documents', ['public_id'], unique=True)
    op.create_table('document_versions',
    sa.Column('document_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('version_number', sa.Integer(), nullable=False),
    sa.Column('object_key', sa.String(length=512), nullable=False, comment='对象存储中的对象键'),
    sa.Column('file_name', sa.String(length=255), nullable=False, comment='原始文件名'),
    sa.Column('content_type', sa.String(length=255), nullable=False, comment='MIME 类型'),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('parse_status', sa.Enum('uploaded', 'parsing', 'parsed', 'failed', name='parse_status', native_enum=False, create_constraint=True), server_default='uploaded', nullable=False),
    sa.Column('parse_job_id', sa.Integer(), nullable=True, comment='解析任务 id；待 jobs 表创建后补充外键'),
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('public_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('is_deleted', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('document_id', 'version_number', name='uq_document_version')
    )
    op.create_index(op.f('ix_document_versions_document_id'), 'document_versions', ['document_id'], unique=False)
    op.create_index(op.f('ix_document_versions_public_id'), 'document_versions', ['public_id'], unique=True)
    op.create_table('upload_sessions',
    sa.Column('document_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('object_key', sa.String(length=512), nullable=False, comment='对象存储中的对象键'),
    sa.Column('file_name', sa.String(length=255), nullable=False, comment='原始文件名'),
    sa.Column('content_type', sa.String(length=255), nullable=False, comment='MIME 类型'),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'completed', 'expired', name='upload_session_status', native_enum=False, create_constraint=True), server_default='pending', nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('completed_version_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('public_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('is_deleted', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.ForeignKeyConstraint(['completed_version_id'], ['document_versions.id'], ),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_upload_sessions_document_id'), 'upload_sessions', ['document_id'], unique=False)
    op.create_index(op.f('ix_upload_sessions_public_id'), 'upload_sessions', ['public_id'], unique=True)
    op.create_foreign_key(
        'fk_documents_active_version_id',
        'documents',
        'document_versions',
        ['active_version_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.drop_constraint(op.f('project_status'), 'projects', type_='check')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_check_constraint(op.f('project_status'), 'projects', "status::text = ANY (ARRAY['active'::character varying, 'archived'::character varying]::text[])")
    op.drop_constraint(op.f('fk_documents_active_version_id'), 'documents', type_='foreignkey')
    op.drop_index(op.f('ix_upload_sessions_public_id'), table_name='upload_sessions')
    op.drop_index(op.f('ix_upload_sessions_document_id'), table_name='upload_sessions')
    op.drop_table('upload_sessions')
    op.drop_index(op.f('ix_document_versions_public_id'), table_name='document_versions')
    op.drop_index(op.f('ix_document_versions_document_id'), table_name='document_versions')
    op.drop_table('document_versions')
    op.drop_index(op.f('ix_documents_public_id'), table_name='documents')
    op.drop_index(op.f('ix_documents_project_id'), table_name='documents')
    op.drop_index(op.f('ix_documents_organization_id'), table_name='documents')
    op.drop_table('documents')
