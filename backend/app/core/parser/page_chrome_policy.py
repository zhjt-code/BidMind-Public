"""PDF 页眉页脚公共过滤策略。

职责: 基于跨页重复信号识别 PDF 页眉页脚区域，并输出可复用的正文裁剪与元素过滤决策。
不做: 不调用 OCR/VL、不做表格结构解析、不做语义分块或业务提取。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

Rect = tuple[float, float, float, float]

_NOISE_RE = re.compile(r"[\s\u3000·•・,，。.;；、:：\"'“”‘’()\[\]【】\-—_]+")
_DIGIT_RE = re.compile(r"\d+")
_TABLE_HEADER_TERMS = (
    "评分",
    "评审",
    "因素",
    "标准",
    "分值",
    "得分",
    "权重",
    "项目",
    "内容",
)
_EVIDENCE_TERMS = (
    "营业执照",
    "授权书",
    "证书",
    "资质",
    "合同",
    "承诺函",
    "身份证",
    "许可证",
    "证明",
    "复印件",
    "扫描件",
)
_SCORING_EVIDENCE_TERMS = (
    "评分",
    "评审",
    "分值",
    "得分",
    "满分",
    "扣分",
    "不得分",
    "报价",
    "商务部分",
    "技术部分",
    "质量保证",
    "售后服务",
    "业绩",
    "服务团队",
)


@dataclass(frozen=True)
class PageChromeDecision:
    """单页页眉页脚识别结果。"""

    page_num: int
    width: float
    height: float
    header_bottom: float = 0.0
    footer_top: float | None = None
    chrome_text_signatures: set[str] = field(default_factory=set)
    chrome_image_signatures: set[str] = field(default_factory=set)

    @property
    def body_bbox(self) -> Rect:
        """返回正文区域 bbox，坐标系与 pdfplumber / fitz 页坐标一致。"""

        footer_top = self.footer_top if self.footer_top is not None else self.height
        top = min(max(self.header_bottom, 0.0), self.height)
        bottom = min(max(footer_top, top), self.height)
        return (0.0, top, self.width, bottom)


@dataclass(frozen=True)
class PageChromeSummary:
    """Page Chrome 策略摘要，供日志与任务进度展示。"""

    pages: int = 0
    cropped_pages: int = 0
    repeated_text_signatures: int = 0
    repeated_image_signatures: int = 0
    filtered_images: int = 0

    def to_dict(self) -> dict[str, int]:
        """序列化为普通 dict。"""

        return {
            "pages": self.pages,
            "cropped_pages": self.cropped_pages,
            "repeated_text_signatures": self.repeated_text_signatures,
            "repeated_image_signatures": self.repeated_image_signatures,
            "filtered_images": self.filtered_images,
        }


@dataclass
class PageChromePolicy:
    """跨页重复页眉页脚策略。"""

    decisions: dict[int, PageChromeDecision] = field(default_factory=dict)
    summary: PageChromeSummary = field(default_factory=PageChromeSummary)

    def get_body_bbox(self, page_num: int) -> Rect:
        """获取指定页的正文区域；无策略时返回整页区域。"""

        decision = self.decisions.get(page_num)
        if decision is None:
            return (0.0, 0.0, 0.0, 0.0)
        return decision.body_bbox

    def clean_text(self, page_num: int, text: str) -> str:
        """删除已判定为页眉页脚的重复文本行。"""

        if not text:
            return text
        decision = self.decisions.get(page_num)
        if decision is None or not decision.chrome_text_signatures:
            return text

        kept: list[str] = []
        for raw_line in text.splitlines():
            sig = normalize_text_signature(raw_line)
            if sig and sig in decision.chrome_text_signatures:
                continue
            kept.append(raw_line)
        return "\n".join(kept)

    def filter_images(self, page_num: int, images: Iterable[Any]) -> list[Any]:
        """过滤落在重复页眉页脚区域内的图片。"""

        decision = self.decisions.get(page_num)
        if decision is None:
            return list(images)

        kept: list[Any] = []
        filtered = 0
        for image in images:
            if self._is_chrome_image(decision, image):
                filtered += 1
                continue
            kept.append(image)

        if filtered:
            self.summary = PageChromeSummary(
                pages=self.summary.pages,
                cropped_pages=self.summary.cropped_pages,
                repeated_text_signatures=self.summary.repeated_text_signatures,
                repeated_image_signatures=self.summary.repeated_image_signatures,
                filtered_images=self.summary.filtered_images + filtered,
            )
        return kept

    def _is_chrome_image(self, decision: PageChromeDecision, image: Any) -> bool:
        bbox = _coerce_bbox(getattr(image, "bbox", None))
        if bbox is None:
            return False

        sig = image_signature(image, decision.width, decision.height)
        if sig not in decision.chrome_image_signatures:
            return False

        _, y0, _, y1 = bbox
        if decision.header_bottom and y1 <= decision.header_bottom + 1:
            return True
        footer_top = decision.footer_top
        if footer_top is not None and y0 >= footer_top - 1:
            return True
        if _has_evidence_context(image):
            return False
        return False


@dataclass(frozen=True)
class _Element:
    page_num: int
    bbox: Rect
    signature: str
    side: str
    kind: str


def learn_page_chrome(
    pages: Iterable[Any],
    *,
    page_images: dict[int, list[Any]] | None = None,
    min_repeat_pages: int | None = None,
    repeat_ratio: float = 0.6,
    candidate_band_ratio: float = 0.25,
) -> PageChromePolicy:
    """从页面集合中学习跨页重复的页眉页脚区域。"""

    page_list = list(pages)
    if not page_list:
        return PageChromePolicy()

    total_pages = len(page_list)
    repeat_floor = min_repeat_pages if min_repeat_pages is not None else 3
    threshold = max(2, repeat_floor, math.ceil(total_pages * repeat_ratio))
    candidate_band_ratio = min(max(candidate_band_ratio, 0.05), 0.35)

    text_candidates: list[_Element] = []
    image_candidates: list[_Element] = []
    page_dims: dict[int, tuple[float, float]] = {}

    for idx, page in enumerate(page_list, start=1):
        page_num = _page_num(page, idx)
        width, height = _page_size(page)
        page_dims[page_num] = (width, height)
        for bbox, text in _extract_text_blocks(page):
            side = _candidate_side(bbox, height, candidate_band_ratio)
            if side is None:
                continue
            if _looks_like_table_header(text):
                continue
            sig = normalize_text_signature(text)
            if not sig or _has_business_text(text):
                continue
            kind = "text_line" if _is_low_information_signature(sig) else "text"
            text_candidates.append(_Element(page_num, bbox, sig, side, kind))

        for image in (page_images or {}).get(page_num, []):
            bbox = _coerce_bbox(getattr(image, "bbox", None))
            if bbox is None:
                continue
            side = _candidate_side(bbox, height, candidate_band_ratio)
            if side is None:
                continue
            sig = image_signature(image, width, height)
            if sig:
                image_candidates.append(_Element(page_num, bbox, sig, side, "image"))

    repeated_text = _repeated_signatures(text_candidates, threshold)
    repeated_images = _repeated_signatures(image_candidates, threshold)

    by_page: dict[int, dict[str, Any]] = {
        page_num: {
            "header_bottom": 0.0,
            "footer_top": height,
            "texts": set(),
            "images": set(),
        }
        for page_num, (_width, height) in page_dims.items()
    }

    for elem in text_candidates:
        if elem.signature not in repeated_text:
            continue
        _apply_element(by_page[elem.page_num], elem)

    for elem in image_candidates:
        if elem.signature not in repeated_images:
            continue
        _apply_element(by_page[elem.page_num], elem)

    decisions: dict[int, PageChromeDecision] = {}
    cropped = 0
    for page_num, data in by_page.items():
        width, height = page_dims[page_num]
        header_bottom = float(data["header_bottom"])
        footer_top = float(data["footer_top"])
        if header_bottom > 0 or footer_top < height:
            cropped += 1
        decisions[page_num] = PageChromeDecision(
            page_num=page_num,
            width=width,
            height=height,
            header_bottom=header_bottom,
            footer_top=footer_top,
            chrome_text_signatures=set(data["texts"]),
            chrome_image_signatures=set(data["images"]),
        )

    return PageChromePolicy(
        decisions=decisions,
        summary=PageChromeSummary(
            pages=total_pages,
            cropped_pages=cropped,
            repeated_text_signatures=len(repeated_text),
            repeated_image_signatures=len(repeated_images),
        ),
    )


def crop_pdfplumber_page(page: Any, policy: PageChromePolicy, page_num: int) -> Any:
    """按正文 bbox 裁剪 pdfplumber 页面；无有效裁剪时返回原页面。"""

    bbox = policy.get_body_bbox(page_num)
    if bbox == (0.0, 0.0, 0.0, 0.0):
        return page
    _, top, _, bottom = bbox
    page_height = float(getattr(page, "height", 0.0) or 0.0)
    if top <= 0 and (page_height <= 0 or bottom >= page_height):
        return page
    try:
        return page.crop(bbox)
    except Exception:
        return page


def filter_page_images(
    images: Iterable[Any],
    policy: PageChromePolicy,
    page_num: int,
) -> list[Any]:
    """函数式封装，便于调用点不感知策略对象细节。"""

    return policy.filter_images(page_num, images)


def clip_rect_for_page_render(
    policy: PageChromePolicy | None,
    page_num: int,
) -> Rect | None:
    """返回整页渲染应使用的正文 clip。"""

    if policy is None:
        return None
    bbox = policy.get_body_bbox(page_num)
    if bbox == (0.0, 0.0, 0.0, 0.0):
        return None
    _, top, _, bottom = bbox
    if top <= 0 and bottom >= policy.decisions[page_num].height:
        return None
    return bbox


def normalize_text_signature(text: str) -> str:
    """生成跨页重复文本签名，弱化页码和日期数字差异。"""

    normalized = _DIGIT_RE.sub("#", text or "")
    return _NOISE_RE.sub("", normalized).lower()


def image_signature(image: Any, page_width: float, page_height: float) -> str:
    """生成图片跨页重复签名。"""

    bbox = _coerce_bbox(getattr(image, "bbox", None))
    if bbox is None or page_width <= 0 or page_height <= 0:
        return ""
    x0, y0, x1, y1 = bbox
    x0 = min(max(x0, 0.0), page_width)
    x1 = min(max(x1, 0.0), page_width)
    y0 = min(max(y0, 0.0), page_height)
    y1 = min(max(y1, 0.0), page_height)
    width = float(getattr(image, "width", 0) or 0)
    height = float(getattr(image, "height", 0) or 0)
    rel = (
        _ratio_bucket(x0, page_width),
        _ratio_bucket(y0, page_height),
        _ratio_bucket(x1, page_width),
        _ratio_bucket(y1, page_height),
        _ratio_bucket(width, max(page_width, 1.0)),
        _ratio_bucket(height, max(page_height, 1.0)),
    )
    image_format = str(getattr(image, "image_format", "") or "").lower()
    return f"{image_format}:{rel}"


def _ratio_bucket(value: float, denominator: float) -> float:
    bucket = round(value / denominator, 1)
    return 0.0 if abs(bucket) < 0.05 else bucket


def _apply_element(data: dict[str, Any], elem: _Element) -> None:
    _x0, y0, _x1, y1 = elem.bbox
    if elem.kind != "text_line":
        if elem.side == "top":
            data["header_bottom"] = max(float(data["header_bottom"]), y1)
        else:
            data["footer_top"] = min(float(data["footer_top"]), y0)
    if elem.kind == "text":
        data["texts"].add(elem.signature)
    elif elem.kind == "text_line":
        data["texts"].add(elem.signature)
    else:
        data["images"].add(elem.signature)


def _repeated_signatures(elements: list[_Element], threshold: int) -> set[str]:
    pages_by_sig: dict[str, set[int]] = {}
    for elem in elements:
        pages_by_sig.setdefault(elem.signature, set()).add(elem.page_num)
    return {sig for sig, page_nums in pages_by_sig.items() if len(page_nums) >= threshold}


def _candidate_side(
    bbox: Rect,
    page_height: float,
    candidate_band_ratio: float,
) -> str | None:
    _x0, y0, _x1, y1 = bbox
    center_y = (y0 + y1) / 2
    if center_y <= page_height * candidate_band_ratio:
        return "top"
    if center_y >= page_height * (1 - candidate_band_ratio):
        return "bottom"
    return None


def _extract_text_blocks(page: Any) -> list[tuple[Rect, str]]:
    raw_blocks = getattr(page, "text_blocks", None)
    if raw_blocks is not None:
        return [
            ((float(x0), float(y0), float(x1), float(y1)), str(text or ""))
            for x0, y0, x1, y1, text in raw_blocks
            if str(text or "").strip()
        ]

    extract_words = getattr(page, "extract_words", None)
    if not callable(extract_words):
        return []

    try:
        words = extract_words()
    except Exception:
        return []

    lines: dict[int, list[dict[str, Any]]] = {}
    for word in words or []:
        text = str(word.get("text") or "").strip()
        if not text:
            continue
        top = float(word.get("top", 0.0) or 0.0)
        key = round(top / 3)
        lines.setdefault(key, []).append(word)

    blocks: list[tuple[Rect, str]] = []
    for line_words in lines.values():
        ordered = sorted(line_words, key=lambda item: float(item.get("x0", 0.0) or 0.0))
        text = "".join(str(item.get("text") or "") for item in ordered)
        x0 = min(float(item.get("x0", 0.0) or 0.0) for item in ordered)
        y0 = min(float(item.get("top", 0.0) or 0.0) for item in ordered)
        x1 = max(float(item.get("x1", 0.0) or 0.0) for item in ordered)
        y1 = max(float(item.get("bottom", y0) or y0) for item in ordered)
        blocks.append(((x0, y0, x1, y1), text))
    return blocks


def _page_num(page: Any, fallback: int) -> int:
    value = getattr(page, "page_num", None)
    if value is None:
        value = getattr(page, "page_number", None)
    try:
        return int(value or fallback)
    except Exception:
        return fallback


def _page_size(page: Any) -> tuple[float, float]:
    width = float(getattr(page, "width", 0.0) or 0.0)
    height = float(getattr(page, "height", 0.0) or 0.0)
    return width, height


def _coerce_bbox(value: Any) -> Rect | None:
    if not value or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = value
        return (float(x0), float(y0), float(x1), float(y1))
    except Exception:
        return None


def _looks_like_table_header(text: str) -> bool:
    compact = normalize_text_signature(text)
    if ("评分" in compact or "评审" in compact) and any(
        term in compact for term in ("细则", "标准", "分值", "因素", "内容")
    ):
        return True
    hits = sum(1 for term in _TABLE_HEADER_TERMS if term in compact)
    return hits >= 3


def _has_business_text(text: str) -> bool:
    """业务高价值文本不得作为页眉页脚候选。"""

    compact = normalize_text_signature(text)
    return any(term in compact for term in _SCORING_EVIDENCE_TERMS)


def _is_low_information_signature(signature: str) -> bool:
    """低信息量重复文本只允许行级过滤，不驱动区域裁剪。"""

    sig = str(signature or "").strip()
    if not sig:
        return True
    if len(sig) <= 3:
        return True
    non_mark_chars = [ch for ch in sig if ch != "#"]
    if not non_mark_chars:
        return True
    if len(non_mark_chars) <= 2 and "#" in sig:
        return True
    return False


def _has_evidence_context(image: Any) -> bool:
    context = str(getattr(image, "spatial_context", "") or "")
    return any(term in context for term in _EVIDENCE_TERMS)


__all__ = [
    "PageChromeDecision",
    "PageChromePolicy",
    "PageChromeSummary",
    "Rect",
    "clip_rect_for_page_render",
    "crop_pdfplumber_page",
    "filter_page_images",
    "image_signature",
    "learn_page_chrome",
    "normalize_text_signature",
]
