""": Query Planner 与 RRF 补充用例(与 lexical 测试分文件无写冲突)。"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.retriever.evidence_pipeline.contracts import ChannelHits, RetrievalQuery
from app.core.retriever.evidence_pipeline.query_planner import plan_retrieval_channels
from app.core.retriever.evidence_pipeline.rrf import RRF_K, fuse_channel_rankings
from app.core.retriever.semantic_retriever import SearchResult


def test_planner_table_hint_opens_table_channel() -> None:
    plan = plan_retrieval_channels(
        RetrievalQuery(document_id=1, description="正在施工和新建项目情况表")
    )
    assert plan.use_table is True


def test_rrf_empty_and_limit() -> None:
    assert fuse_channel_rankings([]) == []
    only = ChannelHits(
        "dense",
        [
            SearchResult(
                chunk_id=i,
                document_id=1,
                content=str(i),
                page_num=1,
                similarity=0.5,
                role="primary",
            )
            for i in range(1, 6)
        ],
    )
    fused = fuse_channel_rankings([only], limit=2)
    assert [item.chunk_id for item in fused] == [1, 2]
    assert RRF_K == 60
