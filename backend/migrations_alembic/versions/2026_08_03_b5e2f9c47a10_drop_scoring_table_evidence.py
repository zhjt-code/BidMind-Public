"""drop_scoring_table_evidence

删除 documents.scoring_table_evidence 列（死数据处置）。

该列的唯一消费者是候选事务提取器（candidate_transaction），已于治理阶段 2 整包删除。
消费者删除后生产者被遗留：`chunk_stage` 仍对每份含评分区段的文档捕获并落库装配前
物理表快照，实测本地库 63/894 份文档持有 594 kB（约占 documents 表 25%），
而全仓无任何读者，且其捕获模块的测试已随阶段 2 孤儿测试清理一并删除——
形成"能正常工作、零消费者、零测试覆盖"的活代码。

删除前已确认其数据并非不可替代:
`scoring_signals` 覆盖了账本实际消费的声明行通道（`declaration_pages_from_signals`
同时读 declarations 与 headings， 缺口已由此闭合）。本列独有的是装配前
物理表网格与全文档范围的上下文行，若未来需要（如结构声明桥），可由重新解析再生。

降级路径: downgrade 重建同名 nullable JSON 列（数据不可恢复，仅恢复结构）。

Revision ID: b5e2f9c47a10
Revises: d1a7c93e5b02
Create Date: 2026-08-03 10:50:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5e2f9c47a10"
down_revision: Union[str, None] = "d1a7c93e5b02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("documents", "scoring_table_evidence")


def downgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "scoring_table_evidence",
            sa.JSON(),
            nullable=True,
            comment="评分原始表证据快照JSON(装配前完整物理表)",
        ),
    )
