"""add_scoring_assignment_sidecar

评分指派内核 sidecar 存储(P3-4):
- assignment_runs: 文档级运行批次
- assignment_generations: staging 世代与 active 指针目标
- assignment_facts: 候选/采纳评分事实快照
- assignment_reports: 不变量报告与三态诊断

Revision ID: c4e6f8a0b2d4
Revises: b9d3f5a7c2e4
Create Date: 2026-07-27 16:05:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4e6f8a0b2d4"
down_revision: Union[str, None] = "b9d3f5a7c2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scoring_assignment_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("schema_version", sa.String(length=40), nullable=False, server_default="assignment-v1"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PROVISIONAL"),
        sa.Column("active_generation", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        comment="评分指派内核运行批次",
    )
    op.create_index("ix_scoring_assignment_runs_document_id", "scoring_assignment_runs", ["document_id"])

    op.create_table(
        "scoring_assignment_generations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("scoring_assignment_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("truth_scope", sa.JSON(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        comment="评分指派 staging 世代",
    )
    op.create_unique_constraint(
        "uq_scoring_assignment_generation",
        "scoring_assignment_generations",
        ["run_id", "generation"],
    )

    op.create_table(
        "scoring_assignment_facts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("generation_id", sa.Integer(), sa.ForeignKey("scoring_assignment_generations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_key", sa.String(length=255), nullable=False),
        sa.Column("assignment_id", sa.String(length=255), nullable=True),
        sa.Column("lot", sa.String(length=80), nullable=False, server_default="__single__"),
        sa.Column("scheme", sa.String(length=80), nullable=False, server_default="__single__"),
        sa.Column("volume", sa.String(length=80), nullable=False, server_default="unspecified"),
        sa.Column("fact_type", sa.String(length=40), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("score_value", sa.Float(), nullable=False),
        sa.Column("evidence_atom_keys", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PROVISIONAL"),
        sa.Column("payload", sa.JSON(), nullable=True),
        comment="评分指派事实快照",
    )
    op.create_index("ix_scoring_assignment_facts_generation_id", "scoring_assignment_facts", ["generation_id"])

    op.create_table(
        "scoring_assignment_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("generation_id", sa.Integer(), sa.ForeignKey("scoring_assignment_generations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assignment_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("violations", sa.JSON(), nullable=False),
        sa.Column("residual_atom_keys", sa.JSON(), nullable=False),
        sa.Column("conflict_atom_keys", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        comment="评分指派不变量报告",
    )
    op.create_index("ix_scoring_assignment_reports_generation_id", "scoring_assignment_reports", ["generation_id"])


def downgrade() -> None:
    op.drop_index("ix_scoring_assignment_reports_generation_id", table_name="scoring_assignment_reports")
    op.drop_table("scoring_assignment_reports")
    op.drop_index("ix_scoring_assignment_facts_generation_id", table_name="scoring_assignment_facts")
    op.drop_table("scoring_assignment_facts")
    op.drop_constraint("uq_scoring_assignment_generation", "scoring_assignment_generations", type_="unique")
    op.drop_table("scoring_assignment_generations")
    op.drop_index("ix_scoring_assignment_runs_document_id", table_name="scoring_assignment_runs")
    op.drop_table("scoring_assignment_runs")
