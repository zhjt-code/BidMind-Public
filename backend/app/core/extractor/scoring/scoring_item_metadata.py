"""评分项 scoring_metadata 字段清单单一构建点（换轴重构 P3-1 抽取）。

职责: 将 ExtractedScoringItem 转换为落库 scoring_metadata 字典——字段清单在此
      单点维护（红色清单"评分层级字段全链传播"的生产侧收口）。
不做: 不访问 DB、不做去重/归并、不推断分类或卷别、不写 Requirement。
"""
from __future__ import annotations

from typing import Any

from app.schemas.requirement import ExtractedScoringItem


def build_scoring_item_metadata(
    item: ExtractedScoringItem,
    *,
    effective_weight: float | None,
    totals_needs_review: bool,
    suspected_assessment_form: bool = False,
) -> dict[str, Any]:
    """构建单条评分项的 scoring_metadata 字典。

 字段语义:
 - category_basis(P3-1): 分类依据(关键词/llm/manual); None=零命中未分类;
 - scheme: 多套百分制文档按方案分组核计;
 - fact_type(阶段3): base_score/bonus/penalty/price_formula, 调整项不计基础满分;
 - volume_weight/volume_max: 按卷加权折算基准。
 - suspected_assessment_form: 仅 True 时写入，供确认页建议排除。
 """
    meta: dict[str, Any] = {
        "score_item_name": item.score_item_name,
        "max_score": effective_weight,
        "score_rules": item.score_rules,
        "deduction_rules": item.deduction_rules,
        "category_basis": getattr(item, "category_basis", None),
        "volume": item.volume,
        "volume_label": getattr(item, "volume_label", None),
        "lot": getattr(item, "lot", None),
        "scheme": getattr(item, "scheme", None),
        "fact_type": getattr(item, "fact_type", None) or "base_score",
        "volume_weight": item.volume_weight,
        "volume_max": item.volume_max,
        "totals_needs_review": totals_needs_review,
    }
    if suspected_assessment_form:
        meta["suspected_assessment_form"] = True
    return meta


__all__ = ["build_scoring_item_metadata"]
