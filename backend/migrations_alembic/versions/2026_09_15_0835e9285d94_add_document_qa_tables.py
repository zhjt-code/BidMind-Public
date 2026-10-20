"""add_document_qa_tables

文档质检(Document QA)独立结果域:
- document_qa_runs: QA 运行(scope=document 单文档 / scope=envelope 信封收敛)
- document_qa_findings: 发现项(含人工处置三态与乐观锁版本)

与 review_results 物理隔离, 不绑定 Requirement, 不参与符合度汇总。

Revision ID: 0835e9285d94
Revises: e8f1a2b3c4d5
Create Date: 2026-09-15 10:00:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0835e9285d94"
down_revision: Union[str, None] = "e8f1a2b3c4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_qa_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("ruleset_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("envelope_signature", JSONB(), nullable=True),
        sa.Column("features", JSONB(), nullable=True),
        sa.Column("summary", JSONB(), nullable=True),
        sa.Column("checker_status", JSONB(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "scope", "input_fingerprint",
            name="uq_document_qa_run_fingerprint",
        ),
    )
    op.create_index(
        op.f("ix_document_qa_runs_id"), "document_qa_runs", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_document_qa_runs_project_id"),
        "document_qa_runs", ["project_id"], unique=False,
    )
    op.create_index(
        op.f("ix_document_qa_runs_document_id"),
        "document_qa_runs", ["document_id"], unique=False,
    )

    op.create_table(
        "document_qa_findings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("rule_code", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("certainty", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("suggestion", sa.Text(), nullable=True),
        sa.Column("evidence", JSONB(), nullable=False),
        sa.Column("finding_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("disposition", sa.String(length=20), nullable=False),
        sa.Column("disposition_note", sa.Text(), nullable=True),
        sa.Column("disposition_version", sa.Integer(), nullable=False),
        sa.Column("disposed_by", sa.Integer(), nullable=True),
        sa.Column("disposed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"], ["document_qa_runs.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "finding_fingerprint",
            name="uq_document_qa_finding_fingerprint",
        ),
    )
    op.create_index(
        op.f("ix_document_qa_findings_id"),
        "document_qa_findings", ["id"], unique=False,
    )
    op.create_index(
        op.f("ix_document_qa_findings_run_id"),
        "document_qa_findings", ["run_id"], unique=False,
    )
    op.create_index(
        op.f("ix_document_qa_findings_project_id"),
        "document_qa_findings", ["project_id"], unique=False,
    )
    op.create_index(
        op.f("ix_document_qa_findings_category"),
        "document_qa_findings", ["category"], unique=False,
    )
    op.create_index(
        op.f("ix_document_qa_findings_severity"),
        "document_qa_findings", ["severity"], unique=False,
    )
    op.create_index(
        op.f("ix_document_qa_findings_finding_fingerprint"),
        "document_qa_findings", ["finding_fingerprint"], unique=False,
    )


def downgrade() -> None:
    op.drop_table("document_qa_findings")
    op.drop_table("document_qa_runs")
