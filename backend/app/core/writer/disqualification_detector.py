"""P1.1 废标条款违反检测 — 纯函数模块。

从 consistency_checker._check_disqualification 与
gates/writer/disqualification_risk.DisqualificationRiskGate._find_disqualification_issues
抽取共享实现, 避免两处复制粘贴的伪算法同时演化。

历史伪算法:
    对每条 disq_clause, 若约束里含"不得/禁止/严禁", 而正文中出现了"关键词但不含'不得'"
    就告警。逻辑问题:
    1. 投标文件几乎必然出现约束关键词 (因为投标方要回应该条款);
    2. "不得"是招标方用语, 投标方通常以"承诺不存在..."形式表达, 不会包含字面"不得";
    3. 结果几乎全是 false positive, 真正命中时反被噪音淹没。

新策略:
    对每条 disq_clause, 抽取"禁止动作短语" (否定词 + 动词短语),
    对全文反向搜索**投标方的肯定承诺**(主语/宾语匹配 + 肯定情态词),
    即正文中出现"将/可以/存在/包含 + 相同动作"才视为违反信号。
"""

from __future__ import annotations

import re
from typing import Any, Iterable


_NEGATION_KEYWORDS = ("不得", "禁止", "严禁", "不允许", "不可")

# 肯定情态词/谓语, 投标方如果以这些表述 + 与约束相同动作, 就可能违反
_POSITIVE_VOICES = (
    "将", "已经", "存在", "包含", "可以", "可", "提供", "允许",
    "进行", "实施", "开展", "采用", "接受", "涉及",
)

# 投标方否定表达: 出现这些词 + 动作时视为"承诺遵守", 不应告警
_COMPLIANCE_VOICES = (
    "不", "无", "未", "没有", "绝不", "决不", "杜绝", "避免",
    "严格遵守", "承诺", "保证", "确认",
)

# 从 disq_clause 抽取 "禁止动作" — 第一步用非贪婪抓一个 2-8 字短串,
# 然后派生 2/3/4 字前缀作为候选, 便于与正文中"分包" "转包" 等核心动词对齐。
_ACTION_PATTERN = re.compile(
    r"(?:不得|禁止|严禁|不允许|不可)\s*([\u4e00-\u9fff]{2,8}?)(?=[,，。;；、\s]|$)"
)
# 兜底: 找不到合适的短串时, 用贪婪再抓一次, 最多 8 字
_ACTION_PATTERN_GREEDY = re.compile(
    r"(?:不得|禁止|严禁|不允许|不可)\s*([\u4e00-\u9fff]{2,8})"
)


def _extract_forbidden_actions(content: str) -> list[str]:
    """从约束原文抽取被禁止的动作关键词列表。

    为了增加与投标正文的对齐成功率, 对每个抓到的短串派生 2-4 字候选:
    "分包本项目" → ["分包本项目", "分包本项", "分包本", "分包"]
    """
    if not content:
        return []
    raw = _ACTION_PATTERN.findall(content) + _ACTION_PATTERN_GREEDY.findall(content)
    seen: set[str] = set()
    result: list[str] = []
    for base in raw:
        base = base.strip()
        if not base:
            continue
        # 从原样到 2 字前缀, 依次作为候选
        for n in range(len(base), 1, -1):
            cand = base[:n]
            if cand and cand not in seen:
                seen.add(cand)
                result.append(cand)
    return result


def _find_compliance_window(text: str, action: str, window: int = 30) -> bool:
    """检查 action 在 text 中出现时, 其前后 window 字符窗口内是否有
    投标方的合规/承诺表达 (表示"承诺不做此动作")。"""
    if not action or not text:
        return False
    for match in re.finditer(re.escape(action), text):
        start = max(0, match.start() - window)
        end = min(len(text), match.end() + window)
        window_text = text[start:end]
        if any(v in window_text for v in _COMPLIANCE_VOICES):
            return True
    return False


def _has_positive_commitment(text: str, action: str, window: int = 30) -> bool:
    """检查 action 在 text 中出现时, 其前后 window 字符内是否有
    肯定情态词 (表示"承诺做此动作"), 触发违反信号。"""
    if not action or not text:
        return False
    for match in re.finditer(re.escape(action), text):
        start = max(0, match.start() - window)
        end = min(len(text), match.end() + window)
        window_text = text[start:end]
        # 如果窗口内已有合规表达, 跳过
        if any(v in window_text for v in _COMPLIANCE_VOICES):
            continue
        if any(v in window_text for v in _POSITIVE_VOICES):
            return True
    return False


def find_disqualification_issues(
    full_text: str,
    clauses: Iterable[Any],
    *,
    sub_type: str = "disqualification",
) -> list[dict[str, Any]]:
    """针对 sub_type=disqualification 的约束列表, 返回违反信号。

    语义 (与旧伪算法相比):
        - 旧: "约束有否定词 + 全文含关键词 + 全文无否定词" → 告警
        - 新: 从约束抽取"禁止动作", 对每个动作:
              * 若正文窗口有合规/承诺表达 → 不告警
              * 若正文窗口有肯定情态词 + 动作 → 告警 (疑似违反)
              * 其他 → 不告警 (避免 false positive)
    """
    issues: list[dict[str, Any]] = []
    if not full_text:
        return issues

    for clause in clauses:
        if getattr(clause, "sub_type", "") != sub_type:
            continue
        content = getattr(clause, "content", "") or ""
        if not content:
            continue

        actions = _extract_forbidden_actions(content)
        if not actions:
            continue

        for action in actions:
            if _find_compliance_window(full_text, action, window=30):
                # 正文已明确承诺不做此动作, 视为合规
                continue
            if _has_positive_commitment(full_text, action, window=30):
                issues.append({
                    "type": "disqualification_risk",
                    "severity": "high",
                    "node_id": None,
                    "message": (
                        f"废标风险: 约束禁止'{action}', 但正文疑似存在肯定表述, 请核对"
                    ),
                    "detail": content[:200],
                })
                # 同一条 clause 只报一次, 避免重复刷屏
                break

    return issues


__all__ = [
    "find_disqualification_issues",
]
