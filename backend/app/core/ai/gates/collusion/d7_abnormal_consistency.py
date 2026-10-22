"""B4.10 D7AbnormalConsistencyGate — 异常一致性算法判定。

LCS 滑动窗口连续文本块检测 + 相同拼写错误检测。
本批次 advisory 起步。
"""
from __future__ import annotations

import re

from app.core.ai.gates.base import Gate, GateContext, GateVerdict

# 每个滑动窗口字符数
_WINDOW_SIZE = 200
# 滑动步长（50% 重叠）
_WINDOW_STEP = 100
# LCS 高相似阈值
_LCS_RATIO_THRESHOLD = 90
# 激活 lcs_signal 所需的最低窗口长度（字符）
_LCS_MIN_WINDOW_LEN = 200

# 常见行业错别字对 (正确写法, 错误写法)
_TYPO_PAIRS: list[tuple[str, str]] = [
    ("投标", "头标"),
    ("中标", "中彪"),
    ("招标", "招彪"),
    ("保证金", "保正金"),
    ("资质", "资制"),
    ("合同", "和同"),
    ("竣工", "峻工"),
    ("验收", "验受"),
    ("履约", "律约"),
    ("预算", "于算"),
    ("决算", "结算"),
    ("监理", "坚理"),
    ("施工", "实工"),
    ("竞标", "竞彪"),
    ("评标", "评彪"),
]

# 常见公文样板短语（过滤掉它们避免误判）
_BOILERPLATE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"投标人名称[:：].*"),
    re.compile(r"项目名称[:：].*"),
    re.compile(r"招标编号[:：].*"),
    re.compile(r"日期[:：].*"),
    re.compile(r"\s+"),
]


def _strip_boilerplate(text: str) -> str:
    """去除公文样板短语并压缩空白。"""
    for pat in _BOILERPLATE_PATTERNS:
        text = pat.sub(" ", text)
    return text.strip()


def _sliding_windows(text: str, size: int, step: int) -> list[str]:
    windows = []
    for i in range(0, max(1, len(text) - size + 1), step):
        windows.append(text[i : i + size])
    return windows


def _detect_lcs_signal(
    text_a: str, text_b: str
) -> tuple[float, str | None]:
    """滑动窗口最大 fuzz.ratio；返回 (signal, 最优窗口片段 or None)。"""
    clean_a = _strip_boilerplate(text_a)
    clean_b = _strip_boilerplate(text_b)

    if len(clean_a) < _LCS_MIN_WINDOW_LEN or len(clean_b) < _LCS_MIN_WINDOW_LEN:
        return 0.0, None

    windows_a = _sliding_windows(clean_a, _WINDOW_SIZE, _WINDOW_STEP)
    windows_b = _sliding_windows(clean_b, _WINDOW_SIZE, _WINDOW_STEP)

    best_ratio = 0
    best_snippet: str | None = None

    from rapidfuzz import fuzz
    for wa in windows_a:
        for wb in windows_b:
            r = fuzz.ratio(wa, wb)
            if r > best_ratio:
                best_ratio = r
                best_snippet = wa[:80] + "…"

    if best_ratio >= _LCS_RATIO_THRESHOLD:
        return 0.5, best_snippet
    return 0.0, None


def _detect_typo_signal(
    text_a: str, text_b: str
) -> tuple[float, list[str]]:
    """检测两份文本中共同出现的拼写错误。"""
    hits: list[str] = []
    for correct, wrong in _TYPO_PAIRS:
        if wrong in text_a and wrong in text_b:
            hits.append(f"共同错别字「{wrong}」(应为「{correct}」)")

    signal = min(0.6, len(hits) * 0.3)
    return signal, hits


class D7AbnormalConsistencyGate(Gate):
    """D7 异常一致性：LCS 文本块 + 共同拼写错误算法判定。"""

    gate_name = "D7AbnormalConsistencyGate"
    flow = "collusion"
    stage = "post_llm"
    order = 70
    default_severity = "advisory"
    description = "D7 异常一致性算法判定"

    def applies(self, ctx: GateContext) -> bool:
        return ctx.flow == "collusion" and ctx.stage == "post_llm"

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        evidence = ctx.evidence or {}
        text_a: str = evidence.get("text_a") or ""
        text_b: str = evidence.get("text_b") or ""

        if not text_a.strip() or not text_b.strip():
            return GateVerdict(
                gate_name=self.gate_name,
                verdict="pass",
                severity=self.default_severity,
                reason="文本不可用",
                confidence=0.0,
            )

        findings: list[str] = []

        lcs_signal, lcs_snippet = _detect_lcs_signal(text_a, text_b)
        if lcs_signal > 0 and lcs_snippet:
            findings.append(f"检测到 ≥{_WINDOW_SIZE}字连续高相似文本块: 「{lcs_snippet}」")

        typo_signal, typo_hits = _detect_typo_signal(text_a, text_b)
        findings.extend(typo_hits)

        d7_score = round(min(1.0, lcs_signal + typo_signal), 4)

        verdict: str
        reason: str
        if d7_score >= 0.3:
            verdict = "warn"
            reason = f"D7 异常一致性信号 (score={d7_score:.3f}): {'; '.join(findings)}"
        else:
            verdict = "pass"
            reason = f"D7 异常一致性低 (score={d7_score:.3f})"

        patch = {
            "d7_abnormal": {
                "score": d7_score,
                "findings": findings,
                "evidence_level": "second",
                "certainty": 0.8,
                "source": "algorithmic",
            }
        }

        return GateVerdict(
            gate_name=self.gate_name,
            verdict=verdict,
            severity=self.default_severity,
            reason=reason,
            suggested_patch=patch,
            confidence=0.8,
            trace={
                "lcs_signal": lcs_signal,
                "typo_signal": typo_signal,
                "typo_hits": typo_hits,
            },
        )
