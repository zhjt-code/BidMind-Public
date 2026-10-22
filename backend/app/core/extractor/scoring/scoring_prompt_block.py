"""
职责: 从招标要求对象构造评分项评审标准提示块与检索扩展文本。
不做: 不调用 LLM/DB、不执行投标响应提取、不决定审查结论。
"""
from __future__ import annotations


def _is_scoring(req: object) -> bool:
    """判断要求是否为评分项或重点评审项（有实际分值的 is_key_review）。

    评判规则（满足任一即为评分相关）:
    - requirement_kind == 'scoring_item'
    - is_key_review=1 且 score_weight 非空
    """
    kind = getattr(req, "requirement_kind", "normal") or "normal"
    if kind == "scoring_item":
        return True
    is_key = getattr(req, "is_key_review", 0) or 0
    weight = getattr(req, "score_weight", None)
    return bool(is_key) and weight is not None


def build_scoring_context(req: object) -> str:
    """构造评分项评审标准补充块，供投标响应提取 Prompt 注入。

    从 scoring_metadata 中提取 score_item_name、max_score、
    authoritative_description、score_rules、deduction_rules，
    格式化为供 LLM 理解的评审标准段落。

    Args:
        req: Requirement ORM 对象或拥有相同属性的 mock 对象。

    Returns:
        格式化后的评分标准块（非空字符串）；普通要求返回空字符串。
    """
    if not _is_scoring(req):
        return ""

    meta: dict = getattr(req, "scoring_metadata", None) or {}
    score_item_name: str = meta.get("score_item_name") or ""
    max_score = meta.get("max_score")
    if max_score is None:
        max_score = getattr(req, "score_weight", None)
    authoritative_description: str = meta.get("authoritative_description") or ""
    score_rules: list[str] = meta.get("score_rules") or []
    deduction_rules: list[str] = meta.get("deduction_rules") or []

    lines: list[str] = ["## 评分项评审标准（招标文件原文）"]

    if score_item_name:
        lines.append(f"- 评分项名称: {score_item_name}")
    if max_score is not None:
        lines.append(f"- 满分: {max_score}分")
    if authoritative_description:
        lines.append(f"- 权威描述: {authoritative_description}")
    if score_rules:
        lines.append("- 评分/分档规则:")
        for rule in score_rules:
            lines.append(f"  - {rule}")
    if deduction_rules:
        lines.append("- 扣分规则:")
        for rule in deduction_rules:
            lines.append(f"  - {rule}")

    return "\n".join(lines)


def build_query_extension(req: object) -> str:
    """构造检索查询扩展文本，追加到 description 用于语义检索增强。

    从 score_item_name 和 score_rules 中提取短关键词，并：
    - 自动去重（与 description 已有内容作大小写不敏感对比）
    - 限制总长度 ≤ 120 字符
    - 每条 score_rules 取前 30 字作为片段

    Args:
        req: Requirement ORM 对象或拥有相同属性的 mock 对象。

    Returns:
        扩展关键词文本（可直接拼接到 description 后）；非评分项返回空字符串。
    """
    if not _is_scoring(req):
        return ""

    meta: dict = getattr(req, "scoring_metadata", None) or {}
    score_item_name: str = meta.get("score_item_name") or ""
    score_rules: list[str] = meta.get("score_rules") or []

    desc_lower = (getattr(req, "description", "") or "").lower()

    parts: list[str] = []
    seen_lower: set[str] = set()

    if score_item_name:
        snippet = score_item_name[:40].strip()
        if snippet and snippet.lower() not in desc_lower:
            parts.append(snippet)
            seen_lower.add(snippet.lower())

    for rule in score_rules:
        snippet = rule[:30].strip()
        if not snippet:
            continue
        if snippet.lower() in desc_lower or snippet.lower() in seen_lower:
            continue
        seen_lower.add(snippet.lower())
        parts.append(snippet)
        if sum(len(p) for p in parts) >= 100:
            break

    ext = " ".join(parts)
    return ext[:120]
