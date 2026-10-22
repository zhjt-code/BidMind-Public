""" 工作包 2D: ClaimResponseConsistencyGate 收口防御测试。

约束三条修复行为:
1. 否认措辞词表覆盖"未明确/无具体/未量化"类表述;
2. 实质响应下限与 ExtractedBidResponse.content 的 min_length=5 对齐;
3. 冲突 patch 只转 needs_review, 不得写 compliance_level/score
 (闸门不替 LLM 提升符合度, 判定方向归人工复核)。
"""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.ai.gates.base import GateContext
from app.core.ai.gates.review.claim_response_consistency import (
    ClaimResponseConsistencyGate,
)

_GATE = ClaimResponseConsistencyGate()


def _ctx(comment: str, response_content: str, has_response: bool = True) -> GateContext:
    target = SimpleNamespace(
        compliance_level="not",
        comment=comment,
        score=0,
        is_compliant=False,
    )
    return GateContext(
        flow="investigate",
        stage="post_llm",
        target=target,
        evidence={
            "has_response": has_response,
            "response_content": response_content,
        },
    )


def test_validity_fact_with_no_specific_wording_routes_to_needs_review() -> None:
    """正向: 明确"90日历天"事实 vs "无具体响应内容"否认 → 转人工复核。

    与 Level3 RED 用例同场景不同措辞变体, 约束词表覆盖不止"未明确"单词。
    """
    verdict = _GATE.evaluate(_ctx(
        comment="投标文件中无具体响应内容，判定不符合。",
        response_content="投标有效期：开标之日起90日历天",
    ))
    assert verdict.verdict == "enforce_patch", (
        f"明确事实与否认结论冲突必须触发复核, 实际: {verdict.verdict} ({verdict.reason})"
    )
    patch = verdict.suggested_patch or {}
    assert patch.get("failure_state") == "needs_review"
    assert "compliance_level" not in patch, f"闸门不得自动升级符合度: {patch}"


def test_fragment_response_below_min_length_passes() -> None:
    """负向1: 响应仅"短"(<5字)属无实质内容, 闸门放行。"""
    verdict = _GATE.evaluate(_ctx(
        comment="投标人未提供相关证明材料，判定不符合。",
        response_content="短",
    ))
    assert verdict.verdict == "pass"
    assert verdict.suggested_patch is None or verdict.suggested_patch == {}


def test_qhse_partial_evidence_never_upgrades_compliance() -> None:
    """负向2(关键): 部分证据(许可证扫描件)不足以覆盖 QHSE 复合要求。

    允许闸门因检测到响应内容而转 needs_review, 但 patch 键集合不得包含
    compliance_level/score——"有响应"不等于"部分符合", 升级判定必须归人工。
    """
    verdict = _GATE.evaluate(_ctx(
        comment="未见防护设备设施配置及安全费用使用计划的具体内容，判定不符合",
        response_content="安全生产许可证扫描件（证书编号XXX）",
    ))
    patch = verdict.suggested_patch or {}
    if verdict.verdict == "enforce_patch":
        assert patch.get("failure_state") == "needs_review"
    assert "compliance_level" not in patch, f"闸门不得自动升级符合度: {patch}"
    assert "score" not in patch, f"闸门不得改写得分: {patch}"


def test_plain_noncompliance_comment_without_denial_passes() -> None:
    """无否认措辞的普通不符合意见不触发冲突检测。"""
    verdict = _GATE.evaluate(_ctx(
        comment="响应的质保期为一年，低于要求的两年，判定不符合。",
        response_content="质保期：自验收合格之日起一年",
    ))
    assert verdict.verdict == "pass"


def test_no_response_passes() -> None:
    """has_response=False 且无证据包时, 否认结论成立, 闸门放行。"""
    verdict = _GATE.evaluate(_ctx(
        comment="投标人未提供营业执照复印件，判定不符合。",
        response_content="[未找到响应]",
        has_response=False,
    ))
    assert verdict.verdict == "pass"


def test_evidence_pack_rewrites_missing_claim_without_upgrading() -> None:
    """包内有原文时不得定谳未提供, 意见原文保留, 且不得改写符合度。"""
    original = "投标人未提供营业执照复印件，判定不符合。"
    ctx = _ctx(
        comment=original,
        response_content="[未找到响应]",
        has_response=False,
    )
    ctx.evidence["evidence_pack_text"] = "营业执照扫描件（统一社会信用代码已载明）"
    verdict = _GATE.evaluate(ctx)
    assert verdict.verdict == "enforce_patch"
    patch = verdict.suggested_patch or {}
    rewritten = patch.get("comment") or ""
    assert rewritten.startswith(original.rstrip("。"))
    assert "未提供" in rewritten
    assert "已检索到相关材料" not in rewritten
    assert "待核验是否具备" not in rewritten
    assert "检索已命中" in rewritten
    assert patch.get("failure_state") == "needs_review"
    assert "compliance_level" not in patch
    assert "score" not in patch


if __name__ == "__main__":
    test_validity_fact_with_no_specific_wording_routes_to_needs_review()
    test_fragment_response_below_min_length_passes()
    test_qhse_partial_evidence_never_upgrades_compliance()
    test_plain_noncompliance_comment_without_denial_passes()
    test_no_response_passes()
    test_evidence_pack_rewrites_missing_claim_without_upgrading()
    print("ALL 6 TESTS PASSED")
