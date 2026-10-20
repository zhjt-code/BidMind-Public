"""add error log traffic class and occurrence count

 错误日志流量隔离与同源聚合:
- traffic_class: 隔离 pytest / 管理员探测 / 生产流量, 让日志条数重新可用于判断生产可靠性
- occurrence_count: 同指纹回声折叠为一行并累加次数, 替代原先"窗口内直接丢弃"

向后兼容说明:
- 两列均带 server_default, 不含新列的旧代码分支可以继续正常 INSERT
- 历史行 traffic_class 一律为 unknown(真实来源不可回溯, 禁止伪造回填)
- server_default 保留在表定义上, 不做 DROP DEFAULT, 保证灰度期间新旧代码共存

Revision ID: c9d3e7a52f61
Revises: f6b2d8c4a1e7
Create Date: 2026-08-09 13:40:00.000000+08:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c9d3e7a52f61"
down_revision: Union[str, None] = "f6b2d8c4a1e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "system_error_logs",
        sa.Column(
            "traffic_class",
            sa.String(length=16),
            nullable=False,
            server_default="unknown",
            comment="流量类别: production / admin_test / regression / unknown(存量)",
        ),
    )
    op.add_column(
        "system_error_logs",
        sa.Column(
            "occurrence_count",
            sa.Integer(),
            nullable=False,
            server_default="1",
            comment="同指纹回声折叠后的累计发生次数",
        ),
    )
    op.create_index(
        "ix_system_error_logs_traffic_class",
        "system_error_logs",
        ["traffic_class"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_system_error_logs_traffic_class",
        table_name="system_error_logs",
    )
    op.drop_column("system_error_logs", "occurrence_count")
    op.drop_column("system_error_logs", "traffic_class")
