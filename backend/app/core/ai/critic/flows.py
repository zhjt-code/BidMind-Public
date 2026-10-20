"""Critic flow constants shared by CoVe/Judge and ops metrics.

This module is intentionally dependency-free to avoid import cycles. It
distinguishes two concepts:

- metric flow: the label written to Prometheus, e.g. ``investigate``;
- judge template flow: the prompt namespace used by Judge, e.g. ``review``.

 showed that keeping these as unrelated string literals lets a valid
metric label silently fall back to the generic Judge prompt. Keep aliases here
so new flows must be reviewed in one place.
"""

from __future__ import annotations

from typing import Literal

FLOW_INVESTIGATE: Literal["investigate"] = "investigate"
FLOW_REVIEW: Literal["review"] = "review"
FLOW_COLLUSION: Literal["collusion"] = "collusion"
FLOW_WRITER: Literal["writer"] = "writer"
FLOW_GENERIC: Literal["generic"] = "generic"

CriticFlow = Literal[
    "investigate",
    "review",
    "collusion",
    "writer",
    "generic",
]

# Flow labels that represent real business paths in Critic metrics.
CRITIC_BUSINESS_FLOWS: tuple[CriticFlow, ...] = (
    FLOW_INVESTIGATE,
    FLOW_COLLUSION,
    FLOW_WRITER,
)

# Judge prompt routing aliases.  The metric label remains unchanged; only the
# prompt namespace is normalized.
JUDGE_TEMPLATE_FLOW_BY_METRIC_FLOW: dict[str, CriticFlow] = {
    FLOW_INVESTIGATE: FLOW_REVIEW,
    FLOW_REVIEW: FLOW_REVIEW,
    FLOW_COLLUSION: FLOW_COLLUSION,
    FLOW_WRITER: FLOW_WRITER,
    FLOW_GENERIC: FLOW_GENERIC,
}


def resolve_judge_template_flow(flow: str | None) -> CriticFlow:
    """Return the Judge prompt flow for a metric flow label.

    Unknown values intentionally fall back to ``generic`` to preserve Judge's
    fail-safe behavior for ad-hoc callers.
    """
    normalized = str(flow or FLOW_GENERIC).lower()
    return JUDGE_TEMPLATE_FLOW_BY_METRIC_FLOW.get(normalized, FLOW_GENERIC)

