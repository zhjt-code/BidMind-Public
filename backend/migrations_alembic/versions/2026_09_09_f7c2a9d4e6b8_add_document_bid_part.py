"""add_document_bid_part

投标信封分卷: documents.bid_part 标记 BID 文档在信封中的卷角色
- NULL: 整本投标文件(存量数据与默认)
- commercial / technical: 商务标 / 技术标分卷

部分唯一索引 (project_id, doc_type, bid_part) WHERE bid_part IS NOT NULL:
同一项目同一卷角色至多一份; NULL 不参与唯一(串标多 BID 不受影响,
存量 bid_part 全为 NULL, 无回填冲突)。

Revision ID: f7c2a9d4e6b8
Revises: e9b1c3d5f7a0
Create Date: 2026-09-09 17:00:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f7c2a9d4e6b8"
down_revision: Union[str, None] = "e9b1c3d5f7a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "bid_part", sa.String(length=16), nullable=True,
            comment="投标信封卷角色: NULL=整本 / commercial=商务标 / technical=技术标",
        ),
    )
    op.create_index(
        "uq_documents_project_doc_type_bid_part",
        "documents",
        ["project_id", "doc_type", "bid_part"],
        unique=True,
        postgresql_where=sa.text("bid_part IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_documents_project_doc_type_bid_part",
        table_name="documents",
    )
    op.drop_column("documents", "bid_part")
