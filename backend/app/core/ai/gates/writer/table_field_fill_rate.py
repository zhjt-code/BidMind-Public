"""S.5.2 · TableFieldFillRateGate — 表格字段填充率守卫（B5.7 模板化后新增）。

stage=post_llm, default_severity=advisory, order=40。

背景（I-011 P3-2）：B5.7 data_fill 章节走 Jinja2 模板渲染时，若素材侧没有
结构化字段（`_extract_template_items` 目前用保守策略，多数字段默认空串），
会产出"格式对但内容空"的表格。这种 HTML 在视觉上看起来是表格，在重放测试中
也会显得稳定，但对用户无实际价值。

本 Gate 对 data_fill 章节做一次表格单元格非空率检查，低于阈值即 advisory
标记 `failure_state=needs_review` + `table_fill_rate=<float>` 到 target，
供 UI 渲染 "待补充" 橙标签。advisory 起步；若生产流量下拒绝率 >= 2% 稳定，
再考虑升 enforce（留给 P3+ 评估）。
"""

from __future__ import annotations

import re

from app.core.ai.gates.base import Gate, GateContext, GateVerdict

DEFAULT_MIN_FILL_RATE = 0.8
# 忽略的表头/序号类 td 内容，不计入"空/非空"统计（避免误判）
_IGNORED_TD_TEXT = re.compile(r"^\s*(?:[0-9]+|[0-9０-９]+\.?|-+|·+)\s*$")
# 匹配单个 <td>...</td> 的内容（非贪婪）；忽略大小写 & 跨行
_TD_RE = re.compile(r"<td[^>]*>([\s\S]*?)</td>", re.IGNORECASE)
# 去除 HTML 标签剩纯文本
_TAG_RE = re.compile(r"<[^>]+>")


def _extract_td_texts(content: str) -> list[str]:
    """从 HTML content 中抽取所有 <td> 的纯文本。非 HTML 输入返回空列表。"""
    if not content or "<td" not in content.lower():
        return []
    return [_TAG_RE.sub("", m.group(1)).strip() for m in _TD_RE.finditer(content)]


def _compute_fill_rate(content: str) -> tuple[float, int, int]:
    """返回 (fill_rate, non_empty_count, total_count)；无表格时返回 (1.0, 0, 0)。"""
    texts = _extract_td_texts(content)
    if not texts:
        return 1.0, 0, 0
    # 过滤掉纯序号、纯分隔符等非语义内容
    meaningful = [t for t in texts if not _IGNORED_TD_TEXT.match(t)]
    if not meaningful:
        return 1.0, 0, 0
    non_empty = sum(1 for t in meaningful if t)
    total = len(meaningful)
    return (non_empty / total) if total else 1.0, non_empty, total


class TableFieldFillRateGate(Gate):
    """data_fill 章节表格单元格非空率检查（S.5.2 新建）。"""

    gate_name = "TableFieldFillRateGate"
    flow = "writer"
    stage = "post_llm"
    order = 40
    default_severity = "advisory"
    description = "data_fill 章节表格字段填充率检查（P3-2）"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "writer" or ctx.stage != "post_llm":
            return False
        evidence = ctx.evidence or {}
        return evidence.get("node_type") == "data_fill"

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        target = ctx.target
        content = getattr(target, "content", None) or ""
        threshold: float = float(
            (ctx.config or {}).get(
                "table_fill_rate_threshold", DEFAULT_MIN_FILL_RATE,
            )
        )

        rate, non_empty, total = _compute_fill_rate(content)

        if total == 0:
            # 不是表格型 data_fill（可能是纯文本段落），不守卫
            return GateVerdict(
                gate_name=self.gate_name,
                verdict="pass",
                severity=self.default_severity,
                reason="no table detected in content",
                trace={"non_empty_td": 0, "total_td": 0, "fill_rate": 1.0},
            )

        if rate >= threshold:
            return GateVerdict(
                gate_name=self.gate_name,
                verdict="pass",
                severity=self.default_severity,
                reason=f"table fill rate {rate:.2%} >= {threshold:.0%}",
                trace={
                    "non_empty_td": non_empty, "total_td": total,
                    "fill_rate": round(rate, 4),
                },
            )

        return GateVerdict(
            gate_name=self.gate_name,
            verdict="warn",
            severity=self.default_severity,
            reason=f"table fill rate {rate:.2%} < {threshold:.0%} (empty-but-formatted risk)",
            suggested_patch={
                "table_fill_rate_violated": True,
                "table_fill_rate": round(rate, 4),
                "failure_state": "needs_review",
            },
            trace={
                "non_empty_td": non_empty, "total_td": total,
                "fill_rate": round(rate, 4), "threshold": threshold,
            },
        )
