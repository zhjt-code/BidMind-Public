"""add_document_gate_fields

文档门禁(解析前要素校验):
- documents.gate_verdict: 门禁结论 pass/suspect/unreadable
- documents.gate_result: 门禁校验结果JSON(DocGateResult)
- documents.gate_ack: 用户是否确认警告后继续试用(留痕)

存量文档不回填(gate_verdict/gate_result 可空表示未经过门禁)。

Revision ID: e5b7d9f1a3c5
Revises: d2f4a6b8c0e1
Create Date: 2026-07-23 16:00:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e5b7d9f1a3c5"
down_revision: Union[str, None] = "d2f4a6b8c0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "gate_verdict", sa.String(length=16), nullable=True,
            comment="门禁结论: pass/suspect/unreadable",
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "gate_result", sa.JSON(), nullable=True,
            comment="门禁校验结果JSON(DocGateResult)",
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "gate_ack", sa.Boolean(), nullable=False,
            server_default=sa.false(),
            comment="用户是否确认警告后继续",
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "gate_ack")
    op.drop_column("documents", "gate_result")
    op.drop_column("documents", "gate_verdict")
