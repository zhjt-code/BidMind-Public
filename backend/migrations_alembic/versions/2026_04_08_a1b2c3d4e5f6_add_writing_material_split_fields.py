"""add_writing_material_split_fields

Revision ID: a9b8c7d6e5f4
Revises: ccab285075f3
Create Date: 2026-04-08

为 writing_materials 增加综合 PDF 拆分相关字段：
- parent_material_id
- source_page_start / source_page_end
- split_confidence
- material_role
- split_review_status
- structure_hint_label
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a9b8c7d6e5f4"
down_revision: Union[str, None] = "ccab285075f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE writing_materials ADD COLUMN IF NOT EXISTS parent_material_id INTEGER")
    op.execute("ALTER TABLE writing_materials ADD COLUMN IF NOT EXISTS source_page_start INTEGER")
    op.execute("ALTER TABLE writing_materials ADD COLUMN IF NOT EXISTS source_page_end INTEGER")
    op.execute("ALTER TABLE writing_materials ADD COLUMN IF NOT EXISTS split_confidence DOUBLE PRECISION")
    op.execute(
        "ALTER TABLE writing_materials "
        "ADD COLUMN IF NOT EXISTS material_role VARCHAR(20) NOT NULL DEFAULT 'normal'"
    )
    op.execute(
        "ALTER TABLE writing_materials "
        "ADD COLUMN IF NOT EXISTS split_review_status VARCHAR(20) NOT NULL DEFAULT 'none'"
    )
    op.execute("ALTER TABLE writing_materials ADD COLUMN IF NOT EXISTS structure_hint_label VARCHAR(100)")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'fk_writing_materials_parent_material_id'
            ) THEN
                ALTER TABLE writing_materials
                ADD CONSTRAINT fk_writing_materials_parent_material_id
                FOREIGN KEY (parent_material_id)
                REFERENCES writing_materials (id)
                ON DELETE CASCADE;
            END IF;
        END$$;
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_writing_materials_parent_material_id "
        "ON writing_materials (parent_material_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_writing_materials_role_status "
        "ON writing_materials (project_id, material_role, split_review_status)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_writing_materials_role_status")
    op.execute("DROP INDEX IF EXISTS idx_writing_materials_parent_material_id")
    op.execute(
        "ALTER TABLE writing_materials DROP CONSTRAINT IF EXISTS fk_writing_materials_parent_material_id"
    )
    op.execute("ALTER TABLE writing_materials DROP COLUMN IF EXISTS structure_hint_label")
    op.execute("ALTER TABLE writing_materials DROP COLUMN IF EXISTS split_review_status")
    op.execute("ALTER TABLE writing_materials DROP COLUMN IF EXISTS material_role")
    op.execute("ALTER TABLE writing_materials DROP COLUMN IF EXISTS split_confidence")
    op.execute("ALTER TABLE writing_materials DROP COLUMN IF EXISTS source_page_end")
    op.execute("ALTER TABLE writing_materials DROP COLUMN IF EXISTS source_page_start")
    op.execute("ALTER TABLE writing_materials DROP COLUMN IF EXISTS parent_material_id")
