"""
职责: 复用解析层的水印学习函数, 剔除质检字体层(fitz 行与 span)中的水印字符
不做: 不改解析层规则、不学习图片水印、不处理页眉页脚、水印过滤关闭时不生效
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.core.document_qa.contracts import QaBlock, QaLine, QaSpan

_AttrKey = tuple[str, float, int, bool]


class _CharProbe:
    """解析层按 pdfplumber 字符字典读属性; 只提供它读取的键, 同属性的字共用一份属性。"""

    __slots__ = ("text", "attrs")

    def __init__(self, text: str, attrs: dict[str, Any]) -> None:
        self.text = text
        self.attrs = attrs

    def get(self, key: str, default: Any = None) -> Any:
        if key == "text":
            return self.text
        return self.attrs.get(key, default)


def _rgb(color: int) -> tuple[float, float, float]:
    return (((color >> 16) & 255) / 255.0, ((color >> 8) & 255) / 255.0, (color & 255) / 255.0)


def _span_attrs(cache: dict[_AttrKey, dict[str, Any]], span: QaSpan, rotated: bool) -> dict[str, Any]:
    key = (span.font, round(span.size, 1), span.color, rotated)
    attrs = cache.get(key)
    if attrs is None:
        attrs = {
            "fontname": span.font,
            "size": span.size,
            "non_stroking_color": _rgb(span.color),
            "matrix": (1.0, 1.0 if rotated else 0.0, 0.0, 1.0, 0.0, 0.0),
        }
        cache[key] = attrs
    return attrs


def learn_text_layer_watermark(
    blocks: list[QaBlock],
    page_count: int,
    *,
    min_repeat_pages: int = 3,
    repeat_ratio: float = 0.6,
) -> Any:
    """用解析层 learn_watermark_text_groups 学水印字符组, 返回同款 WatermarkPolicy。"""
    from app.core.parser.watermark_policy import WatermarkPolicy, learn_watermark_text_groups

    cache: dict[_AttrKey, dict[str, Any]] = {}
    per_page: dict[int, dict[tuple[int, str], _CharProbe]] = {
        page: {} for page in range(1, max(page_count, 0) + 1)
    }
    for block in blocks:
        probes = per_page.setdefault(block.page, {})
        for line in block.lines:
            for span in line.spans:
                attrs = _span_attrs(cache, span, line.rotated)
                for char in span.text:
                    if char.strip():
                        # ponytail: 每页同属性同字只留一个探针以控制内存; 主字号与旋转占比按去重后计数,
                        # 与解析层逐字计数略有出入, 需要精确时改为逐字探针
                        probes.setdefault((id(attrs), char), _CharProbe(char, attrs))
    char_pages = [(page, list(probes.values())) for page, probes in sorted(per_page.items())]
    groups = learn_watermark_text_groups(
        char_pages, min_repeat_pages=min_repeat_pages, repeat_ratio=repeat_ratio,
    )
    return WatermarkPolicy(text_groups={(g.fontname, g.size, g.color_key): g for g in groups})


def _strip_line(line: QaLine, policy: Any, cache: dict[_AttrKey, dict[str, Any]]) -> tuple[QaLine | None, int]:
    if not line.spans:
        return line, 0
    removed = 0
    spans: list[QaSpan] = []
    for span in line.spans:
        attrs = _span_attrs(cache, span, line.rotated)
        text = "".join(
            char for char in span.text
            if not policy.is_watermark_char(_CharProbe(char, attrs))
        )
        removed += len(span.text) - len(text)
        if text.strip():
            spans.append(replace(span, text=text))
    if not removed:
        return line, 0
    text = "".join(span.text for span in spans)
    if not text.strip():
        return None, removed
    return replace(line, text=text, spans=tuple(spans)), removed


def strip_text_layer_watermark(blocks: list[QaBlock], page_count: int) -> tuple[list[QaBlock], int]:
    """学到水印字符组时逐字剔除, 返回 (新块列表, 剔除字符数); 没学到或过滤关闭时原样返回。"""
    from app.core.config import settings

    if not bool(getattr(settings, "WATERMARK_FILTER_ENABLED", True)):
        return blocks, 0
    policy = learn_text_layer_watermark(
        blocks,
        page_count,
        min_repeat_pages=int(getattr(settings, "WATERMARK_MIN_REPEAT_PAGES", 3)),
        repeat_ratio=float(getattr(settings, "WATERMARK_REPEAT_RATIO", 0.6)),
    )
    if not policy.text_groups:
        return blocks, 0

    cache: dict[_AttrKey, dict[str, Any]] = {}
    total = 0
    kept: list[QaBlock] = []
    for block in blocks:
        lines: list[QaLine] = []
        changed = False
        for line in block.lines:
            stripped, removed = _strip_line(line, policy, cache)
            total += removed
            changed = changed or bool(removed)
            if stripped is not None:
                lines.append(stripped)
        if not changed:
            kept.append(block)
            continue
        text = "\n".join(line.text for line in lines).strip()
        if text:
            kept.append(replace(
                block,
                text=text,
                lines=tuple(lines),
                spans=tuple(span for line in lines for span in line.spans),
            ))
    return kept, total
