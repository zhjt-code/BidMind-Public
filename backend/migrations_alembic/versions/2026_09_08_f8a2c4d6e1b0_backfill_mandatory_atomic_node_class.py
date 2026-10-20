"""backfill mandatory_atomic_extract node_class to mechanical: 新增节点未进 2026-08-11 回填清单，吃了 judgment 默认值，
核验窗口被路由进思考型 review 池。本迁移幂等回填为 mechanical。

Revision ID: f8a2c4d6e1b0
Revises: e7c1a9b3d5f2
Create Date: 2026-09-08 09:48:00.000000+08:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f8a2c4d6e1b0"
down_revision: Union[str, None] = "e7c1a9b3d5f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NODE_KEY = "bid_review.mandatory_atomic_extract"


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
