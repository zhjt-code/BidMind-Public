"""backfill outline_scope_titles node_class to mechanical: 提取型节点误标 judgment，小预算在思考池被 CoT 吃光。
本迁移幂等回填为 mechanical。与 同机制，本案例为第二实体。

Revision ID: e9b1c3d5f7a0
Revises: d7f9b1c3e5a2
Create Date: 2026-09-08 14:40:00.000000+08:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e9b1c3d5f7a0"
down_revision: Union[str, None] = "d7f9b1c3e5a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NODE_KEY = "writing.outline_scope_titles"


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE llm_nodes SET node_class = 'mechanical' "
            "WHERE node_key = :node_key"
        ).bindparams(node_key=_NODE_KEY)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE llm_nodes SET node_class = 'judgment' "
            "WHERE node_key = :node_key"
        ).bindparams(node_key=_NODE_KEY)
    )
