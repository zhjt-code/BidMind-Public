"""b1_add_failure_state_fields

Revision ID: a1b2c3d4e5f6
Revises: f7e8d9c0b1a2
Create Date: 2026-04-17

B1 基础设施：4 张表新增 failure_contract §4.1 的 7 字段 + 2 索引。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "ba47204e9080"
down_revision: Union[str, None] = "f7e8d9c0b1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = [
    "review_results",
    "collusion_results",
    "collusion_extractions",
    "writing_section_drafts",
]

_COLUMNS = [
    ("failure_state", sa.String(32), False, "completed"),
    ("failure_reason", sa.Text(), True, None),
    ("confidence", sa.Float(), True, None),
    ("gate_trace", sa.JSON(), True, None),
    ("review_required", sa.SmallInteger(), False, 0),
    ("reviewed_at", sa.DateTime(timezone=True), True, None),
    ("reviewed_by", sa.Integer(), True, None),
]


_INDEX_COL2 = {
    "writing_section_drafts": "outline_node_id",
}


def upgrade() -> None:
    for table in _TABLES:
        for col_name, col_type, nullable, default in _COLUMNS:
            if isinstance(col_type, sa.String) and default is not None:
                op.add_column(table, sa.Column(col_name, col_type, nullable=nullable, server_default=default))
            elif isinstance(col_type, sa.SmallInteger) and default is not None:
                op.add_column(table, sa.Column(col_name, col_type, nullable=nullable, server_default=str(default)))
            else:
                op.add_column(table, sa.Column(col_name, col_type, nullable=nullable))

        col2 = _INDEX_COL2.get(table, "project_id")
        op.create_index(
            f"ix_{table}_failure_state_{col2}",
            table,
            ["failure_state", col2],
        )
        op.create_index(
            f"ix_{table}_review_required",
            table,
            ["review_required"],
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        col2 = _INDEX_COL2.get(table, "project_id")
        op.drop_index(f"ix_{table}_review_required", table_name=table)
        op.drop_index(f"ix_{table}_failure_state_{col2}", table_name=table)

        for col_name, _, _, _ in reversed(_COLUMNS):
            op.drop_column(table, col_name)
