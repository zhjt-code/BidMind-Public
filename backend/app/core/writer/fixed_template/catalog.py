"""固定模板 LLM-first 目录构建入口。

职责: 编排"范围定位 -> LLM 识别 -> 后置校验"三段流程，输出固定模板目录。
不做: 不访问数据库、不生成章节正文、不维护 LLM Center 元数据。
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from app.core.writer.fixed_template.llm_recognizer import recognize_templates
from app.core.writer.fixed_template.models import FixedTemplateEntry
from app.core.writer.fixed_template.post_validator import validate_entries
from app.core.writer.fixed_template.rule_fallback import build_rule_fallback_entries
from app.core.writer.fixed_template.section_locator import (
    locate_format_chapter_ranges,
    render_chunks_for_ranges,
)


def build_fixed_template_catalog(chunks: list[Any]) -> list[FixedTemplateEntry]:
    """同步兼容入口。

 LLM-first catalog 必须通过 async 入口调用。同步路径不再做规则兜底，
 避免重新引入 的全文扫描弱规则。
 """
    logger.warning("fixed_template catalog 同步路径不支持 LLM-first 识别, 返回空列表")
    return []


async def build_fixed_template_catalog_async(
    chunks: list[Any],
    *,
    project_id: Optional[int] = None,
) -> list[FixedTemplateEntry]:
    """异步入口: 范围定位 + 单次 LLM 识别 + 后置校验, 必要时启用规则兜底。: LLM 主路径完全空白时启用受限规则兜底 (rule_fallback), 避免
 单点失败导致 catalog 整段为空、模板章节渲染为 ``[固定模板内容待补充]``。
 兜底 entry source=rule_only / confidence=0.55, 由下游自动打 needs_review。: LLM 主路径成功但**部分识别**时(漏整键或漏某卷),
 启用键级增量兜底: 规则兜底扫描全部 ranges, 凡 (template_key x volume)
 未被 LLM 覆盖的条目并入(source=rule_only 低置信, 下游自动 needs_review),
 不动 LLM 正确识别的部分。为 LLM 单次识别的随机性提供确定性下界。
 """
    ranges = locate_format_chapter_ranges(chunks)
    chunks_text = render_chunks_for_ranges(chunks, ranges)
    min_page, max_page = _page_bounds(chunks)
    if not chunks_text.strip():
        logger.warning(
            "fixed_template catalog: LLM 输入为空, 启用规则兜底 project={}",
            project_id,
        )
        return build_rule_fallback_entries(
            chunks, ranges, min_page=min_page, max_page=max_page,
        )
    raw_entries = await recognize_templates(chunks_text, ranges, project_id=project_id)
    entries: list[FixedTemplateEntry] = []
    if raw_entries:
        entries = validate_entries(raw_entries, min_page=min_page, max_page=max_page)
    if not entries:
        logger.warning(
            "fixed_template catalog: LLM 主路径 0 输出, 启用规则兜底 project={} raw_entries={}",
            project_id,
            len(raw_entries),
        )
        entries = build_rule_fallback_entries(
            chunks, ranges, min_page=min_page, max_page=max_page,
        )
    else:
        entries = _fill_recall_gaps(
            entries, chunks, ranges, min_page=min_page, max_page=max_page,
        )
    logger.info(
        "fixed_template catalog 构建完成 project={} ranges={} raw_entries={} entries={} sources={}",
        project_id,
        [(item.page_start, item.page_end, item.volume_type) for item in ranges],
        len(raw_entries),
        len(entries),
        sorted({entry.source for entry in entries}),
    )
    return entries


def _fill_recall_gaps(
    entries: list[FixedTemplateEntry],
    chunks: list[Any],
    ranges: list[Any],
    *,
    min_page: int,
    max_page: int,
) -> list[FixedTemplateEntry]:
    """键级增量兜底: 规则兜底全量扫描 ranges, 补齐 LLM 未覆盖的 (key, volume) 条目。

 职责: 为 LLM 单次识别提供确定性下界——词典可识别的模板无论 LLM 本轮
 是否输出, catalog 都不缺席; 补入条目 source=rule_only / 低置信,
 由下游自动打 needs_review 人工核对。
 不做: 不动 LLM 已识别的 entry; 不识别词典外模板(custom key 仅 LLM 通道)。: 补"已识别 key 的缺失卷"; 扩展: LLM 整键漏识别
 (如同文件另一轮运行漏掉保证金模板)同样补齐, 消除运行间随机性。
 """
    candidates = build_rule_fallback_entries(
        chunks, ranges, min_page=min_page, max_page=max_page,
    )
    if not candidates:
        return entries

    def _covered(candidate: FixedTemplateEntry) -> bool:
        for entry in entries:
            if entry.template_key != candidate.template_key:
                continue
            # 任一侧未分卷视为覆盖全卷, 避免对早期无卷数据重复补建
            if (
                entry.volume_type is None
                or candidate.volume_type is None
                or entry.volume_type == candidate.volume_type
            ):
                return True
        return False

    fallback_added = [item for item in candidates if not _covered(item)]
    if not fallback_added:
        return entries
    logger.warning(
        "fixed_template 键级召回兜底补入: {} (LLM 已识别 {})",
        [(item.template_key, item.volume_type) for item in fallback_added],
        sorted({(entry.template_key, entry.volume_type) for entry in entries}),
    )
    merged = list(entries) + fallback_added
    merged.sort(
        key=lambda item: (
            item.source_page_start or 0,
            item.template_key,
            item.volume_type or "",
        ),
    )
    return merged


def _page_bounds(chunks: list[Any]) -> tuple[int, int]:
    pages = [
        int(page)
        for chunk in chunks
        if (page := getattr(chunk, "page_num", None)) is not None
    ]
    if not pages:
        return 1, 1
    return min(pages), max(pages)


__all__ = [
    "build_fixed_template_catalog",
    "build_fixed_template_catalog_async",
]
