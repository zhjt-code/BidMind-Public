"""固定模板 LLM 输出后置校验。

职责: 对 LLM 识别出的模板条目做 key/page/anchor sanity check 并转为 FixedTemplateEntry。
不做: 不调用 LLM、不定位章节、不访问数据库。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.core.writer.fixed_template.features import (
    extract_field_slots,
    extract_signature_slots,
    has_table_hint,
    is_directory_like,
    is_generic_template_requirement,
)
from app.core.writer.fixed_template.models import FixedTemplateEntry
from app.core.writer.fixed_template.prompts import FIXED_TEMPLATE_ANCHORS
from app.core.writer.fixed_template.raw_text_cleaner import clean_template_raw_text
from app.core.writer.fixed_template.titles import (
    KNOWN_TEMPLATE_KEYS,
    aliases_for_key,
    canonical_title,
    custom_template_key,
    infer_fixed_template_key,
)


_MIN_TEMPLATE_CHARS = 50
_COMMON_ANCHORS = ("致", "签字", "盖章", "公章", "日期", "投标人")
_VOLUME_VALUES = {"technical", "commercial"}
# 词典外 custom 模板置信度封顶, 使 is_complete_candidate=False,
# 经 binding 例外通道放行后由 template_route 自动打 needs_review 人工 gate。
_CUSTOM_KEY_CONFIDENCE_CAP = 0.7


def _normalize_template_key(raw_key: str, title: str) -> tuple[str | None, str]:
    """把 LLM 输出的 template_key 归一到词典 key 或开放 custom key。

 优先归一到 KNOWN_TEMPLATE_KEYS; 词典无法覆盖时降级为
 ``custom:<归一标题>``(: 封闭词典只做优先归一, 不做准入门槛)。
 返回 ``(canonical_key, reason)``; canonical_key 为 None 仅当标题过短无法
 构造 custom key, 调用方判定为拒绝。
 """
    raw_key = (raw_key or "").strip()
    inferred = infer_fixed_template_key(title)
    if raw_key in KNOWN_TEMPLATE_KEYS:
        return raw_key, "raw_key_canonical"
    for canonical in KNOWN_TEMPLATE_KEYS:
        if raw_key.startswith(f"{canonical}_") or raw_key.startswith(f"{canonical}."):
            return canonical, f"raw_key_prefix:{canonical}"
    if inferred:
        return inferred, "title_inferred"
    custom = custom_template_key(title)
    if custom:
        return custom, "custom_key_from_title"
    return None, "unknown_template_key"


def _normalize_volume_type(raw: object) -> str | None:
    """把 LLM 输出的 volume_type 归一为 technical / commercial / None。"""
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if text in _VOLUME_VALUES:
        return text
    return None


def validate_entries(
    raw_entries: list[dict[str, Any]],
    *,
    min_page: int,
    max_page: int,
) -> list[FixedTemplateEntry]:
    """把 LLM 原始条目转换为经过 sanity check 的 FixedTemplateEntry。"""
    entries: list[FixedTemplateEntry] = []
    for raw in raw_entries:
        entry = _build_entry(raw, min_page=min_page, max_page=max_page)
        if entry is not None:
            entries.append(entry)
    return _dedupe_entries(entries)


def _build_entry(
    raw: dict[str, Any],
    *,
    min_page: int,
    max_page: int,
) -> FixedTemplateEntry | None:
    raw_key = str(raw.get("template_key") or "").strip()
    title = str(raw.get("title") or "").strip()
    raw_text = str(raw.get("raw_text") or "").strip()
    page_start = _coerce_int(raw.get("source_page_start"))
    page_end = _coerce_int(raw.get("source_page_end")) or page_start
    if not raw_key or not title or not raw_text or page_start is None:
        return _reject(raw, "missing_required_field")
    template_key, key_reason = _normalize_template_key(raw_key, title)
    if template_key is None:
        return _reject(raw, f"template_key_unknown:{raw_key}")
    if page_start < min_page or page_start > max_page:
        return _reject(raw, f"page_out_of_range:{page_start}")
    if page_end and (page_end < min_page or page_end > max_page):
        return _reject(raw, f"page_end_out_of_range:{page_end}")
    if len(raw_text) < _MIN_TEMPLATE_CHARS:
        return _reject(raw, "raw_text_too_short")
    if is_directory_like(raw_text) or is_generic_template_requirement(raw_text):
        return _reject(raw, "directory_or_generic_requirement")
    try:
        cleaned = clean_template_raw_text(raw_text, title)
    except Exception as exc:  # noqa: BLE001
        logger.warning("fixed_template raw_text 清洗失败, 回退原文: {}", exc)
        cleaned = raw_text
    if cleaned and len(cleaned) >= _MIN_TEMPLATE_CHARS:
        raw_text = cleaned
    anchors = FIXED_TEMPLATE_ANCHORS.get(template_key, _COMMON_ANCHORS)
    if not _has_anchor(raw_text, anchors):
        return _reject(raw, "anchor_missing")
    if key_reason != "raw_key_canonical":
        logger.info(
            "fixed_template key 归一化: raw={!r} -> {!r} ({})",
            raw_key,
            template_key,
            key_reason,
        )
    confidence = _coerce_float(raw.get("confidence"), default=0.9)
    if key_reason == "custom_key_from_title":
        confidence = min(confidence, _CUSTOM_KEY_CONFIDENCE_CAP)
    return FixedTemplateEntry(
        template_key=template_key,
        title=canonical_title(template_key, title),
        aliases=aliases_for_key(template_key),
        source_page_start=page_start,
        source_page_end=page_end,
        raw_text=raw_text,
        field_slots=tuple(str(x) for x in raw.get("fields") or extract_field_slots(raw_text)),
        signature_slots=tuple(str(x) for x in raw.get("signature_slots") or extract_signature_slots(raw_text)),
        has_table=has_table_hint(raw_text),
        confidence=confidence,
        source="llm_only",
        volume_type=_normalize_volume_type(raw.get("volume_type")),
    )


def _has_anchor(text: str, anchors: tuple[str, ...]) -> bool:
    compact = text.replace(" ", "")
    return any(anchor and anchor.replace(" ", "") in compact for anchor in anchors)


def _reject(raw: dict[str, Any], reason: str) -> None:
    logger.warning(
        "fixed_template LLM 输出被后置校验拒绝: reason={}, title={!r}, key={!r}",
        reason,
        raw.get("title"),
        raw.get("template_key"),
    )
    return None


def _coerce_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _coerce_float(value: object, *, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _dedupe_entries(entries: list[FixedTemplateEntry]) -> list[FixedTemplateEntry]:
    best: dict[tuple[str, str | None, int | None], FixedTemplateEntry] = {}
    for entry in entries:
        key = (entry.template_key, entry.volume_type, entry.source_page_start)
        current = best.get(key)
        if current is None or entry.confidence > current.confidence:
            best[key] = entry
    return sorted(
        best.values(),
        key=lambda item: (
            item.source_page_start or 0,
            item.template_key,
            item.volume_type or "",
        ),
    )
