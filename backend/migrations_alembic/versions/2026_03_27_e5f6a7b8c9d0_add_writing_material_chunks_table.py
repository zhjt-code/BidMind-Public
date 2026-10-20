"""add writing_material_chunks table

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-27

素材处理链升级 Phase 1: 创建 writing_material_chunks 表
- 独立于投标审查的 chunks 表,专用于标书编写素材
- 通过 material_id 关联 writing_materials,支持级联删除
- embedding 字段为 vector(1024) 类型,用于语义匹配
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE writing_material_chunks (
            id          SERIAL PRIMARY KEY,
            material_id INTEGER NOT NULL REFERENCES writing_materials(id) ON DELETE CASCADE,
            project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            content     TEXT NOT NULL,
            embedding   vector(1024),
            chunk_index INTEGER NOT NULL,
            char_count  INTEGER NOT NULL,
            page_num    INTEGER,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_writing_material_chunks_id ON writing_material_chunks (id)")
    op.execute("CREATE INDEX ix_writing_material_chunks_material_id ON writing_material_chunks (material_id)")
    op.execute("CREATE INDEX ix_writing_material_chunks_project_id ON writing_material_chunks (project_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS writing_material_chunks CASCADE")
