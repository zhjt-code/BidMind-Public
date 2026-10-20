# -*- coding: utf-8 -*-
"""评分结构提取产物契约(领域无关, 未落库)。

职责: 承载"读 scoring_table chunk → 解析/去重 → 卷定义回填 → verify_score_totals"
      的完整产物 dataclass 定义(ScoringStructureOutcome), 供审查侧落库
      (ScoringExtractor)与写标侧落库(WritingClause)共用同一提取核心。
      旧导入路径 `scoring_extractor.ScoringStructureOutcome` 由
      scoring_extractor.py 重导出保持兼容。
不做: 不承载任何 ORM 实体、不携带落库状态、不实现提取/候选事务逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclasses_field

from app.core.extractor.scoring.score_totals import ScoreTotalsReport
from app.core.extractor.scoring.scoring_facts import ScoringFactSet
from app.core.extractor.scoring.scoring_fidelity import FidelityAnnotation, FidelityKey
from app.schemas.requirement import ExtractedScoringItem


@dataclass(frozen=True)
class ScoringStructureOutcome:
    """评分结构提取产物（领域无关，未落库）。

    职责: 承载"读 scoring_table chunk → 解析/去重 → 卷定义回填 → 合计校验"的完整产物，
          供审查侧落库 Requirement 与写标侧落库 WritingClause 共用同一提取核心。
    不做: 不承载任何 ORM 实体、不携带落库状态。

    fidelity_sidecar: LLM 兜底路径的忠实性标注，键为 item.description[:60]，
                      供 _save_items 写入 scoring_metadata。确定性路径无此标注。
    """

    items: list[ExtractedScoringItem]
    volume_defs: dict
    report: ScoreTotalsReport
    chunk_count: int = 0
    fidelity_sidecar: dict[FidelityKey, FidelityAnnotation] = dataclasses_field(default_factory=dict)
    parse_diagnostics: list[str] = dataclasses_field(default_factory=list)
    fact_set: ScoringFactSet | None = None
    # Fix-6: L3 诊断（仅元数据，无原文）供下游消费，不在内部丢弃
    l3_diagnostics: list = dataclasses_field(default_factory=list)
    # 阶段2 VLM-first 裁决探针(build_verdict_probe 产物, 审查/写标落库到 Document)
    verdict_probe: dict | None = None


__all__ = ["ScoringStructureOutcome"]
