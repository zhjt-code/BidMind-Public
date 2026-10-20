"""add system_error_logs table

Revision ID: a1b2c3d4e5f6
Revises: f9442dd50225
Create Date: 2026-03-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f9442dd50225'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'system_error_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('level', sa.String(length=20), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('traceback', sa.Text(), nullable=True),
        sa.Column('module', sa.String(length=255), nullable=True),
        sa.Column('function_name', sa.String(length=255), nullable=True),
        sa.Column('line_number', sa.Integer(), nullable=True),
        sa.Column('request_id', sa.String(length=64), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('task_type', sa.String(length=50), nullable=True),
        sa.Column('stage', sa.String(length=100), nullable=True),
        sa.Column('source', sa.String(length=20), nullable=False, server_default='backend'),
        sa.Column('resolved', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('resolved_by', sa.String(length=100), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_system_error_logs_id'), 'system_error_logs', ['id'])
    op.create_index(op.f('ix_system_error_logs_level'), 'system_error_logs', ['level'])
    op.create_index(op.f('ix_system_error_logs_module'), 'system_error_logs', ['module'])
    op.create_index(op.f('ix_system_error_logs_request_id'), 'system_error_logs', ['request_id'])
    op.create_index(op.f('ix_system_error_logs_user_id'), 'system_error_logs', ['user_id'])
    op.create_index(op.f('ix_system_error_logs_project_id'), 'system_error_logs', ['project_id'])
    op.create_index(op.f('ix_system_error_logs_task_type'), 'system_error_logs', ['task_type'])
    op.create_index(op.f('ix_system_error_logs_resolved'), 'system_error_logs', ['resolved'])
    op.create_index('ix_system_error_logs_created_at_desc', 'system_error_logs', [sa.text('created_at DESC')])


def downgrade() -> None:
    op.drop_index('ix_system_error_logs_created_at_desc', table_name='system_error_logs')
    op.drop_index(op.f('ix_system_error_logs_resolved'), table_name='system_error_logs')
    op.drop_index(op.f('ix_system_error_logs_task_type'), table_name='system_error_logs')
    op.drop_index(op.f('ix_system_error_logs_project_id'), table_name='system_error_logs')
    op.drop_index(op.f('ix_system_error_logs_user_id'), table_name='system_error_logs')
    op.drop_index(op.f('ix_system_error_logs_request_id'), table_name='system_error_logs')
    op.drop_index(op.f('ix_system_error_logs_module'), table_name='system_error_logs')
    op.drop_index(op.f('ix_system_error_logs_level'), table_name='system_error_logs')
    op.drop_index(op.f('ix_system_error_logs_id'), table_name='system_error_logs')
    op.drop_table('system_error_logs')
