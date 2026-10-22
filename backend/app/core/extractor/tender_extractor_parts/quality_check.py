"""TenderExtractor 质量检查 + 复合要求拆分 + 业绩对齐阶段（R2-2 拆分产出）。

承接原 `tender_extractor.py` 中以下 4 个非平凡方法：
- `_check_quality`         本地质量检查（关键词阈值 + 跨类相似度）
- `_infer_facet_scope`     子面 → subject_scope 映射查表
- `_split_compound_requirements`  复合要求按子面拆分
- `_apply_performance_alignment`  业绩双归因 + canonical_key 补齐 + 主项选择

保留在主模块：
- `_infer_subject_scope` / `_infer_topic` / `_extract_fact_signature` /
  `_infer_canonical_key` 4 个 1-line class 级 thin delegate
- `_COMPOUND_SPLIT_RULES` / `_FACET_SCOPE_MAP` 2 个 ClassVar 字典（兼容 monkey patch）
- 模块级 `infer_subject_scope` / `infer_topic` / `extract_fact_signature` /
  `infer_canonical_key` 4 个函数（[requirement_service.py:329] lazy import 锚点）

设计：函数第一参数 `self` 保留，函数名相对原 method **去前导下划线**，
body 与原 method **字面一致**（self.xxx 引用通过主类 thin forwarder 触发）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from app.schemas.requirement import ExtractedRequirement

if TYPE_CHECKING:
    from app.core.extractor.tender_extractor import TenderExtractor


def check_quality(
    self: "TenderExtractor",
    requirements: list[ExtractedRequirement],
) -> list[dict]:
    """本地质量检查：检测重复关键词

    Args:
        requirements: 要求列表

    Returns:
        list[dict]: 问题列表
    """
    issues = []

    keyword_counts: dict[str, list[int]] = {}
    for i, req in enumerate(requirements):
        for kw in (req.keywords or []):
            kw_lower = kw.lower()
            if kw_lower not in keyword_counts:
                keyword_counts[kw_lower] = []
            keyword_counts[kw_lower].append(i)

    thresholds = {
        "保证金": 3,
        "营业执照": 2,
        "资质": 3,
        "报价": 4,
        "价格": 4,
        "项目经理": 2,
        "业绩": 3,
    }

    for kw, threshold in thresholds.items():
        matching_indices = []
        for stored_kw, indices in keyword_counts.items():
            if kw in stored_kw or stored_kw in kw:
                matching_indices.extend(indices)

        matching_indices = list(set(matching_indices))
        if len(matching_indices) > threshold:
            issues.append({
                "type": "keyword_excess",
                "description": f"关键词'{kw}'出现{len(matching_indices)}次，超过阈值{threshold}",
                "affected_indices": matching_indices,
                "keyword": kw
            })

    for i, req1 in enumerate(requirements):
        for j, req2 in enumerate(requirements):
            if i >= j:
                continue
            if req1.category != req2.category:
                if self._is_similar(req1.description.lower(), req2.description.lower(), 0.6):
                    issues.append({
                        "type": "cross_category_duplicate",
                        "description": f"跨类别重复: [{req1.category}]{req1.description[:30]}... vs [{req2.category}]{req2.description[:30]}...",
                        "affected_indices": [i, j]
                    })

    return issues


def infer_facet_scope(self: "TenderExtractor", facet_name: str) -> str:
    """根据子面名推断主体范围。"""
    return self._FACET_SCOPE_MAP.get(facet_name, "generic")


def split_compound_requirements(
    self: "TenderExtractor",
    requirements: list[ExtractedRequirement],
) -> list[ExtractedRequirement]:
    """检测复合要求并按子面拆分。

    当一条 requirement 的描述同时命中 >=2 个语义子面时，拆分为多条
    子 requirement，每条保留原始描述但标注各自的 subject_scope。
    拆分在人工确认前执行，用户可在确认页面调整。
    """
    result: list[ExtractedRequirement] = []
    for req in requirements:
        # P1: 非 actionable（信息告知/程序履约）条款不再复合拆分，避免放大确认页噪音
        if getattr(req, "review_relevance", "actionable") != "actionable":
            result.append(req)
            continue
        rules = self._COMPOUND_SPLIT_RULES.get(req.category, {})
        matched_facets = [
            (name, kws)
            for name, kws in rules.items()
            # P1: comm_contract（合同条款子面）不参与拆分，合同类条款不应被放大成多行
            if name != "comm_contract" and any(kw in req.description for kw in kws)
        ]
        if len(matched_facets) <= 1:
            result.append(req)
            continue

        logger.info(
            f"复合要求拆分: category={req.category} "
            f"facets={[f[0] for f in matched_facets]} "
            f"desc={req.description[:60]}"
        )
        for facet_name, _ in matched_facets:
            sub = req.model_copy(deep=True)
            sub.sub_requirements = [facet_name]
            sub.subject_scope = self._infer_facet_scope(facet_name)
            sub.canonical_key = None
            result.append(sub)
    return result


def apply_performance_alignment(
    self: "TenderExtractor",
    requirements: list[ExtractedRequirement],
) -> list[ExtractedRequirement]:
    """补齐 topic/subject_scope/canonical_key/main_requirement_id。"""
    if not requirements:
        return requirements

    for req in requirements:
        req.topic = req.topic or self._infer_topic(req.description, req.keywords)
        req.subject_scope = req.subject_scope or self._infer_subject_scope(req.description, req.keywords)
        req.canonical_key = req.canonical_key or self._infer_canonical_key(req.description, req.keywords)

    group_best: dict[str, ExtractedRequirement] = {}
    for req in requirements:
        key = req.canonical_key or "unknown"
        if key not in group_best or len(req.description) > len(group_best[key].description):
            group_best[key] = req

    for req in requirements:
        main = group_best.get(req.canonical_key or "")
        if main is req:
            req.main_requirement_id = None
        else:
            req.main_requirement_id = None

    return requirements
