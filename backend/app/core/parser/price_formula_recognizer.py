"""价格公式段落识别器（+ Task4B）。

职责: 在评分区段及其尾部相邻页中识别公式型价格评分段落, 渲染为 canonical scoring_table chunk。
不做: 不解析表格、不从公式猜满分(满分仅取卷定义权威来源或转置轴期望)、不调用 LLM/DB。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from app.core.extractor.scoring.volume_parser import parse_flat_volume_composition

if TYPE_CHECKING:
    from app.core.parser.pdf_parser import ParsedDocument
    from app.core.parser.scoring_axis_chunk_source import PriceFormulaContext


# Task4B: 增加"价格标评审方法"作为合法价格评审标题形态。
_TITLE_RE = re.compile(r"(?:报价|价格)\s*评分标准|价格标评审方法")
_PRICE_ITEM_TITLE_RE = re.compile(r"价格项")
_FORMULA_RE = re.compile(r"得分\s*=")
_FORMULA_TERMS = ("基准价", "评标价", "评标总价")
_EXPLICIT_PRICE_SCORE_RE = re.compile(r"价格项.{0,30}?满分为\s*(\d+(?:\.\d+)?)\s*分")
_PRICE_ITEM_NAME = "报价评分"
_PRICE_GROUP = "价格部分"
_TAIL_SCAN_PAGES = 3
# 缺口B(样本10): "价格项(F1×A1)满分为10分+公式"常写在评分明细表之前的总述页,
# 区段起点由表信号回扫确定时不含该页, 向前回看有限页数。
_HEAD_SCAN_PAGES = 2


@dataclass(frozen=True)
class RecognizedPriceFormula:
    """识别出的价格公式评分段落。"""

    page_num: int
    score: float
    text: str
    markdown: str
    lot_label: str | None = None  # Task4B: 绑定的标段，payload 写 lot


def recognize_price_formula_sections(
    parsed_doc: "ParsedDocument",
    sections: list,
    volume_context_text: str,
    *,
    tail_scan_pages: int = _TAIL_SCAN_PAGES,
    price_contexts: "list[PriceFormulaContext] | None" = None,
) -> list[RecognizedPriceFormula]:
    """从评分区段及尾部相邻页识别公式型价格评分段落。

 满分三来源严格解析（I2: fail-closed on conflict）：
 1. 行内显式 "价格项…满分为N分" 声明
 2. 转置轴 PriceFormulaContext.price_score（Task4B）
 3. 卷定义声明句 price 卷基准（parse_flat_volume_composition）
 同 scope 多个非空值不一致 → fail-closed（不生成 chunk），logger.warning 诊断。
 """
    vol_def_score = _extract_vol_def_score(volume_context_text)
    pages_by_num = {page.page_num: page for page in parsed_doc.pages}
    if _looks_like_lot_boundaries(sections):
        out: list[RecognizedPriceFormula] = []
        for lot in sections:
            lot_label = getattr(lot, "label", None)
            # I3: 严格 lot 匹配，不回退到其他 lot 或全局 scope
            axis_score = _extract_axis_score(lot_label, price_contexts, strict_lot=True)
            recognized = _recognize_in_pages(
                pages_by_num,
                range(int(lot.page_start), int(lot.page_end) + 1),
                axis_score=axis_score,
                vol_def_score=vol_def_score,
                lot_label=lot_label,
            )
            if recognized is not None:
                out.append(recognized)
        return out

    # 单一 scope 路径
    axis_score = _extract_axis_score(None, price_contexts, strict_lot=False)
    pages = _scan_page_numbers(sections, tail_scan_pages=tail_scan_pages)
    recognized = _recognize_in_pages(
        pages_by_num,
        pages,
        axis_score=axis_score,
        vol_def_score=vol_def_score,
        lot_label=None,
    )
    return [recognized] if recognized is not None else []


def _extract_axis_score(
    lot_label: str | None,
    price_contexts: "list[PriceFormulaContext] | None",
    *,
    strict_lot: bool,
) -> float | None:
    """从转置轴上下文提取满分期望值。

    I3: strict_lot=True 时只取精确 lot 匹配，禁止跨 lot 回退，防止价格上下文污染。
    strict_lot=False（单一 scope）时优先 lot_label=None，否则取第一个。
    """
    if not price_contexts:
        return None
    if strict_lot and lot_label is not None:
        ctx = next((c for c in price_contexts if c.lot_label == lot_label), None)
    else:
        ctx = next(
            (c for c in price_contexts if c.lot_label is None),
            price_contexts[0] if price_contexts else None,
        )
    return float(ctx.price_score) if ctx is not None and ctx.price_score > 0 else None


def _extract_vol_def_score(volume_context_text: str) -> float | None:
    """从卷定义声明句提取 price 满分基准。"""
    price_def = parse_flat_volume_composition(volume_context_text).get("price")
    return float(price_def.volume_max) if price_def is not None and price_def.volume_max > 0 else None


def _resolve_price_score_strict(
    inline: float | None,
    axis_score: float | None,
    vol_def_score: float | None,
    lot_label: str | None,
    page_num: int,
) -> float | None:
    """三来源满分按证据可信度分级解析（I2 修订, -R6）。

 规则：
 - 多个非空来源值相同 → 去重后使用
 - 多个非空来源值不同，按证据直接性分级：
 - 冲突仅来自 vol_def（且 axis 缺失或与 inline 一致）→ 采用 inline。
 vol_def 来自累积的卷定义字典，多文档/多包场景可能被跨范围值污染，
 不得压制当前页显式文字声明。
 - inline 与 axis 冲突（同文档双显式证据矛盾）或无 inline → fail-closed
 - 单一非空来源 → 直接使用
 """
    sources: dict[str, float] = {}
    if inline is not None and inline > 0:
        sources["inline"] = inline
    if axis_score is not None and axis_score > 0:
        sources["axis"] = axis_score
    if vol_def_score is not None and vol_def_score > 0:
        sources["vol_def"] = vol_def_score

    if not sources:
        return None

    unique_values = set(sources.values())
    if len(unique_values) > 1:
        inline_val = sources.get("inline")
        axis_val = sources.get("axis")
        if inline_val is not None and (axis_val is None or axis_val == inline_val):
            logger.warning(
                "[价格满分] vol_def 与本页显式声明不一致 inline优先: lot={} sources={}",
                lot_label,
                dict(sources),
            )
            return inline_val
        logger.warning(
            "[价格满分] 同scope多来源不一致 fail-closed: lot={} sources={}",
            lot_label,
            dict(sources),
        )
        return None

    return next(iter(unique_values))


def _recognize_in_pages(
    pages_by_num: dict[int, object],
    pages: "range | list[int]",
    *,
    axis_score: float | None,
    vol_def_score: float | None,
    lot_label: str | None,
) -> RecognizedPriceFormula | None:
    for page_num in pages:
        page = pages_by_num.get(page_num)
        if page is None:
            continue
        text = _normalize_text(getattr(page, "text", "") or "")
        if not _is_price_formula_text(text):
            continue
        inline = _explicit_price_score(text)
        score = _resolve_price_score_strict(inline, axis_score, vol_def_score, lot_label, page_num)
        if score is None or score <= 0:
            continue
        markdown = _build_markdown(
            page_num=page_num, score=score, text=text, lot_label=lot_label
        )
        return RecognizedPriceFormula(
            page_num=page_num,
            score=score,
            text=text,
            markdown=markdown,
            lot_label=lot_label,
        )
    return None


def _scan_page_numbers(sections: list, *, tail_scan_pages: int) -> list[int]:
    if not sections:
        return []
    start = min(int(getattr(sec, "page_start", 0) or 0) for sec in sections)
    end = max(int(getattr(sec, "page_end", 0) or 0) for sec in sections)
    if start < 1 or end < start:
        return []
    return list(range(max(1, start - _HEAD_SCAN_PAGES), end + tail_scan_pages + 1))


def _is_price_formula_text(text: str) -> bool:
    """判断页文本是否为公式型价格评分页。

    必须同时满足：标题信号 + 得分公式 + 基准价语义。
    任一缺失均不产出（守住"不从系数猜分"红线）。
    """
    has_title = bool(_TITLE_RE.search(text)) or (
        bool(_PRICE_ITEM_TITLE_RE.search(text)) and _explicit_price_score(text) is not None
    )
    return has_title and bool(_FORMULA_RE.search(text)) and any(
        term in text for term in _FORMULA_TERMS
    )


def _explicit_price_score(text: str) -> float | None:
    match = _EXPLICIT_PRICE_SCORE_RE.search(text or "")
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def _looks_like_lot_boundaries(sections: list) -> bool:
    return bool(sections) and all(hasattr(sec, "label") for sec in sections)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _build_markdown(*, page_num: int, score: float, text: str, lot_label: str | None = None) -> str:
    safe_text = text.replace("|", "/")
    header = f"[评分表 第{page_num}-{page_num}页]"
    if lot_label:
        header = header.rstrip("]") + f" · {lot_label}]"
    return "\n".join(
        [
            header,
            "| 分组 | 评分因素 | 评分内容与标准 | 分值 |",
            "| --- | --- | --- | --- |",
            f"| {_PRICE_GROUP} |  |  |  |",
            f"|  | {_PRICE_ITEM_NAME} | {safe_text} | {score:g}分 |",
        ]
    )


__all__ = ["RecognizedPriceFormula", "recognize_price_formula_sections"]
