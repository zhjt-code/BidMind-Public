"""fixed_template 结构化一致性校验器。

职责: 对比招标固定模板与生成草稿的文本、字段、表格和签章语义结构。
不做: 不修改草稿内容、不执行渲染、不决定 API 导出权限。
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any, Optional

from bs4 import BeautifulSoup

from app.core.writer.fixed_template_contract import (
    ISSUE_FIELD_MISSING,
    ISSUE_SIGNATURE_SLOT_MISSING,
    ISSUE_SOURCE_MISSING,
    ISSUE_STRUCTURE_MISMATCH,
    ISSUE_TABLE_MISMATCH,
)

_FIXED_TEMPLATE_STRUCTURAL_SIMILARITY = 0.82
_FIELD_KEYWORDS = (
    "投标人", "法定代表人", "委托代理人", "项目名称", "项目编号",
    "金额", "报价", "工期", "服务期", "质量", "地址", "电话",
    "身份证", "开户", "账号", "日期", "签字", "盖章", "公章",
)
_SIGNATURE_KEYWORDS = ("签字", "盖章", "公章", "日期", "法定代表人", "委托代理人")
_PLACEHOLDER_RE = re.compile(r"\[待补充[:：]\s*([^\]]+?)\]")


@dataclass(frozen=True)
class FixedTemplateValidationResult:
    """固定模板结构化校验结果。"""

    issues: list[dict[str, Any]]
    similarity: float


def validate_fixed_template(
    *,
    node_id: int | None,
    node_title: str,
    source_text: str,
    draft_content: str,
) -> FixedTemplateValidationResult:
    """对固定模板源文本和草稿内容做结构化一致性校验。"""
    if not source_text.strip():
        return FixedTemplateValidationResult(
            issues=[_issue(
                ISSUE_SOURCE_MISSING,
                node_id,
                f"固定模板'{node_title}'缺少可核对的招标原始模板",
                "无法比对生成内容与招标文件模板是否一致，存在废标风险。",
            )],
            similarity=0.0,
        )

    source_plain = _plain_text(source_text)
    draft_plain = _plain_text(draft_content)
    similarity = difflib.SequenceMatcher(None, source_plain, draft_plain).ratio()
    issues: list[dict[str, Any]] = []

    if similarity < _FIXED_TEMPLATE_STRUCTURAL_SIMILARITY:
        issues.append(_issue(
            ISSUE_STRUCTURE_MISMATCH,
            node_id,
            (
                f"固定模板'{node_title}'整体结构相似度 {similarity:.0%} "
                f"低于 {_FIXED_TEMPLATE_STRUCTURAL_SIMILARITY:.0%}"
            ),
            "固定模板正文或结构发生明显变化，需人工核对是否与招标模板完全一致。",
        ))

    missing_fields = sorted(_extract_fields(source_plain) - _extract_fields(draft_plain))
    if missing_fields:
        issues.append(_issue(
            ISSUE_FIELD_MISSING,
            node_id,
            f"固定模板'{node_title}'缺少模板字段: {', '.join(missing_fields[:8])}",
            "字段集合必须与招标模板保持一致，只允许填写字段值，不允许删除字段。",
        ))

    missing_signature = sorted(_extract_signature_slots(source_plain) - _extract_signature_slots(draft_plain))
    if missing_signature:
        issues.append(_issue(
            ISSUE_SIGNATURE_SLOT_MISSING,
            node_id,
            f"固定模板'{node_title}'缺少签章/日期位置: {', '.join(missing_signature[:8])}",
            "签字、盖章、日期、法定代表人等位置属于废标高风险字段，必须保留。",
        ))

    source_tables = _table_signatures(source_text)
    draft_tables = _table_signatures(draft_content)
    if source_tables or draft_tables:
        table_issue = _compare_tables(source_tables, draft_tables)
        if table_issue:
            issues.append(_issue(
                ISSUE_TABLE_MISMATCH,
                node_id,
                f"固定模板'{node_title}'表格结构不一致",
                table_issue,
            ))

    return FixedTemplateValidationResult(issues=issues, similarity=similarity)


def _issue(issue_type: str, node_id: int | None, message: str, detail: str) -> dict[str, Any]:
    return {
        "type": issue_type,
        "severity": "high",
        "node_id": node_id,
        "message": message,
        "detail": detail,
    }


def _plain_text(value: str) -> str:
    soup = BeautifulSoup(value or "", "html.parser")
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_fields(text: str) -> set[str]:
    fields = {kw for kw in _FIELD_KEYWORDS if kw in text}
    for match in _PLACEHOLDER_RE.finditer(text):
        name = match.group(1).strip()
        if name:
            fields.add(name)
    for label in re.findall(r"([\u4e00-\u9fffA-Za-z0-9()]{2,16})\s*[:：]", text):
        if any(kw in label for kw in _FIELD_KEYWORDS):
            fields.add(label.strip())
    return fields


def _extract_signature_slots(text: str) -> set[str]:
    return {kw for kw in _SIGNATURE_KEYWORDS if kw in text}


def _table_signatures(value: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(value or "", "html.parser")
    signatures: list[dict[str, Any]] = []
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [
                re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
                for cell in tr.find_all(["th", "td"])
            ]
            if cells:
                rows.append(cells)
        if not rows:
            continue
        signatures.append({
            "rows": len(rows),
            "cols": max(len(row) for row in rows),
            "headers": [cell for cell in rows[0] if cell],
        })
    return signatures


def _compare_tables(source: list[dict[str, Any]], draft: list[dict[str, Any]]) -> Optional[str]:
    if len(source) != len(draft):
        return f"表格数量不一致: 原模板 {len(source)} 个，草稿 {len(draft)} 个"
    for index, (src, dst) in enumerate(zip(source, draft), start=1):
        if src["rows"] != dst["rows"] or src["cols"] != dst["cols"]:
            return (
                f"第 {index} 个表格行列不一致: "
                f"原模板 {src['rows']}x{src['cols']}，草稿 {dst['rows']}x{dst['cols']}"
            )
        missing_headers = set(src["headers"]) - set(dst["headers"])
        if missing_headers:
            return f"第 {index} 个表格缺少表头: {', '.join(sorted(missing_headers))}"
    return None
