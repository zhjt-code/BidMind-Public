"""写标约束写作消费分级器

职责: 对已提取约束批量标记 writing_relevance, 将明显非正文消费项折叠为 reference。
不做: 不删除约束、不处理 HIDDEN 旁路类型、不覆盖人工恢复、不创建章节绑定。
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from app.core.ai import LLMClient
from app.core.ai.prompt_registry import resolve_runtime_params
from app.core.config import settings
from app.core.writer.clause_scope_policy import (
    HIDDEN_COVERAGE_SUB_TYPES,
    WRITING_RELEVANCE_CHECK_ONLY,
    WRITING_RELEVANCE_DIRECT,
    WRITING_RELEVANCE_REFERENCE,
    build_relevance_mark,
    is_manual_relevance_override,
)
from app.core.writer.prompts.writing_relevance import get_writing_relevance_prompt
from app.core.writer.writing_relevance.rules import (
    classify_check_only_by_rules,
    classify_reference_by_rules,
)

_SUBSTANTIVE_MARK_RE = re.compile(r"[★▲☆]")


def _is_substantive_disqualification(clause: Any) -> bool:
    if getattr(clause, "sub_type", "") != "disqualification":
        return False
    text = f"{getattr(clause, 'raw_text', '') or ''}{getattr(clause, 'content', '') or ''}"
    return bool(_SUBSTANTIVE_MARK_RE.search(text))


def _is_forced_direct(clause: Any) -> bool:
    return (
        getattr(clause, "sub_type", "") == "scoring_criterion"
        or _is_substantive_disqualification(clause)
    )


def _format_clauses(clauses: list[Any]) -> str:
    lines: list[str] = []
    for clause in clauses:
        lines.append(
            f"- ID={getattr(clause, 'id', '')}, 类型={getattr(clause, 'sub_type', '')}, "
            f"内容={str(getattr(clause, 'content', '') or '')[:200]}"
        )
    return "\n".join(lines)


def _normalized_clause_text(clause: Any) -> str:
    text = str(getattr(clause, "content", "") or "")
    return re.sub(r"[\s\W_]+", "", text).lower()


def _mark_direct(clause: Any, reason: str) -> None:
    clause.metadata_json = build_relevance_mark(
        getattr(clause, "metadata_json", None),
        level=WRITING_RELEVANCE_DIRECT,
        source="extract_pass",
        reason=reason,
    )


def _mark_reference(clause: Any, reason: str) -> None:
    clause.metadata_json = build_relevance_mark(
        getattr(clause, "metadata_json", None),
        level=WRITING_RELEVANCE_REFERENCE,
        source="extract_pass",
        reason=reason,
    )


def _mark_check_only(clause: Any, reason: str) -> None:
    clause.metadata_json = build_relevance_mark(
        getattr(clause, "metadata_json", None),
        level=WRITING_RELEVANCE_CHECK_ONLY,
        source="extract_pass",
        reason=reason,
    )


async def classify_writing_relevance(
    clauses: list[Any],
    *,
    llm_client: LLMClient,
) -> list[Any]:
    """批量标记 writing_relevance, 返回原列表以便调用方继续使用。"""
    candidates: list[Any] = []
    by_id: dict[int, Any] = {}
    seen_disqualification: dict[str, int] = {}
    for clause in clauses:
        sub_type = getattr(clause, "sub_type", "")
        clause_id = getattr(clause, "id", None)
        if sub_type in HIDDEN_COVERAGE_SUB_TYPES or clause_id is None:
            continue
        if is_manual_relevance_override(clause):
            continue
        if (
            sub_type == "disqualification"
            and not _is_substantive_disqualification(clause)
        ):
            normalized = _normalized_clause_text(clause)
            duplicate_of = seen_disqualification.get(normalized)
            if duplicate_of is not None:
                _mark_reference(clause, f"同文重复，已由约束ID {duplicate_of} 承接")
                clause.metadata_json["duplicate_of_clause_id"] = duplicate_of
                continue
            if normalized:
                seen_disqualification[normalized] = int(clause_id)
        if _is_forced_direct(clause):
            _mark_direct(clause, "评分项或实质性条款必须进入正文消费")
            continue
        check_only_reason = (
            classify_check_only_by_rules(clause)
            if settings.WRITING_DISQ_CHECK_ONLY_ENABLED else None
        )
        if check_only_reason:
            _mark_check_only(clause, check_only_reason)
            continue
        rule_reason = classify_reference_by_rules(clause)
        if rule_reason:
            _mark_reference(clause, rule_reason)
            continue
        _mark_direct(clause, "默认进入正文消费")
        candidates.append(clause)
        by_id[int(clause_id)] = clause

    if not candidates:
        return clauses

    try:
        system_msg, user_msg = get_writing_relevance_prompt(_format_clauses(candidates))
        temperature, max_tokens = resolve_runtime_params(
            "writing.relevance_classify", 0.1, 2000,
        )
        result = await llm_client.chat_json(
            user_message=user_msg,
            system_message=system_msg,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        logger.warning("写标约束消费分级 LLM 失败, 保守全部 direct: {}", exc)
        return clauses

    if not isinstance(result, list):
        return clauses

    for item in result:
        if not isinstance(item, dict):
            continue
        try:
            clause_id = int(item.get("clause_id"))
        except (TypeError, ValueError):
            continue
        clause = by_id.get(clause_id)
        if clause is None or _is_forced_direct(clause) or is_manual_relevance_override(clause):
            continue
        relevance = item.get("writing_relevance")
        reason = str(item.get("reason") or "LLM判定")
        if relevance == WRITING_RELEVANCE_REFERENCE:
            _mark_reference(clause, reason)
        else:
            _mark_direct(clause, reason)

    return clauses


__all__ = ["classify_writing_relevance"]
