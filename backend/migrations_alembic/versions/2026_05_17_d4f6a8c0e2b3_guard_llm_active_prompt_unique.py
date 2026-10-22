"""guard_llm_active_prompt_unique

Revision ID: d4f6a8c0e2b3
Revises: c3e5f7a9b1d3
Create Date: 2026-05-17

清理 LLM Center 中同一 node + role 的重复 active Prompt, 并用 partial
unique index 固化“当前激活版本唯一”的不变量。

注意: downgrade 仅删除索引; upgrade 中对历史重复 active 的归档清理不可自动还原。
"""

from typing import Sequence, Union

from alembic import op


revision: str = "d4f6a8c0e2b3"
down_revision: Union[str, None] = "c3e5f7a9b1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY node_id, template_role
                    ORDER BY version DESC, id DESC
                ) AS rn
            FROM llm_template_versions
            WHERE is_active = TRUE
        )
        UPDATE llm_template_versions AS v
        SET
            is_active = FALSE,
            status = 'archived',
            updated_at = NOW()
        FROM ranked AS r
        WHERE v.id = r.id AND r.rn > 1
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_tv_active_node_role
        ON llm_template_versions (node_id, template_role)
        WHERE is_active = TRUE
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_llm_tv_active_node_role")
