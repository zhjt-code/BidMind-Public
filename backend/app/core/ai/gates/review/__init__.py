"""投标审查侧 Gate 子包（B3.1 / B3.6）。"""

from .claim_response_consistency import ClaimResponseConsistencyGate
from .cross_group_conflict import CrossGroupConflictGate
from .page_anchor_verify import PageAnchorVerifyGate
from .relevance_anomaly_backfill import RelevanceAnomalyBackfillGate
from .relevance_guard import RelevanceGuardGate

__all__ = [
    "ClaimResponseConsistencyGate",
    "CrossGroupConflictGate",
    "PageAnchorVerifyGate",
    "RelevanceAnomalyBackfillGate",
    "RelevanceGuardGate",
]
