"""SectionPlan feature flag helpers.

职责: 决定章节论证计划是否启用，并计算 compose 等待规划的 wait_for 秒数。
不做: 不读取业务库、不调用 LLM、不修改运行时配置。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class SectionPlanRolloutDecision:
    enabled: bool
    reason: str


def resolve_section_plan_wait_timeout_sec() -> float:
    """
    职责: 计算 compose_free 等待 SectionPlan 的 wait_for 秒数。
    不做: 不调用 LLM、不读写数据库、不决定是否灰度启用。
    """

    configured = float(getattr(settings, "WRITING_SECTION_PLAN_TIMEOUT_SEC", 0) or 0)
    cloud = float(getattr(settings, "CLOUD_TIMEOUT", 300.0) or 300.0)
    return max(configured, cloud)


def decide_section_plan_rollout(project_id: int | None) -> SectionPlanRolloutDecision:
    """Return stable rollout decision for a project."""

    if not settings.WRITING_SECTION_PLAN_ENABLED:
        return SectionPlanRolloutDecision(False, "legacy_flag_off")
    if project_id is None:
        return SectionPlanRolloutDecision(False, "legacy_no_project")
    ratio = max(0.0, min(1.0, float(settings.WRITING_SECTION_PLAN_RATIO)))
    if ratio <= 0.0:
        return SectionPlanRolloutDecision(False, "legacy_bucket")
    if ratio >= 1.0:
        return SectionPlanRolloutDecision(True, "planned")
    digest = hashlib.sha256(f"writing-section-plan:{project_id}".encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    if bucket < ratio:
        return SectionPlanRolloutDecision(True, "planned")
    return SectionPlanRolloutDecision(False, "legacy_bucket")


__all__ = [
    "SectionPlanRolloutDecision",
    "decide_section_plan_rollout",
    "resolve_section_plan_wait_timeout_sec",
]
