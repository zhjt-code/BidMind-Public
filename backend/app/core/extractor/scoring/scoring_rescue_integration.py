"""评分提取通道产物契约。

职责: 承载通道尾部产物 dataclass(ScoringRescueIntegrationResult), 供 pdf_direct
      主路径与 VLM 零chunk入口构造产物、共享轮次引擎(rescue_rounds)采纳替换。
不做: 不编排救援、不落库、不携带 ORM 实体。

阶段3(2026-08-31): legacy 尾部执行器(execute_scoring_structure_tail/
ScoringRescueIntegrationRequest/L2修补/L3全候选管线)已随确定性链删除。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoringRescueIntegrationResult:
    """通道尾部完整产物（不可变）。

    l3_diagnostics 仅含 scope/页范围/候选数/status/rejection/耗时，无文档原文。
    """
    items: list
    report: object  # ScoreTotalsReport
    fidelity_sidecar: dict
    diagnostics: list
    l3_diagnostics: list
    adopted: bool
    mode: str


__all__ = ["ScoringRescueIntegrationResult"]
