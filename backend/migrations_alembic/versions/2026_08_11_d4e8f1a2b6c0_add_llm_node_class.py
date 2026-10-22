"""add llm_nodes.node_class for mechanical/judgment pool routing

Wave1-S2:
- 新增 node_class 列（server_default=judgment）
- 种子回填 38 个 mechanical 节点

Revision ID: d4e8f1a2b6c0
Revises: c9d3e7a52f61
Create Date: 2026-08-11 11:00:00.000000+08:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e8f1a2b6c0"
down_revision: Union[str, None] = "c9d3e7a52f61"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MECHANICAL_NODE_KEYS: tuple[str, ...] = (
    # bid_review (15)
    "bid_review.tender_extract_page",
    "bid_review.tender_reflection",
    "bid_review.tender_merge",
    "bid_review.category_merge",
    "bid_review.category_merge_decision",
    "bid_review.secondary_merge",
    "bid_review.secondary_merge_decision",
    "bid_review.bid_extract_match",
    "bid_review.relevance_classify",
    "bid_review.scheme_reader",
    "bid_review.scoring_extract",
    "bid_review.scoring_repair",
    "bid_review.scoring_full_candidate",
    "bid_review.scoring_table_candidate",
    "bid_review.scoring_table_structure",
    # collusion (5)
    "collusion.route_extract",
    "collusion.general_extract",
    "collusion.tender_format_extract",
    "collusion.industry_signal",
    "collusion.project_summary",
    # writing (18)
    "writing.hard_clause",
    "writing.guide_clause",
    "writing.structure_clause",
    "writing.clause_classify",
    "writing.clause_dedup",
    "writing.clause_dedup_decision",
    "writing.material_tag",
    "writing.fixed_template_recognize",
    "writing.fixed_template_validate",
    "writing.image_classify",
    "writing.relevance_classify",
    "writing.render_config",
    "writing.bid_directory_extract",
    "writing.bid_structure_analysis",
    "writing.length_adjust",
    "writing.section_summary",
    "writing.cross_ref_check",
    "writing.chapter_profile",
)


def upgrade() -> None:
    op.add_column(
        "llm_nodes",
        sa.Column(
            "node_class",
            sa.String(length=16),
            nullable=False,
            server_default="judgment",
            comment="节点分类: mechanical / judgment",
        ),
    )
    keys = ", ".join(f"'{k}'" for k in _MECHANICAL_NODE_KEYS)
    op.execute(
        sa.text(
            f"UPDATE llm_nodes SET node_class = 'mechanical' WHERE node_key IN ({keys})"
        )
    )


def downgrade() -> None:
    op.drop_column("llm_nodes", "node_class")
