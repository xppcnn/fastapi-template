"""review rules, model runs and extraction batches

Revision ID: a1b2c3d4e5f6
Revises: 8fb3a1aee1a9
Create Date: 2026-08-27 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '8fb3a1aee1a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enum(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def _base_columns() -> list[sa.Column]:
    return [
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('public_id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    ]


_ENUM_CHECK_CONSTRAINTS = [
    ('model_runs', 'model_run_status'),
    ('review_rules', 'rule_type'),
    ('review_rules', 'evaluation_method'),
    ('review_rules', 'scoring_method'),
    ('review_rules', 'rule_status'),
    ('rule_extraction_runs', 'rule_extraction_run_status'),
    ('rule_extract_batches', 'rule_extract_batch_status'),
]

# (table, constraint_name, column_name, [values]) —— 用于 downgrade 重建 CHECK
_DROPPED_ENUM_CHECKS: list[tuple[str, str, str, list[str]]] = [
    ('model_runs', 'model_run_status', 'status', ['running', 'succeeded', 'failed']),
    ('review_rules', 'rule_type', 'rule_type', ['disqualification', 'qualification', 'response', 'scoring']),
    ('review_rules', 'evaluation_method', 'evaluation_method', ['deterministic', 'semantic']),
    ('review_rules', 'scoring_method', 'scoring_method', ['none', 'objective', 'subjective']),
    ('review_rules', 'rule_status', 'status', ['draft', 'confirmed', 'ignored']),
    ('rule_extraction_runs', 'rule_extraction_run_status', 'status', ['queued', 'running', 'succeeded', 'partial', 'failed']),
    ('rule_extract_batches', 'rule_extract_batch_status', 'status', ['pending', 'running', 'succeeded', 'failed']),
]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('model_runs',
        sa.Column('organization_id', sa.BigInteger(), nullable=False),
        sa.Column('project_id', sa.BigInteger(), nullable=False),
        sa.Column('review_run_id', sa.BigInteger(), nullable=True, comment='预留:Task 9 审核运行表落地后加 FK'),
        sa.Column('operation_id', sa.String(length=120), nullable=False),
        sa.Column('attempt', sa.Integer(), nullable=False),
        sa.Column('operation_name', sa.String(length=80), nullable=False),
        sa.Column('status', _enum('running', 'succeeded', 'failed', name='model_run_status'), server_default='running', nullable=False),
        sa.Column('model', sa.String(length=120), nullable=False),
        sa.Column('prompt_version', sa.String(length=80), nullable=True),
        sa.Column('model_input_hash', sa.String(length=64), nullable=True, comment='规范化输入消息的 SHA-256,用于追溯'),
        sa.Column('raw_output', sa.Text(), nullable=True),
        sa.Column('error_message', sa.String(length=2000), nullable=True),
        sa.Column('prompt_tokens', sa.Integer(), nullable=True),
        sa.Column('completion_tokens', sa.Integer(), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        *_base_columns(),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('operation_id', 'attempt', name='uq_model_runs_operation_attempt'),
    )
    op.create_index(op.f('ix_model_runs_public_id'), 'model_runs', ['public_id'], unique=True)
    op.create_index(op.f('ix_model_runs_operation_id'), 'model_runs', ['operation_id'], unique=False)
    op.create_index(op.f('ix_model_runs_organization_id'), 'model_runs', ['organization_id'], unique=False)
    op.create_index(op.f('ix_model_runs_project_id'), 'model_runs', ['project_id'], unique=False)

    op.create_table('review_rules',
        sa.Column('organization_id', sa.BigInteger(), nullable=False),
        sa.Column('project_id', sa.BigInteger(), nullable=False),
        sa.Column('tender_version_id', sa.BigInteger(), nullable=False),
        sa.Column('rule_type', _enum('disqualification', 'qualification', 'response', 'scoring', name='rule_type'), nullable=False),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, comment='要求原文/判定口径'),
        sa.Column('evaluation_method', _enum('deterministic', 'semantic', name='evaluation_method'), server_default='semantic', nullable=False),
        sa.Column('condition', sa.JSON(), nullable=True, comment='受控运算符条件(仅 deterministic 使用)'),
        sa.Column('scoring_method', _enum('none', 'objective', 'subjective', name='scoring_method'), server_default='none', nullable=False),
        sa.Column('max_score', sa.Float(), nullable=True),
        sa.Column('evaluation_criterion', sa.Text(), nullable=True, comment='评分项扣分判定口径(评价对象之外的评分依据)'),
        sa.Column('status', _enum('draft', 'confirmed', 'ignored', name='rule_status'), server_default='draft', nullable=False),
        sa.Column('needs_review', sa.Boolean(), server_default=sa.text('false'), nullable=False, comment='越界/不可溯源产物标记,确认前须人工复核'),
        sa.Column('source_segment_ids', sa.JSON(), nullable=True, comment='合并来源(产生该 draft 的 DocumentBlock 公开 UUID 列表)'),
        sa.Column('dedup_key', sa.String(length=500), nullable=False, comment='类型+判定口径的规范化去重 key'),
        sa.Column('model_run_id', sa.BigInteger(), nullable=True),
        sa.Column('extraction_run_id', sa.BigInteger(), nullable=True, comment='产生该 draft 的规则提取批次运行(人工规则为空)'),
        sa.Column('prompt_version', sa.String(length=80), nullable=True),
        sa.Column('created_by_id', sa.BigInteger(), nullable=True),
        sa.Column('version', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), server_default='1', nullable=False),
        *_base_columns(),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['model_run_id'], ['model_runs.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tender_version_id'], ['document_versions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_review_rules_public_id'), 'review_rules', ['public_id'], unique=True)
    op.create_index(op.f('ix_review_rules_organization_id'), 'review_rules', ['organization_id'], unique=False)
    op.create_index(op.f('ix_review_rules_project_id'), 'review_rules', ['project_id'], unique=False)
    op.create_index(op.f('ix_review_rules_tender_version_id'), 'review_rules', ['tender_version_id'], unique=False)

    op.create_table('rule_extraction_runs',
        sa.Column('organization_id', sa.BigInteger(), nullable=False),
        sa.Column('project_id', sa.BigInteger(), nullable=False),
        sa.Column('tender_version_id', sa.BigInteger(), nullable=False),
        sa.Column('status', _enum('queued', 'running', 'succeeded', 'partial', 'failed', name='rule_extraction_run_status'), server_default='queued', nullable=False),
        sa.Column('total_batches', sa.Integer(), nullable=False),
        sa.Column('completed_batches', sa.Integer(), server_default='0', nullable=False),
        sa.Column('failed_batches', sa.Integer(), server_default='0', nullable=False),
        sa.Column('error_message', sa.String(length=2000), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        *_base_columns(),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tender_version_id'], ['document_versions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_rule_extraction_runs_public_id'), 'rule_extraction_runs', ['public_id'], unique=True)
    op.create_index(op.f('ix_rule_extraction_runs_organization_id'), 'rule_extraction_runs', ['organization_id'], unique=False)
    op.create_index(op.f('ix_rule_extraction_runs_project_id'), 'rule_extraction_runs', ['project_id'], unique=False)
    op.create_index(op.f('ix_rule_extraction_runs_tender_version_id'), 'rule_extraction_runs', ['tender_version_id'], unique=False)

    op.create_table('rule_extract_batches',
        sa.Column('run_id', sa.BigInteger(), nullable=False),
        sa.Column('order_index', sa.Integer(), nullable=False),
        sa.Column('status', _enum('pending', 'running', 'succeeded', 'failed', name='rule_extract_batch_status'), server_default='pending', nullable=False),
        sa.Column('first_order_index', sa.Integer(), nullable=True, comment='该批覆盖的 DocumentBlock order_index 范围起点'),
        sa.Column('last_order_index', sa.Integer(), nullable=True, comment='该批覆盖的 DocumentBlock order_index 范围终点'),
        sa.Column('error_message', sa.String(length=2000), nullable=True),
        *_base_columns(),
        sa.ForeignKeyConstraint(['run_id'], ['rule_extraction_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'order_index', name='uq_rule_extract_batches_run_order'),
    )
    op.create_index(op.f('ix_rule_extract_batches_public_id'), 'rule_extract_batches', ['public_id'], unique=True)
    op.create_index(op.f('ix_rule_extract_batches_run_id'), 'rule_extract_batches', ['run_id'], unique=False)

    # Enum(native_enum=False, create_constraint=True) 生成的 CHECK 是 implicit 的,
    # 不在模型元数据中,主动 drop 保持 alembic check 干净(与既有迁移约定一致)。
    for table, column in _ENUM_CHECK_CONSTRAINTS:
        op.drop_constraint(op.f(column), table, type_='check')


def downgrade() -> None:
    """Downgrade schema."""
    for table, constraint, column, values in _DROPPED_ENUM_CHECKS:
        quoted = ", ".join(f"'{v}'::character varying" for v in values)
        op.create_check_constraint(
            op.f(constraint), table,
            f"{column}::text = ANY (ARRAY[{quoted}]::text[])",
        )
    op.drop_index(op.f('ix_rule_extract_batches_run_id'), table_name='rule_extract_batches')
    op.drop_index(op.f('ix_rule_extract_batches_public_id'), table_name='rule_extract_batches')
    op.drop_table('rule_extract_batches')
    op.drop_index(op.f('ix_rule_extraction_runs_tender_version_id'), table_name='rule_extraction_runs')
    op.drop_index(op.f('ix_rule_extraction_runs_project_id'), table_name='rule_extraction_runs')
    op.drop_index(op.f('ix_rule_extraction_runs_organization_id'), table_name='rule_extraction_runs')
    op.drop_index(op.f('ix_rule_extraction_runs_public_id'), table_name='rule_extraction_runs')
    op.drop_table('rule_extraction_runs')
    op.drop_index(op.f('ix_review_rules_tender_version_id'), table_name='review_rules')
    op.drop_index(op.f('ix_review_rules_project_id'), table_name='review_rules')
    op.drop_index(op.f('ix_review_rules_organization_id'), table_name='review_rules')
    op.drop_index(op.f('ix_review_rules_public_id'), table_name='review_rules')
    op.drop_table('review_rules')
    op.drop_index(op.f('ix_model_runs_project_id'), table_name='model_runs')
    op.drop_index(op.f('ix_model_runs_organization_id'), table_name='model_runs')
    op.drop_index(op.f('ix_model_runs_operation_id'), table_name='model_runs')
    op.drop_index(op.f('ix_model_runs_public_id'), table_name='model_runs')
    op.drop_table('model_runs')