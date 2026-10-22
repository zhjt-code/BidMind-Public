"""证据编排器 

职责: 为单条审查项组装可回放证据包（多通道召回、融合、覆盖快照）
不做: 符合性打分、串标定性、LLM 调用、ORM 持久化
"""

from app.core.retriever.evidence_pipeline.contracts import (
    ChannelHits,
    EvidencePackage,
    RetrievalPlan,
    RetrievalQuery,
)

__all__ = [
    "ChannelHits",
    "EvidencePackage",
    "RetrievalPlan",
    "RetrievalQuery",
]
