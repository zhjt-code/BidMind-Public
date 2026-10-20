"""add pg_trgm indexes on chunk content and section_path

词项检索通道: 为 chunks.content / section_path 提供 trigram GIN,
支撑 CJK 精确/模糊匹配, 不引入 jieba 或 zhparser.

Revision ID: e7c1a9b3d5f2
Revises: d0e2f4a6b8c1
Create Date: 2026-09-03 15:20:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op

revision: str = "e7c1a9b3d5f2"
down_revision: Union[str, None] = 'c9e1f3a5b7d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_content_trgm "
        "ON chunks USING gin (content gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_section_path_trgm "
        "ON chunks USING gin (section_path gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_section_path_trgm")
    op.execute("DROP INDEX IF EXISTS ix_chunks_content_trgm")
