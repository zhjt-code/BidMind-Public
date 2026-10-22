"""C3 · D7ConcreteEvidenceGate — D7/D6 具体证据校验（拦截自相矛盾 finding）。

stage=post_llm, order=52（在 FindingPageQuoteVerifyGate=50 后、各算法对照 Gate 之前）。

历史背景：
- / 用户反馈截图 3："存在相同非标准表述'钢材配送方案'（应为'钢材配送方案'）"
 -- LLM 同时给出 quote_a 与 quote_b 完全一致的字符串作为"错别字"证据，自相矛盾。
- / 用户反馈截图 2："例如将'防腐'误写为'防府'（假设）" -- 推测词由 C2
 finding_polarity 拦截；但若 LLM 给出错别字 finding 且 quote_a==quote_b，
 即便没有推测词，也仍是无效证据。

本 Gate 设计契约：
- 仅校验 D6/D7 维度（D3 已有 D3TextAlgorithmicGate 算法对跑）
- D7 错别字/非标准表述 finding 必须满足：
 quote_a 与 quote_b 至少有一侧非空，且二者不应"完全相等且无差异"
 (否则不构成"差异点"，应转为推测性观察)
- D7 排版错误 finding 必须 detail 中提及具体编号或位置（如"第 30 页"/"图 3-2"）
- D6 格式异常 finding 必须 detail 中含具体格式特征关键词
 （目录/章节/编号/字体/行距/页边距/表格/标点 等）
- 命中违例的 finding 设 verified=False + rejection_reason，由后续证据组装层
 转入 attention_items.speculative_finding 或丢弃
- 本 Gate 不修改 score，只标 finding 级 verified；保持保守
"""

from __future__ import annotations

import re
from typing import Any

from app.core.ai.gates.base import Gate, GateContext, GateVerdict

# D6 finding.detail 必须含的具体格式特征关键词之一
_D6_CONCRETE_KEYWORDS = re.compile(
    r"(?:目录|章节|条款|编号|章序|节序|字体|字号|行距|段距|页边距|页眉|页脚"
    r"|页码|表格|表头|表线|缩进|标点|签章|装订|封面|扉页|宋体|黑体|楷体"
    r"|加粗|斜体|罗马数字|阿拉伯数字|阿拉伯字)"
)

# D7 排版错误 finding.detail 必须含的具体位置/编号关键词
_D7_LAYOUT_LOC_PATTERNS = re.compile(
    r"(?:第\s*\d+\s*页|图\s*\d|表\s*\d|\d+\.\d+|\d+-\d+|图表\s*\d)"
)


def _normalize_for_compare(text: str | None) -> str:
    if not text:
        return ""
    # 去除空白与中英文标点便于比对"形似"
    return re.sub(
        r"[\s\u3000·•・,，。.;；、:：\"'\"\"''()\[\]【】]+",
        "",
        str(text),
    ).lower()


def _is_typo_finding(finding: dict) -> bool:
    """判定该 finding 是否声称"错别字/非标准表述"类。"""
    summary = str(finding.get("summary") or "")
    detail = str(finding.get("detail") or "")
    blob = f"{summary} {detail}"
    return bool(re.search(r"错别字|非标准表述|拼写错误|术语错误", blob))


def _is_layout_finding(finding: dict) -> bool:
    """判定该 finding 是否声称"排版错误"类。"""
    summary = str(finding.get("summary") or "")
    detail = str(finding.get("detail") or "")
    blob = f"{summary} {detail}"
    return bool(re.search(r"排版错误|页码跳跃|图表编号错误|目录错误|编号错乱", blob))


def _quote_self_contradicts(finding: dict) -> bool:
    """quote_a / quote_b 同时给出但归一化后完全相同 → 无差异，自相矛盾。"""
    qa = finding.get("quote_a")
    qb = finding.get("quote_b")
    if not qa or not qb:
        return False
    nqa = _normalize_for_compare(qa)
    nqb = _normalize_for_compare(qb)
    if not nqa or not nqb:
        return False
    return nqa == nqb


class D7ConcreteEvidenceGate(Gate):
    """D6/D7 具体证据校验：拦截无差异/无具体特征的派生 finding。"""

    gate_name = "D7ConcreteEvidenceGate"
    flow = "collusion"
    stage = "post_llm"
    order = 52
    default_severity = "enforce"
    description = "D6/D7 具体证据校验（错别字必须有差异、排版/格式必须有具体特征）"

    def applies(self, ctx: GateContext) -> bool:
        return ctx.flow == "collusion" and ctx.stage == "post_llm"

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        target = ctx.target or {}
        rejected_count = 0
        traces: list[dict[str, Any]] = []

        # ── D7 ──
        d7 = target.get("d7_abnormal")
        if isinstance(d7, dict):
            for f in d7.get("findings") or []:
                if not isinstance(f, dict):
                    continue
                if f.get("verified") is False:
                    continue  # 已被前置 Gate 拒绝

                if _is_typo_finding(f):
                    qa = f.get("quote_a")
                    qb = f.get("quote_b")
                    # 错别字 finding 必须有 quote 证据
                    if not qa and not qb:
                        f["verified"] = False
                        f.setdefault(
                            "rejection_reason",
                            "声称错别字/非标准表述但未提供 quote_a/quote_b 证据",
                        )
                        rejected_count += 1
                        traces.append({"dim": "D7", "kind": "typo_no_quote"})
                        continue
                    # quote_a == quote_b 自相矛盾（声称有差异却给出相同字符）
                    if _quote_self_contradicts(f):
                        f["verified"] = False
                        f.setdefault(
                            "rejection_reason",
                            "声称错别字/非标准表述但 quote_a 与 quote_b 完全相同，"
                            "未呈现差异字符（自相矛盾）",
                        )
                        rejected_count += 1
                        traces.append({"dim": "D7", "kind": "typo_self_contradicts"})
                        continue

                if _is_layout_finding(f):
                    detail = str(f.get("detail") or "")
                    if not _D7_LAYOUT_LOC_PATTERNS.search(detail):
                        f["verified"] = False
                        f.setdefault(
                            "rejection_reason",
                            "声称排版错误但 detail 中未给出具体位置或编号"
                            "（如'第 30 页'/'图 3-2'）",
                        )
                        rejected_count += 1
                        traces.append({"dim": "D7", "kind": "layout_no_location"})
                        continue

        # ── D6 ──
        d6 = target.get("d6_format")
        if isinstance(d6, dict):
            for f in d6.get("findings") or []:
                if not isinstance(f, dict):
                    continue
                if f.get("verified") is False:
                    continue
                detail = str(f.get("detail") or "") + " " + str(f.get("summary") or "")
                if not _D6_CONCRETE_KEYWORDS.search(detail):
                    f["verified"] = False
                    f.setdefault(
                        "rejection_reason",
                        "D6 格式异常 finding 未命名具体格式特征"
                        "（如目录/编号/字体/行距/表格 等）",
                    )
                    rejected_count += 1
                    traces.append({"dim": "D6", "kind": "no_concrete_feature"})

        if rejected_count == 0:
            return GateVerdict(
                gate_name=self.gate_name,
                verdict="pass",
                severity=self.default_severity,
                reason="D6/D7 finding 均通过具体证据校验",
                confidence=1.0,
            )

        return GateVerdict(
            gate_name=self.gate_name,
            verdict="enforce_patch",
            severity=self.default_severity,
            reason=f"D6/D7 具体证据校验拒绝 {rejected_count} 条派生 finding",
            confidence=1.0,
            trace={"rejected_count": rejected_count, "items": traces},
        )
