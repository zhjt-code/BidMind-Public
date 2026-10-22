"""评分项证据充分性评估（换轴重构 P1， fail-closed 双防线之验收侧）。

职责: 对候选评分项条目做证据充分性拒收判定——名称空白、紧凑后单字符名且无
 规则文本视为不充分证据, 输出拒收索引与 fail-visible 标记; 逐条目异常
 转为标记而非抛出(调用边界防御)。
不做: 不修复/补全条目、不做分值校验、不做物化、不做声明捕获、不调用 LLM/DB。
"""
from __future__ import annotations

import re
import unicodedata

_WHITESPACE_RE = re.compile(r"\s+")


def assess_item_evidence_sufficiency(
    entries: list[tuple[str, float, str]],
) -> tuple[tuple[int, ...], tuple[str, ...]]:
    """逐条目评估证据充分性; 返回(不充分条目索引元组, fail-visible标记元组)。

 entries: (名称, 分值, 规则文本)。规则文本须由调用方合并 score_rules 与
 deduction_rules 后传入(: 扣分类项的规则天然只存于后者)。
 拒收条件: 名称空白/仅空白; 名称紧凑后长度<=1 且规则文本空白。
 条目处理异常按 fail-closed 计入拒收。
 """
    insufficient: list[int] = []
    flags: list[str] = []
    for index, entry in enumerate(entries):
        try:
            reason = _entry_insufficiency_reason(entry)
        except Exception as exc:  # noqa: BLE001 调用边界防御: 异常转标记不抛出
            insufficient.append(index)
            flags.append(f"entry[{index}]: 处理异常转拒收({type(exc).__name__})")
            continue
        if reason:
            insufficient.append(index)
            flags.append(f"entry[{index}]: {reason}")
    return tuple(insufficient), tuple(flags)


def _compact_item_name(name: str) -> str:
    """项名紧凑形式: NFKC 全半角归一 + 去全部空白, 不剥任何业务尾缀。: 此处禁止复用组标签归一器 normalize_group_label——它会迭代剥离
 "项/分/标准/部分/卷"等组语义尾缀, 用在项名上会把"扣分项"削成单字"扣"而
 误判为无信息量。对项名而言这些字往往是实义部分(扣分项/加分项/否决项),
 项名与组标签是两类对象, 不共用归一口径。
 """
    return _WHITESPACE_RE.sub("", unicodedata.normalize("NFKC", str(name or "")))


def _entry_insufficiency_reason(entry: tuple[str, float, str]) -> str:
    """单条目拒收原因; 充分证据返回空串。"""
    name, _score, rule_text = entry
    name_text = str(name or "")
    if not name_text.strip():
        return "名称空白拒收"
    rule_blank = not str(rule_text or "").strip()
    if rule_blank and len(_compact_item_name(name_text)) <= 1:
        return "单字符项名且无规则文本拒收"
    return ""


__all__ = ["assess_item_evidence_sufficiency"]
