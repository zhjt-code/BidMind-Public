"""串标审查 修复迭代的灰度开关判定

对应 `docs/bug_tracking.md` / 计划文档中 P0/P1/P2 每期引入的 feature flag，
采用"开关 + 百分比"双层控制：开关为 False 直接禁用；为 True 且 ratio >= 1
直接启用；否则按 project_id 做 md5 哈希 % 100 灰度判定（与
`should_use_gate_pipeline` 语义一致）。

project_id 为 None 或 ratio <= 0 直接视为未开启，避免未绑定 project 的
调用误入灰度实验路径。
"""

from __future__ import annotations

import hashlib

from app.core.config import settings


def _hash_bucket(project_id: int | str | None) -> int | None:
    """返回 0-99 的稳定哈希桶；project_id 为空返回 None。"""
    if project_id is None:
        return None
    try:
        raw = str(project_id).strip()
        if not raw:
            return None
        return int(hashlib.md5(raw.encode()).hexdigest(), 16) % 100
    except Exception:
        return None


def _gated(enabled: bool, ratio: float, project_id: int | str | None) -> bool:
    if not enabled:
        return False
    if ratio >= 1.0:
        return True
    if ratio <= 0.0:
        return False
    bucket = _hash_bucket(project_id)
    if bucket is None:
        return False
    return bucket < int(ratio * 100)


def use_fused_evidence_to_llm(project_id: int | str | None) -> bool:
    """P0-1: 是否让 S4 群组研判 LLM 消费 S3.6 fused_evidence。"""
    return _gated(
        settings.COLLUSION_FUSED_EVIDENCE_TO_LLM,
        settings.COLLUSION_FUSED_EVIDENCE_TO_LLM_RATIO,
        project_id,
    )


def use_global_findings_enrich(project_id: int | str | None) -> bool:
    """P0-2/P0-3: 是否在项目级综合 Prompt 注入 global_findings_context，
    同时在 /results API 暴露 D4/D8 全局发现。"""
    return _gated(
        settings.COLLUSION_GLOBAL_FINDINGS_ENRICH,
        settings.COLLUSION_GLOBAL_FINDINGS_ENRICH_RATIO,
        project_id,
    )


def use_d4_full_coverage(project_id: int | str | None) -> bool:
    """P1-1: 是否启用 D4 报价异常的全链路覆盖（筛对 + 算法对比）。"""
    return _gated(
        settings.COLLUSION_D4_FULL_COVERAGE,
        settings.COLLUSION_D4_FULL_COVERAGE_RATIO,
        project_id,
    )


def use_d6_strict_mode(project_id: int | str | None) -> bool:
    """P2-2: 是否启用 D6 Gate 严格模式（结构缺失时下发 certainty_cap）。"""
    return _gated(
        settings.COLLUSION_D6_STRICT_MODE,
        settings.COLLUSION_D6_STRICT_MODE_RATIO,
        project_id,
    )
