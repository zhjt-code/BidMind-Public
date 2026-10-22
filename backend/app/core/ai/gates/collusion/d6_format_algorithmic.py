"""B4.9 D6FormatAlgorithmicGate — 文档格式一致性算法判定。

对比两份投标文件的目录结构、字体族、页边距，
扣除招标文件白名单命中部分后判定格式相似度。
本批次 advisory 起步，不覆写 LLM D6 结果。
"""
from __future__ import annotations

from typing import Any

from app.core.ai.gates.base import Gate, GateContext, GateVerdict

# 边距容差 (mm)
_MARGIN_TOLERANCE_MM = 1.0


def _join_headings(headings: list[str]) -> str:
    return " | ".join(h.strip() for h in headings if isinstance(h, str))


def _margin_diff_ok(margins_a: dict[str, float], margins_b: dict[str, float]) -> bool:
    """四个边距差均 <= 1mm 视为相同。"""
    for key in ("top", "bottom", "left", "right"):
        va = margins_a.get(key)
        vb = margins_b.get(key)
        if va is None or vb is None:
            return False
        if abs(float(va) - float(vb)) > _MARGIN_TOLERANCE_MM:
            return False
    return True


def _margins_match_whitelist(
    margins_a: dict[str, float],
    margins_b: dict[str, float],
    required_margins: dict[str, float],
) -> bool:
    """两份文件的边距都与招标要求相符时，视为白名单命中。"""
    for key in ("top", "bottom", "left", "right"):
        req = required_margins.get(key)
        if req is None:
            continue
        va = margins_a.get(key)
        vb = margins_b.get(key)
        if va is None or vb is None:
            return False
        if (
            abs(float(va) - float(req)) > _MARGIN_TOLERANCE_MM
            or abs(float(vb) - float(req)) > _MARGIN_TOLERANCE_MM
        ):
            return False
    return True


class D6FormatAlgorithmicGate(Gate):
    """D6 文档格式算法判定：目录 + 字体 + 边距 + 页眉页脚相似度。"""

    gate_name = "D6FormatAlgorithmicGate"
    flow = "collusion"
    stage = "post_llm"
    order = 60
    default_severity = "advisory"
    description = "D6 文档格式算法判定"

    def applies(self, ctx: GateContext) -> bool:
        return ctx.flow == "collusion" and ctx.stage == "post_llm"

    def evaluate(self, ctx: GateContext) -> GateVerdict:  # noqa: C901
        evidence = ctx.evidence or {}
        struct_a: dict[str, Any] | None = evidence.get("docx_structure_a")
        struct_b: dict[str, Any] | None = evidence.get("docx_structure_b")

        if not struct_a or not struct_b:
            # P2-2: 严格模式下，结构缺失意味着 D6 LLM 分数缺乏算法兜底；按
            # Golden Rule 4 "宁漏报不错报"，强制把 LLM D6 certainty 压到 ≤0.3
            # （违反三级证据"辅助参考"定位时能自动降级），关闭时维持原 pass 兼容行为
            try:
                from app.core.collusion.feature_flags import use_d6_strict_mode
                project_id = (ctx.config or {}).get("project_id")
                if use_d6_strict_mode(project_id):
                    return GateVerdict(
                        gate_name=self.gate_name,
                        verdict="warn",
                        severity=self.default_severity,
                        reason="结构信息不可用，严格模式下 D6 certainty 上限 0.3",
                        suggested_patch={"d6_format": {"certainty_cap": 0.3}},
                        confidence=0.3,
                    )
            except Exception:
                # 灰度模块加载失败时保持原行为
                pass

            return GateVerdict(
                gate_name=self.gate_name,
                verdict="pass",
                severity=self.default_severity,
                reason="结构信息不可用",
                confidence=0.0,
            )

        whitelist: dict[str, Any] = evidence.get("tender_format_whitelist") or {}

        findings: list[str] = []
        d6_score = 0.0

        # ── 1. 目录相似度 ──────────────────────────────────────────────
        toc_a = _join_headings(struct_a.get("toc_headings") or [])
        toc_b = _join_headings(struct_b.get("toc_headings") or [])
        toc_signal = 0.0
        if toc_a and toc_b:
            from rapidfuzz import fuzz
            toc_ratio = fuzz.ratio(toc_a, toc_b)
            if toc_ratio >= 90:
                toc_signal = 0.3
                findings.append(f"目录结构高度相似 (fuzz={toc_ratio:.0f})")

        # ── 2. 字体族 ──────────────────────────────────────────────────
        fonts_a: set[str] = {f.strip() for f in (struct_a.get("font_families") or []) if f}
        fonts_b: set[str] = {f.strip() for f in (struct_b.get("font_families") or []) if f}
        font_signal = 0.0
        if fonts_a and fonts_b and fonts_a == fonts_b:
            font_signal = 0.25
            # 白名单扣除：招标要求了相同字体，属于正常合规
            required_fonts: set[str] = {
                f.strip()
                for f in (whitelist.get("required_fonts") or [])
                if f
            }
            if required_fonts and required_fonts.issuperset(fonts_a):
                font_signal = 0.0
                findings.append("字体族相同（均为招标规定字体，白名单扣除）")
            else:
                findings.append(f"字体族完全相同: {sorted(fonts_a)}")

        # ── 3. 页边距 ──────────────────────────────────────────────────
        margins_a: dict[str, float] = struct_a.get("margins") or {}
        margins_b: dict[str, float] = struct_b.get("margins") or {}
        margin_signal = 0.0
        if margins_a and margins_b and _margin_diff_ok(margins_a, margins_b):
            margin_signal = 0.25
            required_margins: dict[str, float] = whitelist.get("required_margins") or {}
            if required_margins and _margins_match_whitelist(
                margins_a, margins_b, required_margins
            ):
                margin_signal = 0.0
                findings.append("页边距相同（符合招标规定，白名单扣除）")
            else:
                findings.append(
                    f"页边距几乎一致: A={margins_a} B={margins_b}"
                )

        # ── 4. 页眉页脚 ────────────────────────────────────────────────
        hf_a = sorted(struct_a.get("headers_footers") or [])
        hf_b = sorted(struct_b.get("headers_footers") or [])
        hf_signal = 0.0
        if hf_a and hf_b and hf_a == hf_b:
            hf_signal = 0.1
            findings.append(f"页眉页脚完全相同: {hf_a}")

        d6_score = toc_signal + font_signal + margin_signal + hf_signal
        d6_score = round(min(1.0, d6_score), 4)

        verdict: str
        reason: str
        if d6_score >= 0.5:
            verdict = "warn"
            reason = f"D6 格式相似度高 (score={d6_score:.3f}): {'; '.join(findings)}"
        else:
            verdict = "pass"
            reason = f"D6 格式相似度低 (score={d6_score:.3f})"

        # 同源契约: DIMENSION_EVIDENCE_LEVEL_CONTRACT["d6_format"] == "third"。
        # Gate 直接产出契约值，避免依赖 analyzer._normalize_dim_evidence_levels 事后兜底，
        # 防止未来 advisory 升级或 patch 策略变化时把一级证据语义错误传播下去。
        patch = {
            "d6_format": {
                "score": d6_score,
                "findings": findings,
                "evidence_level": "third",
                "certainty": 0.9,
                "source": "algorithmic",
            }
        }

        return GateVerdict(
            gate_name=self.gate_name,
            verdict=verdict,
            severity=self.default_severity,
            reason=reason,
            suggested_patch=patch,
            confidence=0.9,
            trace={
                "toc_signal": toc_signal,
                "font_signal": font_signal,
                "margin_signal": margin_signal,
                "hf_signal": hf_signal,
            },
        )
