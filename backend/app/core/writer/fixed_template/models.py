"""固定模板事实层数据结构。

职责: 定义固定模板目录构建过程中传递的轻量不可变数据。
不做: 不识别模板、不切分边界、不访问数据库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class FixedTemplateEntry:
    """单个固定模板的结构化事实。"""

    template_key: str
    title: str
    aliases: tuple[str, ...]
    source_page_start: Optional[int]
    source_page_end: Optional[int]
    raw_text: str
    field_slots: tuple[str, ...] = field(default_factory=tuple)
    signature_slots: tuple[str, ...] = field(default_factory=tuple)
    has_table: bool = False
    confidence: float = 0.0
    source: str = "rule"
    volume_type: Optional[str] = None

    def snapshot_dict(self) -> dict:
        """转换为 writing_clauses.metadata_json 可承载的快照结构。"""
        return {
            "title": self.title,
            "template_key": self.template_key,
            "aliases": list(self.aliases),
            "source_page_start": self.source_page_start,
            "source_page_end": self.source_page_end,
            "raw_text_from_chunks": self.raw_text,
            "field_slots": list(self.field_slots),
            "signature_slots": list(self.signature_slots),
            "has_table": self.has_table,
            "extraction_confidence": round(self.confidence, 3),
            "is_complete_candidate": self.is_complete_candidate,
            "source": self.source,
            "volume_type": self.volume_type,
        }

    @property
    def is_complete_candidate(self) -> bool:
        """模板是否具备可直接消费的基本完整性。"""
        return self.confidence >= 0.75 and bool(self.raw_text.strip())


@dataclass(frozen=True)
class TemplateMatchResult:
    """模板标题匹配结果。"""

    matched: bool
    template_key: Optional[str] = None
    confidence: float = 0.0
    reason: str = ""
