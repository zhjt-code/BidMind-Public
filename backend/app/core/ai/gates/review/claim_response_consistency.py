"""ClaimResponseConsistencyGate — claim-response 一致性校验（B3.6 / B3.8 / S.2.3）。

迁移自 reviewer.py ``_apply_claim_response_check``。
S.2.3 决策 D-024：default_severity 由 advisory 升级为 enforce，解除"有 Gate 无效果"
问题（Pipeline._apply_patches 对 advisory patch 直接跳过）；Lite 环境沿用偏离 #008
豁免 2 周 advisory 观察期口径。回滚仅需 GATE_SEVERITY_OVERRIDES env 覆盖。
：词表补充"未明确/无具体/未量化"类否认措辞；实质响应下限 20→5 对齐
ExtractedBidResponse.content min_length；冲突 patch 只转 needs_review，不再改写
compliance_level/score（闸门不得替 LLM 提升符合度）。
：意见含否认词且证据包/挂载 chunk 非空即 applies；冲突只转 needs_review，
不把符合度改成 fully。
：意见原文保留，检索命中只写后缀/系统提示，禁止句内替换否认词。
"""

from __future__ import annotations

from typing import Any

from app.core.ai.gates.base import Gate, GateContext, GateVerdict

_NOT_PROVIDED_KEYWORDS = (
    "未提供", "未响应", "缺失", "缺少", "未找到", "未包含",
    "没有提供", "未附", "未见", "未出具",
    # "未明确具体有效期时长"类否认措辞此前不在词表，冲突直接放行
    "未明确", "无具体", "未量化", "未说明", "未体现", "未载明",
)

# 与 ExtractedBidResponse.content 的 min_length=5 对齐；
# 旧下限 20 使"投标有效期：开标之日起90日历天"(16字)这类短而明确的事实响应被放行。
_MIN_SUBSTANTIAL_LEN = 5


def _comment_has_denial(comment: str) -> bool:
    return any(keyword in (comment or "") for keyword in _NOT_PROVIDED_KEYWORDS)


def _pack_text(evidence: dict[str, Any]) -> str:
    pack = str(evidence.get("evidence_pack_text") or "").strip()
    if pack:
        return pack
    if evidence.get("has_response"):
        return str(evidence.get("response_content") or "").strip()
    return ""


def _append_hit_overlay(comment: str) -> str:
    """
    职责: 在审查意见原文后追加检索命中待复核注记
    不做: 不改写句内措辞、不改符合度
    """
    body = comment or ""
    if "检索已命中" in body or "符合性待复核" in body:
        return body
    return body.rstrip("。；; ") + "。检索已命中，符合性待复核。"


class ClaimResponseConsistencyGate(Gate):
    """LLM 声称未提供但证据包有实质内容时，转人工复核；不改写意见正文。"""

    gate_name = "ClaimResponseConsistencyGate"
    flow = "investigate"
    stage = "post_llm"
    order = 40
    default_severity = "enforce"
    description = "claim-response 一致性校验（推广；S.2.3 升 enforce）"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "investigate" or ctx.stage != "post_llm":
            return False
        comment = getattr(ctx.target, "comment", "") or ""
        if not _comment_has_denial(comment):
            return False
        return len(_pack_text(ctx.evidence or {})) >= _MIN_SUBSTANTIAL_LEN

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        target = ctx.target
        evidence = ctx.evidence or {}

        comment: str = getattr(target, "comment", "") or ""
        has_not_provided = _comment_has_denial(comment)
        if not has_not_provided:
            return GateVerdict(
                gate_name=self.gate_name, verdict="pass",
                severity=self.default_severity,
                reason="no 'not provided' claim in comment",
            )

        pack = _pack_text(evidence)
        if len(pack) < _MIN_SUBSTANTIAL_LEN:
            return GateVerdict(
                gate_name=self.gate_name, verdict="pass",
                severity=self.default_severity,
                reason="no substantial evidence pack",
            )

        overlay = _append_hit_overlay(comment)
        hint = (
            " [系统提示: 投标文件中检测到相关响应内容"
            f"(长度{len(pack)}字), 请核验是否确实未响应]"
        )
        if "系统提示" in overlay:
            new_comment = overlay
        else:
            new_comment = overlay + hint if len(overlay) + len(hint) <= 1000 else overlay
        # 只转人工复核，不写 compliance_level/score
        patch: dict[str, Any] = {
            "comment": new_comment,
            "failure_state": "needs_review",
        }

        return GateVerdict(
            gate_name=self.gate_name,
            verdict="enforce_patch",
            severity=self.default_severity,
            reason=f"claim-response conflict: LLM says not provided but "
                   f"pack has {len(pack)} chars",
            suggested_patch=patch,
            confidence=0.9,
        )
