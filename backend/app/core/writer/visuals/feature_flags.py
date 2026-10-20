"""事实型图示灰度开关。

职责: 基于项目ID和配置决定是否启用章节图示物化。
不做: 不读取数据库、不调用LLM、不修改运行时配置。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class SectionDiagramRolloutDecision:
    enabled: bool
    reason: str


def decide_section_diagram_rollout(
    project_id: int | None,
) -> SectionDiagramRolloutDecision:
    """Return stable diagram rollout decision for a project."""

    if not settings.WRITING_SECTION_DIAGRAM_ENABLED:
        return SectionDiagramRolloutDecision(False, "diagram_flag_off")
    if project_id is None:
        return SectionDiagramRolloutDecision(False, "diagram_no_project")
    ratio = max(0.0, min(1.0, float(settings.WRITING_SECTION_DIAGRAM_RATIO)))
    if ratio <= 0.0:
        return SectionDiagramRolloutDecision(False, "diagram_bucket")
    if ratio >= 1.0:
        return SectionDiagramRolloutDecision(True, "diagram_on")
    digest = hashlib.sha256(
        f"writing-section-diagram:{project_id}".encode("utf-8")
    ).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    if bucket < ratio:
        return SectionDiagramRolloutDecision(True, "diagram_on")
    return SectionDiagramRolloutDecision(False, "diagram_bucket")


__all__ = ["SectionDiagramRolloutDecision", "decide_section_diagram_rollout"]
