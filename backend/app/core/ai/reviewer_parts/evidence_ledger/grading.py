"""
职责: 判定账本原子是材料级还是提及级
不做: 分面匹配、符合性判断、检索、LLM
"""

from __future__ import annotations

import re

from app.core.retriever.evidence_pipeline.entity_retriever import _ID_PATTERNS
from app.core.retriever.evidence_ranker import is_low_information_content
from app.core.retriever.material_key import MATCH_STRONG, match_material_in_text

MATERIAL_CHUNK_TYPES = frozenset({"image_evidence", "image_annotation"})
_MENTION_MARKERS = ("承诺书", "承诺", "目录")
_WINDOW = 300
_SUBSTANCE_MIN_CHARS = 16
_BLANK_MARKERS = ("空白", "粘贴处", "盖章处", "签字处", "模板")
_IDENTIFIER_CUES = ("编号", "证号", "证书编号", "统一社会信用代码", "信用代码", "许可证号")
_HEADING_RE = re.compile(
    r"^[（(]?\s*[0-9一二三四五六七八九十百千]+[、.．)）]\s*\S+"
)


def grade_atom(chunk_type: str | None, content: str, probe: str) -> str:
    """材料级: 名词邻域含证号, 或标题落点伴实质正文; OCR 判型仅佐证。"""
    text = content or ""
    window = _window_around(text, probe)
    noun_hit = _noun_hit(text, probe)
    has_id = _has_identifier(window) or _has_identifier(text)
    has_substance = _has_substance(text)
    is_ocr = (chunk_type or "") in MATERIAL_CHUNK_TYPES

    if _is_blank_template(text, has_id):
        return "mention"
    if _is_mention_line(window) and not has_id:
        return "mention"

    if is_ocr:
        if noun_hit and (has_id or has_substance):
            return "material"
        return "mention"

    if _is_title_like(text):
        if noun_hit and has_substance:
            return "material"
        return "mention"

    if has_id:
        return "material"
    if noun_hit and has_substance:
        return "material"
    return "mention"


def _noun_hit(content: str, probe: str) -> bool:
    if not probe or not content:
        return False
    if probe in content:
        return True
    if len(probe) > 6 and probe[:6] in content:
        return True
    return match_material_in_text(probe, content) == MATCH_STRONG


def _has_substance(content: str) -> bool:
    if not content or is_low_information_content(content):
        return False
    compact = "".join(content.split())
    if any(marker in compact for marker in _BLANK_MARKERS):
        return False
    return len(compact) >= _SUBSTANCE_MIN_CHARS


def _is_title_like(content: str) -> bool:
    sample = (content or "").strip()
    if not sample:
        return True
    first_line = sample.splitlines()[0].strip()
    compact = "".join(sample.split())
    if len(compact) <= 20:
        return True
    return bool(_HEADING_RE.match(first_line))


def _is_blank_template(content: str, has_id: bool) -> bool:
    if has_id:
        return False
    compact = "".join((content or "").split())
    if not compact:
        return True
    if any(marker in compact for marker in _BLANK_MARKERS):
        return True
    return is_low_information_content(content)


def _window_around(content: str, probe: str, radius: int = _WINDOW) -> str:
    if not content:
        return ""
    idx = content.find(probe) if probe else -1
    if idx < 0 and probe and len(probe) > 6:
        idx = content.find(probe[:6])
    if idx < 0:
        return content[: radius * 2]
    start = max(0, idx - radius)
    end = min(len(content), idx + max(len(probe), 1) + radius)
    return content[start:end]


def _has_identifier(text: str) -> bool:
    if not text:
        return False
    if any(pattern.search(text) for pattern in _ID_PATTERNS):
        return True
    return any(cue in text for cue in _IDENTIFIER_CUES)


def _is_mention_line(text: str) -> bool:
    return any(marker in (text or "") for marker in _MENTION_MARKERS)
