"""CrossDimensionHintGate — 跨维度证据共享提示（B3.4, post_pipeline）。

迁移自 analyzer.py ``_cross_dimension_evidence_share``。
保持 advisory：仅追加提示 finding，不修改已有 finding 或 score。

幂等约束：同一 ``compare_result`` 上重复触发本 Gate 不会产生重复 hint finding，
通过识别 ``summary`` 中的 "关联提示: D1" / "关联提示: D3" 前缀去重。
"""

from __future__ import annotations

from typing import Any

from app.core.ai.gates.base import Gate, GateContext, GateVerdict

# 用于幂等去重的 summary 前缀，集中常量避免散落字符串漂移
_HINT_PREFIX_D1_TO_D2 = "关联提示: D1人员重叠高分"
_HINT_PREFIX_D3_TO_D7 = "关联提示: D3文本雷同高分"


def _has_hint(findings: Any, prefix: str) -> bool:
    """判断 findings 列表中是否已存在以 ``prefix`` 起始的 hint finding。"""
    if not isinstance(findings, list):
        return False
    for f in findings:
        if isinstance(f, dict):
            summary = f.get("summary")
            if isinstance(summary, str) and summary.startswith(prefix):
                return True
    return False


class CrossDimensionHintGate(Gate):
    """跨维度证据共享：强势维度的证据线索注入弱势维度作为辅助提示。"""

    gate_name = "CrossDimensionHintGate"
    flow = "collusion"
    stage = "post_pipeline"
    order = 10
    default_severity = "advisory"
    description = "跨维度证据共享提示（D1→D2, D3→D7）"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "collusion" or ctx.stage != "post_pipeline":
            return False
        return isinstance(ctx.target, dict)

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        target = ctx.target
        hints_added = 0

        d1 = target.get("d1_personnel")
        d2 = target.get("d2_company")
        d3 = target.get("d3_text")
        d7 = target.get("d7_abnormal")

        d1_score = d1.get("score", 0) if isinstance(d1, dict) else 0
        d2_score = d2.get("score", 0) if isinstance(d2, dict) else 0
        d3_score = d3.get("score", 0) if isinstance(d3, dict) else 0
        d7_score = d7.get("score", 0) if isinstance(d7, dict) else 0

        if d1_score > 0.5 and d2_score == 0:
            if not isinstance(d2, dict):
                # D2 缺位时按 DIMENSION_EVIDENCE_LEVEL_CONTRACT["d2_company"] == "first"
                # 初始化骨架，与 schema 契约保持一致
                target["d2_company"] = {
                    "score": 0, "findings": [],
                    "evidence_level": "first", "certainty": 0.0,
                }
                d2 = target["d2_company"]
            d2_findings = d2.setdefault("findings", [])
            if not _has_hint(d2_findings, _HINT_PREFIX_D1_TO_D2):
                d2_findings.append({
                    "summary": f"{_HINT_PREFIX_D1_TO_D2}，建议关注企业关联",
                    "detail": (
                        f"D1人员重叠得分{d1_score:.2f}，存在关键人员跨标出现的情况，"
                        f"可能暗示企业间存在未被直接识别的关联关系。"
                    ),
                    "quote_a": None, "page_a": None,
                    "quote_b": None, "page_b": None,
                    "similarity": None,
                })
                hints_added += 1

        if d3_score > 0.5 and d7_score == 0:
            if not isinstance(d7, dict):
                # D7 契约为 third，与 DIMENSION_EVIDENCE_LEVEL_CONTRACT 对齐
                target["d7_abnormal"] = {
                    "score": 0, "findings": [],
                    "evidence_level": "third", "certainty": 0.0,
                }
                d7 = target["d7_abnormal"]
            d7_findings = d7.setdefault("findings", [])
            if not _has_hint(d7_findings, _HINT_PREFIX_D3_TO_D7):
                d7_findings.append({
                    "summary": f"{_HINT_PREFIX_D3_TO_D7}，建议关注异常一致性",
                    "detail": (
                        f"D3文本雷同得分{d3_score:.2f}，投标文件存在大段相似内容，"
                        f"可能伴随格式/排版/错别字等异常一致性模式。"
                    ),
                    "quote_a": None, "page_a": None,
                    "quote_b": None, "page_b": None,
                    "similarity": None,
                })
                hints_added += 1

        verdict = "warn" if hints_added > 0 else "pass"
        return GateVerdict(
            gate_name=self.gate_name,
            verdict=verdict,
            severity=self.default_severity,
            reason=f"cross_dimension_hint: {hints_added} hint(s) added",
            confidence=1.0,
        )
