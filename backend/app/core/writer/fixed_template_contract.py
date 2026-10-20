"""fixed_template 强一致契约与风险标记。

职责: 定义固定模板一致性检查的共享常量和轻量结果结构。
不做: 不访问数据库、不渲染 Word、不修改章节正文。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


FIXED_TEMPLATE_SNAPSHOT_KEY = "fixed_template_snapshot"
FIXED_TEMPLATE_REQUIRES_REVIEW_FLAG = "fixed_template_requires_review"

ISSUE_SOURCE_MISSING = "fixed_template_source_missing"
ISSUE_MODIFIED = "fixed_template_modified"
ISSUE_STRUCTURE_MISMATCH = "fixed_template_structure_mismatch"
ISSUE_FIELD_MISSING = "fixed_template_field_missing"
ISSUE_TABLE_MISMATCH = "fixed_template_table_mismatch"
ISSUE_SIGNATURE_SLOT_MISSING = "fixed_template_signature_slot_missing"
ISSUE_INCOMPLETE = "fixed_template_incomplete"


@dataclass(frozen=True)
class FixedTemplateSnapshot:
    """固定模板来源快照，优先来自招标原文 chunk 回查。"""

    title: str
    source_page_start: Optional[int]
    source_page_end: Optional[int]
    raw_text_from_chunks: str
    extraction_confidence: float
    is_complete_candidate: bool
    template_key: Optional[str] = None
    aliases: Optional[list[str]] = None
    field_slots: Optional[list[str]] = None
    signature_slots: Optional[list[str]] = None
    has_table: bool = False
    source: str = "chunk_range"

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "source_page_start": self.source_page_start,
            "source_page_end": self.source_page_end,
            "raw_text_from_chunks": self.raw_text_from_chunks,
            "extraction_confidence": self.extraction_confidence,
            "is_complete_candidate": self.is_complete_candidate,
            "template_key": self.template_key,
            "aliases": self.aliases or [],
            "field_slots": self.field_slots or [],
            "signature_slots": self.signature_slots or [],
            "has_table": self.has_table,
            "source": self.source,
        }


def get_fixed_template_snapshot(metadata: Any) -> Optional[dict[str, Any]]:
    """从 metadata_json 中读取 fixed_template 快照。"""
    if not isinstance(metadata, dict):
        return None
    snapshot = metadata.get(FIXED_TEMPLATE_SNAPSHOT_KEY)
    return snapshot if isinstance(snapshot, dict) else None


def resolve_fixed_template_source(clause: Any) -> tuple[str, Optional[dict[str, Any]]]:
    """返回固定模板优先来源文本和对应快照。

 优先级: 布局保真文本 raw_text_verbatim > chunk 回查文本
 raw_text_from_chunks > 约束 raw_text。
 """
    snapshot = get_fixed_template_snapshot(getattr(clause, "metadata_json", None))
    if snapshot:
        verbatim = str(snapshot.get("raw_text_verbatim") or "").strip()
        if verbatim:
            return verbatim, snapshot
        raw = str(snapshot.get("raw_text_from_chunks") or "").strip()
        if raw:
            return raw, snapshot
    return str(getattr(clause, "raw_text", None) or "").strip(), snapshot


def has_verbatim_source(snapshot: Optional[dict[str, Any]]) -> bool:
    """快照是否携带布局保真文本(决定填充/渲染是否走直通模式)。"""
    if not isinstance(snapshot, dict):
        return False
    return bool(str(snapshot.get("raw_text_verbatim") or "").strip())


def fixed_template_review_gate(reason: str, severity: str = "high") -> dict[str, Any]:
    """构造落库 gate_flags 条目，供前端展示复核提示。"""
    return {
        FIXED_TEMPLATE_REQUIRES_REVIEW_FLAG: {
            "verdict": "reject",
            "severity": severity,
            "reason": reason[:200],
            "trace": {"outcome": "needs_review"},
        }
    }
