"""P2-1: 串标专用的文档结构提取器。

为了支撑 D6FormatAlgorithmicGate，需要采集以下 4 类"非内容"结构特征：
  - toc_headings:    目录 / 标题列表（顺序）
  - font_families:   使用到的字体族集合
  - margins:         页边距（单位 mm，键 top/bottom/left/right）
  - headers_footers: 页眉 + 页脚文本列表

本模块**不修改现有 parser**，避免影响 investigate / writer 流程；而是基于
已经存在的解析基础件（pdfplumber + python-docx）做轻量二次采集：

* PDF  →  pdfplumber: page.cropbox/bbox 推算 margins；chars[].fontname 聚合
* DOCX →  python-docx: section.*_margin（EMU→mm）、paragraph.runs.font.name、
          paragraph.style.name 中标题级别识别 toc、section.header/footer

任何异常都被吞掉并返回空 dict，让 D6 Gate 走"结构信息不可用→pass"的兜底分支。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from loguru import logger

_EMU_PER_MM = 36000  # python-docx: Length in EMU；1 mm = 36000 EMU

# pdfplumber 单位为 PostScript point (1/72 inch)；1 inch = 25.4 mm → 1 pt ≈ 0.3528 mm
_PT_PER_MM = 72.0 / 25.4

# DOCX 标题 style 名称前缀
_HEADING_STYLE_PREFIXES = ("Heading", "标题", "heading")

# 标题最大采样数（避免 toc 过长）
_MAX_HEADINGS = 80


def extract_docx_structure(file_path: str | None) -> dict[str, Any]:
    """根据后缀派发到 PDF/DOCX 结构提取。失败返回空 dict。: 成功提取后经 `CollusionDocxStructure` 校验，保证字段类型/结构稳定；
 校验失败 warning + 返回空 dict，保持返回类型 dict，下游零破坏。
 """
    if not file_path:
        return {}
    try:
        suffix = Path(file_path).suffix.lower()
    except Exception:
        return {}

    raw: dict[str, Any] = {}
    try:
        if suffix == ".pdf":
            raw = _extract_pdf_structure(file_path)
        elif suffix == ".docx":
            raw = _extract_docx_structure(file_path)
        elif suffix == ".doc":
            # .doc 格式 python-docx 不支持，跳过
            return {}
        else:
            return {}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"[DocxStructureExtractor] 提取失败 file={file_path} err={exc}"
        )
        return {}

    if not raw:
        return {}

    try:
        from app.schemas.collusion import CollusionDocxStructure
        validated = CollusionDocxStructure.model_validate(raw)
        return validated.model_dump()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"[DocxStructureExtractor] docx_structure schema 校验失败 file={file_path} err={exc}"
        )
        return {}


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _extract_pdf_structure(file_path: str) -> dict[str, Any]:
    if not os.path.exists(file_path):
        return {}

    try:
        import pdfplumber
    except ImportError:
        return {}

    toc_headings: list[str] = []
    font_set: set[str] = set()
    margins_mm: dict[str, float] = {}
    headers_footers: list[str] = []

    with pdfplumber.open(file_path) as pdf:
        pages = pdf.pages or []
        if not pages:
            return {}

        # --- margins：用第一页 cropbox 相对 mediabox 的偏移推算（pt → mm） ---
        first = pages[0]
        try:
            mbox = first.mediabox  # [x0, y0, x1, y1]
            cbox = first.bbox  # 实际内容区域
            left_pt = max(0.0, float(cbox[0]) - float(mbox[0]))
            right_pt = max(0.0, float(mbox[2]) - float(cbox[2]))
            bottom_pt = max(0.0, float(cbox[1]) - float(mbox[1]))
            top_pt = max(0.0, float(mbox[3]) - float(cbox[3]))
            margins_mm = {
                "top": round(top_pt / _PT_PER_MM, 2),
                "bottom": round(bottom_pt / _PT_PER_MM, 2),
                "left": round(left_pt / _PT_PER_MM, 2),
                "right": round(right_pt / _PT_PER_MM, 2),
            }
        except Exception:
            margins_mm = {}

        # --- fonts：遍历前 10 页的 chars 聚合 fontname ---
        for page in pages[: min(10, len(pages))]:
            try:
                for ch in page.chars or []:
                    fn = str(ch.get("fontname") or "").strip()
                    if fn:
                        # 去掉 PDF 常见的"字符子集前缀"：如 "ABCDEF+SimSun"
                        if "+" in fn:
                            fn = fn.split("+", 1)[1]
                        font_set.add(fn)
            except Exception:
                continue

        # --- headers/footers：每页顶部/底部 10% 的文本 ---
        hf_candidates: dict[str, int] = {}
        for page in pages[: min(10, len(pages))]:
            try:
                page_height = float(page.height)
                top_threshold = page_height * 0.1
                bottom_threshold = page_height * 0.9
                for word in page.extract_words() or []:
                    y_top = float(word.get("top", 0))
                    text = str(word.get("text") or "").strip()
                    if not text:
                        continue
                    if y_top <= top_threshold or y_top >= bottom_threshold:
                        hf_candidates[text] = hf_candidates.get(text, 0) + 1
            except Exception:
                continue

        # 出现在 ≥2 页的顶部/底部文本视为页眉页脚
        for text, cnt in hf_candidates.items():
            if cnt >= 2 and len(text) <= 50:
                headers_footers.append(text)

        # --- toc_headings：用每页第一行或大字号行，采集前 N 条 ---
        try:
            # pdfplumber 没有直接的 outline API，这里退化为"所有页第一行 ≤60 字符"
            for page in pages[: min(30, len(pages))]:
                lines = (page.extract_text() or "").splitlines()
                first_non_empty = next((line.strip() for line in lines if line.strip()), None)
                if first_non_empty and len(first_non_empty) <= 60:
                    toc_headings.append(first_non_empty)
                if len(toc_headings) >= _MAX_HEADINGS:
                    break
        except Exception:
            pass

    return {
        "toc_headings": toc_headings,
        "font_families": sorted(font_set),
        "margins": margins_mm,
        "headers_footers": sorted(set(headers_footers)),
    }


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------

def _extract_docx_structure(file_path: str) -> dict[str, Any]:
    if not os.path.exists(file_path):
        return {}

    try:
        from docx import Document as DocxDocument
    except ImportError:
        return {}

    doc = DocxDocument(file_path)

    # --- toc_headings：style 名含 "Heading" 或 "标题" 的 paragraph ---
    toc_headings: list[str] = []
    for para in doc.paragraphs:
        try:
            style_name = str(para.style.name or "")
            if any(style_name.startswith(p) for p in _HEADING_STYLE_PREFIXES):
                text = (para.text or "").strip()
                if text:
                    toc_headings.append(text)
                    if len(toc_headings) >= _MAX_HEADINGS:
                        break
        except Exception:
            continue

    # --- font_families：遍历 paragraph.runs ---
    font_set: set[str] = set()
    for para in doc.paragraphs[:500]:  # 防爆量
        try:
            for run in para.runs:
                fn = run.font.name
                if fn:
                    font_set.add(str(fn).strip())
        except Exception:
            continue

    # --- margins：第一节 ---
    margins_mm: dict[str, float] = {}
    try:
        section = doc.sections[0]
        margins_mm = {
            "top": round(float(section.top_margin or 0) / _EMU_PER_MM, 2),
            "bottom": round(float(section.bottom_margin or 0) / _EMU_PER_MM, 2),
            "left": round(float(section.left_margin or 0) / _EMU_PER_MM, 2),
            "right": round(float(section.right_margin or 0) / _EMU_PER_MM, 2),
        }
    except Exception:
        margins_mm = {}

    # --- headers/footers：第一节 ---
    headers_footers: list[str] = []
    try:
        for section in doc.sections:
            for area in (section.header, section.footer):
                if area is None:
                    continue
                for para in area.paragraphs:
                    text = (para.text or "").strip()
                    if text:
                        headers_footers.append(text)
    except Exception:
        pass

    return {
        "toc_headings": toc_headings,
        "font_families": sorted(font_set),
        "margins": margins_mm,
        "headers_footers": sorted(set(headers_footers)),
    }
