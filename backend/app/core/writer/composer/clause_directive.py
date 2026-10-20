"""约束到写作指令转化层

职责: 将已绑定约束(含 metadata_json)按"写作动作"重组为分组指令文本, 供章节生成消费
不做: 不渲染/不调 LLM/DB、不改约束数据、不做绑定/去重、不管理 token 全局预算
      (仅按入参 max_chars 自截断, 全局预算由调用方掌控)
"""

from __future__ import annotations

import re
from typing import Any, Optional

from app.models.writing_clause import WritingClause

# 实质性标记(与第一期提取 Prompt 的 ★/▲/☆ 标记集对齐)
_SUBSTANTIVE_MARK_RE = re.compile(r"[★▲☆]")

# 写作动作分组: (group_key, 组标题), 顺序即展示优先级(截断时先保高优先级)
_GROUP_DEFS: list[tuple[str, str]] = [
    ("redline", "内容红线（违反将导致废标/无效投标）"),
    ("scoring", "评分响应点（直接影响得分）"),
    ("content", "必写内容要素"),
    ("table", "表格要求"),
    ("material", "需在正文体现的材料/证据"),
    ("highlight", "加分与差异化方向"),
    ("context", "项目背景"),
    ("other", "其他要求"),
]

_SUBTYPE_TO_GROUP: dict[str, str] = {
    "disqualification": "redline",
    "scoring_criterion": "scoring",
    "content_required": "content",
    "table_format": "table",
    "material_required": "material",
    "emphasis_point": "highlight",
    "differentiation_hint": "highlight",
    "project_context": "context",
}


def _classify(sub_type: str) -> str:
    """将 sub_type 映射到写作动作分组; 未知类型归入 other 兜底。"""
    return _SUBTYPE_TO_GROUP.get(sub_type or "", "other")


def _read_meta(clause: WritingClause, key: str) -> Optional[Any]:
    """安全读取 metadata_json 中的字段; 非 dict / 空值统一返回 None。"""
    meta = getattr(clause, "metadata_json", None)
    if not isinstance(meta, dict):
        return None
    value = meta.get(key)
    if value in (None, "", [], {}):
        return None
    return value


def _meta_text(value: Optional[Any]) -> str:
    """将 metadata 值归一化为短文本(list/tuple 用顿号拼接)。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value if str(item).strip()]
        return "、".join(items)
    return str(value).strip()


def _is_substantive(clause: WritingClause) -> bool:
    """废标条款是否为★/▲实质性条款(红线组内置顶)。"""
    text = f"{getattr(clause, 'raw_text', '') or ''}{getattr(clause, 'content', '') or ''}"
    return bool(_SUBSTANTIVE_MARK_RE.search(text))


def _format_redline(clause: WritingClause, content: str) -> str:
    mark = "★ " if _is_substantive(clause) else ""
    risk = _meta_text(_read_meta(clause, "risk_consequence"))
    suffix = f"（{risk}）" if risk else ""
    return f"- {mark}{content}{suffix}"


def _format_scoring(clause: WritingClause, content: str) -> str:
    name = _meta_text(_read_meta(clause, "score_item_name")) or content
    if not name:
        return ""
    max_score = _read_meta(clause, "max_score")
    head = f"{name}（满分{_meta_text(max_score)}分）" if max_score is not None else name
    parts = [head]
    score_rules = _meta_text(_read_meta(clause, "score_rules"))
    if score_rules:
        parts.append(f"得分要点：{score_rules}")
    deduction_rules = _meta_text(_read_meta(clause, "deduction_rules"))
    if deduction_rules:
        parts.append(f"避免扣分：{deduction_rules}")
    if not score_rules and not deduction_rules and content and content != name:
        parts.append(f"评分标准：{content}")
    elif score_rules and content and content != name and score_rules not in content:
        # content 含超出 score_rules 的差量信息（如具体条件、天数等）时补出，
        # 避免 Wave1 提取的详细描述因 score_rules 非空而整体丢失。
        parts.append(f"评分标准补充：{content}")
    return "- " + "；".join(parts)


def _format_material(clause: WritingClause, content: str) -> str:
    attrs = [
        _meta_text(_read_meta(clause, "material_kind")),
        _meta_text(_read_meta(clause, "required_form")),
    ]
    attrs = [a for a in attrs if a]
    line = f"- {content}"
    if attrs:
        line += f"（{'/'.join(attrs)}）"
    evidence = _meta_text(_read_meta(clause, "evidence_hint"))
    if evidence:
        line += f" 需体现：{evidence}"
    return line


def _format_line(group_key: str, clause: WritingClause) -> str:
    """按分组把单条约束格式化为写作指令; 空内容返回空串。"""
    content = (getattr(clause, "content", "") or "").strip()
    if group_key == "scoring":
        return _format_scoring(clause, content)
    if not content:
        return ""
    if group_key == "redline":
        return _format_redline(clause, content)
    if group_key == "material":
        return _format_material(clause, content)
    return f"- {content}"


def build_clause_directives(
    clauses: list[WritingClause],
    *,
    max_chars: int,
) -> str:
    """将约束列表转化为按写作动作分组的指令文本。

    分组顺序即优先级, 超 max_chars 时优先保留高优先级组(红线/评分点),
    丢弃低优先级组(背景/其他)。clauses 为空返回空串(由调用方回退占位)。
    """
    if not clauses:
        return ""

    buckets: dict[str, list[WritingClause]] = {}
    for clause in clauses:
        buckets.setdefault(_classify(getattr(clause, "sub_type", "")), []).append(clause)

    sections: list[str] = []
    used = 0
    for group_key, title in _GROUP_DEFS:
        items = buckets.get(group_key)
        if not items:
            continue
        if group_key == "redline":
            items = sorted(items, key=lambda c: 0 if _is_substantive(c) else 1)

        lines: list[str] = []
        for clause in items:
            line = _format_line(group_key, clause)
            if not line:
                continue
            if used + len(line) > max_chars and sections:
                break
            lines.append(line)
            used += len(line)

        if lines:
            header = f"### {title}"
            sections.append(header + "\n" + "\n".join(lines))
            used += len(header) + 2
        if used >= max_chars:
            break

    return "\n\n".join(sections)


__all__ = ["build_clause_directives"]
