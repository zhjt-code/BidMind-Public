"""技术测试任务覆盖检查。

职责: 从已确认写标约束中识别测试任务清单，并检查当前草稿是否逐项覆盖。
不做: 不访问数据库、不调用 LLM、不改写目录或正文。
"""

from __future__ import annotations

import re
from typing import Any

_TASK_LIST_PATTERN = re.compile(
    r"(?:包括|覆盖)([^。；;]*?(?:测试|试验)[^。；;]*)"
)
_TASK_SPLIT_PATTERN = re.compile(r"[、,，；;]\s*")


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _extract_test_tasks(text: str) -> list[str]:
    """从约束文本中提取按顿号/逗号分隔的测试任务名称。"""
    normalized = " ".join((text or "").split())
    tasks: list[str] = []
    seen: set[str] = set()
    for match in _TASK_LIST_PATTERN.finditer(normalized):
        segment = match.group(1)
        if "包括" in segment:
            segment = segment.rsplit("包括", 1)[1]
        for part in _TASK_SPLIT_PATTERN.split(segment):
            item = part.strip(" ：:。；;，, ")
            if len(item) < 4:
                continue
            if (
                "全部" in item
                or "各测试组数" in item
                or "要求投标人" in item
                or "具备对应" in item
            ):
                continue
            if "测试" not in item and "试验" not in item:
                continue
            if item not in seen:
                seen.add(item)
                tasks.append(item)
    return tasks


def _task_is_present(task: str, full_text: str) -> bool:
    normalized_task = _normalize_text(task)
    normalized_full = _normalize_text(full_text)
    if normalized_task in normalized_full:
        return True

    compact = (
        normalized_task
        .replace("分析测试", "")
        .replace("分析试验", "")
        .replace("测试", "")
        .replace("试验", "")
    )
    return len(compact) >= 4 and compact in normalized_full


def build_technical_task_coverage_issues(
    clauses: list[Any],
    drafts: list[Any],
) -> list[dict[str, Any]]:
    """返回正文缺失的技术测试任务覆盖问题。"""
    tasks: list[str] = []
    seen: set[str] = set()
    for clause in clauses:
        sub_type = str(getattr(clause, "sub_type", "") or "")
        if sub_type not in {"content_required", "emphasis_point", "project_context"}:
            continue
        for task in _extract_test_tasks(str(getattr(clause, "content", "") or "")):
            if task not in seen:
                seen.add(task)
                tasks.append(task)

    if len(tasks) < 2:
        return []

    full_text = "\n".join(str(getattr(draft, "content", "") or "") for draft in drafts)
    missing = [task for task in tasks if not _task_is_present(task, full_text)]
    if not missing:
        return []

    return [{
        "type": "technical_task_coverage",
        "severity": "high",
        "node_id": None,
        "message": "技术方案缺少测试任务覆盖: " + "、".join(missing[:8]),
        "detail": f"已识别 {len(tasks)} 项测试任务，正文缺少 {len(missing)} 项。",
    }]


__all__ = ["build_technical_task_coverage_issues"]
