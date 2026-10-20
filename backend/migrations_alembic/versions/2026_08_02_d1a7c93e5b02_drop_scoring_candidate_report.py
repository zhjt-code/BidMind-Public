"""drop_scoring_candidate_report

删除 documents.scoring_candidate_report 列（ADR 2026-07-28 §4 冻结批次落地）。

候选事务链路（candidate_transaction）已按"账本换轴"ADR 判定为废弃路线并整包删除：
生产模式长期为 off，该列在 off 模式下从不写入（persist_candidate_report 不被调用），
requirement_service 亦显式对 off 模式返回 None，故删除对现网行为无影响。

降级路径: downgrade 重建同名 nullable JSON 列（数据不可恢复，仅恢复结构）。

Revision ID: d1a7c93e5b02
Revises: c4e6f8a0b2d4
Create Date: 2026-08-02 17:30:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1a7c93e5b02"
down_revision: Union[str, None] = "c4e6f8a0b2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("documents", "scoring_candidate_report")


def downgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "scoring_candidate_report",
            sa.JSON(),
            nullable=True,
            comment="评分表统一候选事务脱敏报告",
        ),
    )
