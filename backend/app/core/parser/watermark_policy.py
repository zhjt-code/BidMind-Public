"""PDF 水印预处理策略（根治层）。

职责: 基于"承载信息恒定 + 多页重复 + 弱显示属性"三重信号识别文字/图片水印，
 在文本与表格提取前做字符级/图片级预过滤。
不做: 不依赖水印位置/角度/单页实例数假设（用户约束: 角度无规律、一页可多实例）、
 不做 OCR/VL、不处理页眉页脚区域裁剪（page_chrome_policy 职责）、不删矢量绘图。

判定不变量(与页眉页脚同构的"跨页重复"学习框架, 但粒度为字符级):
- 多页重复: 同一字符属性组(字体+字号+颜色)在 >= 阈值比例页面复现;
- 信息恒定: 该组去重字符集合跨页稳定(每页覆盖并集的大部分), 且为短语级(<=上限字数)
 —— 平铺多实例水印每页实例数不同, 去重集合仍恒定;
- 弱显示属性(防误删跨页续表表头/正文的关键闸门): 非黑色系(灰/浅色) 或 旋转
 或 字号显著大于主字体, 任一命中即可; 黑色常规字号水平的表头/页码/正文永不命中。
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

# 水印文本是短语(如"<ORG_15>"=11字), 去重字符集超过该上限视为正文组
_MAX_CHARSET_SIZE = 30
# 每页该组字符集合对并集的最低平均覆盖率(容忍个别页渲染缺字)
_MIN_CHARSET_COVERAGE = 0.7
# 颜色分量达到该值视为"弱显示/非纯黑"(黑色正文约 0~0.25)
_LOW_INK_COMPONENT = 0.3
# 字号超过主字体该倍数视为水印级大字
_OVERSIZE_RATIO = 1.8
# 字符矩阵 b 分量绝对值超过该值视为旋转字符
_ROTATION_EPS = 0.05
# 图片水印: 面积占页面比例达到该值才候选(公章/小图不候选, 防误删证据图)
_IMAGE_MIN_AREA_RATIO = 0.35

_GroupKey = tuple[str, float, str]


@dataclass(frozen=True)
class WatermarkTextGroup:
    """一组被判定为水印的字符属性(字体+字号+颜色)及其承载字符集合。"""

    fontname: str
    size: float
    color_key: str
    charset: frozenset[str]
    trait: str
    pages_seen: int


@dataclass
class WatermarkSummary:
    """水印过滤摘要(fail-visible: 被过滤内容必须可追溯)。"""

    text_groups: int = 0
    image_signatures: int = 0
    filtered_chars: int = 0
    filtered_images: int = 0
    group_traits: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """序列化为解析 metadata 摘要。"""
        return {
            "text_groups": self.text_groups,
            "image_signatures": self.image_signatures,
            "filtered_chars": self.filtered_chars,
            "filtered_images": self.filtered_images,
            "group_traits": list(self.group_traits),
        }


@dataclass
class WatermarkPolicy:
    """学习到的水印过滤策略。"""

    text_groups: dict[_GroupKey, WatermarkTextGroup] = field(default_factory=dict)
    image_signatures: set[str] = field(default_factory=set)
    summary: WatermarkSummary = field(default_factory=WatermarkSummary)

    @property
    def has_rules(self) -> bool:
        """是否存在任一水印过滤规则。"""
        return bool(self.text_groups) or bool(self.image_signatures)

    def is_watermark_char(self, obj: dict) -> bool:
        """判定单个 pdfplumber char 对象是否为水印字符。"""
        group = self.text_groups.get(_char_group_key(obj))
        if group is None:
            return False
        return str(obj.get("text") or "") in group.charset

    def keep_object(self, obj: dict) -> bool:
        """pdfplumber page.filter 回调: 仅剔除水印字符, 其余对象(线/框/图)原样保留。"""
        if obj.get("object_type") != "char":
            return True
        if self.is_watermark_char(obj):
            self.summary.filtered_chars += 1
            return False
        return True

    def filter_page(self, page: Any) -> Any:
        """返回剔除水印字符后的 pdfplumber 页面; 无规则或过滤失败时返回原页。"""
        if not self.text_groups:
            return page
        try:
            return page.filter(self.keep_object)
        except Exception:
            return page

    def filter_images(
        self,
        images: Iterable[Any],
        page_width: float,
        page_height: float,
    ) -> list[Any]:
        """剔除跨页重复的大面积水印图片(有证据上下文的图片一律保留)。"""
        kept: list[Any] = []
        for image in images:
            if self._is_watermark_image(image, page_width, page_height):
                self.summary.filtered_images += 1
                continue
            kept.append(image)
        return kept

    def _is_watermark_image(self, image: Any, page_width: float, page_height: float) -> bool:
        if not self.image_signatures:
            return False
        from app.core.parser.page_chrome_policy import image_signature

        if image_signature(image, page_width, page_height) not in self.image_signatures:
            return False
        if _has_evidence_context(image):
            return False
        return _image_area_ratio(image, page_width, page_height) >= _IMAGE_MIN_AREA_RATIO


def learn_watermark_policy(
    pages: Iterable[Any],
    *,
    page_images: dict[int, list[Any]] | None = None,
    min_repeat_pages: int = 3,
    repeat_ratio: float = 0.6,
) -> WatermarkPolicy:
    """从 pdfplumber 页面集合学习水印策略(封装页对象读取, 判定核心为纯函数)。"""
    char_pages: list[tuple[int, list[dict]]] = []
    page_dims: dict[int, tuple[float, float]] = {}
    for idx, page in enumerate(pages, start=1):
        page_num = int(getattr(page, "page_number", idx) or idx)
        try:
            chars = list(getattr(page, "chars", None) or [])
        except Exception:
            chars = []
        char_pages.append((page_num, chars))
        page_dims[page_num] = (
            float(getattr(page, "width", 0.0) or 0.0),
            float(getattr(page, "height", 0.0) or 0.0),
        )

    groups = learn_watermark_text_groups(
        char_pages, min_repeat_pages=min_repeat_pages, repeat_ratio=repeat_ratio
    )
    image_sigs = _learn_watermark_image_signatures(
        page_images or {}, page_dims,
        min_repeat_pages=min_repeat_pages, repeat_ratio=repeat_ratio,
    )
    policy = WatermarkPolicy(
        text_groups={
            (g.fontname, g.size, g.color_key): g for g in groups
        },
        image_signatures=image_sigs,
    )
    policy.summary.text_groups = len(groups)
    policy.summary.image_signatures = len(image_sigs)
    policy.summary.group_traits = [
        f"{g.trait}:{''.join(sorted(g.charset))[:16]}" for g in groups
    ]
    return policy


def learn_watermark_text_groups(
    char_pages: list[tuple[int, list[dict]]],
    *,
    min_repeat_pages: int = 3,
    repeat_ratio: float = 0.6,
) -> list[WatermarkTextGroup]:
    """从各页字符字典学习水印字符属性组(纯函数, 供单测直接驱动)。

    判定条件(全部 AND):
    1. 跨页复现页数 >= max(min_repeat_pages, ceil(总页数*repeat_ratio));
    2. 去重字符并集 <= 短语上限, 且每页集合平均覆盖并集 >= 覆盖率下限(信息恒定);
    3. 弱显示属性任一命中: 非黑色 / 组内多数字符旋转 / 字号 >= 主字体*倍数。
    """
    total_pages = len(char_pages)
    if total_pages == 0:
        return []
    threshold = max(min_repeat_pages, math.ceil(total_pages * repeat_ratio))

    per_page_charsets: dict[_GroupKey, dict[int, set[str]]] = defaultdict(dict)
    char_totals: Counter = Counter()
    rotated_counts: Counter = Counter()
    for page_num, chars in char_pages:
        for ch in chars:
            text = str(ch.get("text") or "")
            if not text.strip():
                continue
            key = _char_group_key(ch)
            per_page_charsets[key].setdefault(page_num, set()).add(text)
            char_totals[key] += 1
            if _is_rotated_char(ch):
                rotated_counts[key] += 1

    dominant_size = _dominant_font_size(char_totals)

    groups: list[WatermarkTextGroup] = []
    for key, by_page in per_page_charsets.items():
        pages_seen = len(by_page)
        if pages_seen < threshold:
            continue
        union: set[str] = set()
        for charset in by_page.values():
            union |= charset
        if not union or len(union) > _MAX_CHARSET_SIZE:
            continue
        coverage = sum(len(cs) for cs in by_page.values()) / (len(union) * pages_seen)
        if coverage < _MIN_CHARSET_COVERAGE:
            continue
        trait = _weak_display_trait(
            key,
            dominant_size=dominant_size,
            rotated_ratio=rotated_counts[key] / max(char_totals[key], 1),
        )
        if trait is None:
            continue
        fontname, size, color_key = key
        groups.append(WatermarkTextGroup(
            fontname=fontname,
            size=size,
            color_key=color_key,
            charset=frozenset(union),
            trait=trait,
            pages_seen=pages_seen,
        ))
    return groups


def _learn_watermark_image_signatures(
    page_images: dict[int, list[Any]],
    page_dims: dict[int, tuple[float, float]],
    *,
    min_repeat_pages: int,
    repeat_ratio: float,
) -> set[str]:
    """跨页重复且大面积的图片签名集合(小图/公章不入选)。"""
    if not page_images:
        return set()
    from app.core.parser.page_chrome_policy import image_signature

    total_pages = max(len(page_dims), len(page_images))
    threshold = max(min_repeat_pages, math.ceil(total_pages * repeat_ratio))
    pages_by_sig: dict[str, set[int]] = defaultdict(set)
    for page_num, images in page_images.items():
        width, height = page_dims.get(page_num, (0.0, 0.0))
        if width <= 0 or height <= 0:
            continue
        for image in images:
            if _image_area_ratio(image, width, height) < _IMAGE_MIN_AREA_RATIO:
                continue
            sig = image_signature(image, width, height)
            if sig:
                pages_by_sig[sig].add(page_num)
    return {sig for sig, nums in pages_by_sig.items() if len(nums) >= threshold}


# ---------------------------------------------------------------------------
# 字符/图片特征 helper
# ---------------------------------------------------------------------------


def _char_group_key(ch: dict) -> _GroupKey:
    fontname = str(ch.get("fontname") or "")
    try:
        size = round(float(ch.get("size", 0.0) or 0.0), 1)
    except (TypeError, ValueError):
        size = 0.0
    return fontname, size, _color_key(ch.get("non_stroking_color"))


def _color_key(color: Any) -> str:
    values = _color_values(color)
    if values is None:
        return str(color)
    return ",".join(f"{v:.3f}" for v in values)


def _color_values(color: Any) -> list[float] | None:
    if color is None:
        return None
    if isinstance(color, (int, float)):
        return [float(color)]
    if isinstance(color, (tuple, list)):
        out: list[float] = []
        for item in color:
            try:
                out.append(float(item))
            except (TypeError, ValueError):
                return None
        return out
    return None


def _is_low_ink_color(color_key: str) -> bool:
    """非纯黑深色(灰/浅色/彩色)即弱显示色; 黑色正文各分量接近 0。"""
    try:
        values = [float(v) for v in color_key.split(",") if v]
    except ValueError:
        return False
    return bool(values) and max(values) >= _LOW_INK_COMPONENT


def _is_rotated_char(ch: dict) -> bool:
    matrix = ch.get("matrix")
    if not matrix or len(matrix) < 2:
        return False
    try:
        return abs(float(matrix[1])) > _ROTATION_EPS
    except (TypeError, ValueError):
        return False


def _dominant_font_size(char_totals: Counter) -> float:
    if not char_totals:
        return 0.0
    (fontname, size, color_key), _count = char_totals.most_common(1)[0]
    del fontname, color_key
    return float(size)


def _weak_display_trait(
    key: _GroupKey,
    *,
    dominant_size: float,
    rotated_ratio: float,
) -> str | None:
    """弱显示属性判定: 返回命中特征名, 全不命中返回 None(黑色常规字号水平)。"""
    _fontname, size, color_key = key
    if _is_low_ink_color(color_key):
        return "low_ink"
    if rotated_ratio >= 0.5:
        return "rotated"
    if dominant_size > 0 and size >= dominant_size * _OVERSIZE_RATIO:
        return "oversize"
    return None


def _image_area_ratio(image: Any, page_width: float, page_height: float) -> float:
    if page_width <= 0 or page_height <= 0:
        return 0.0
    bbox = getattr(image, "bbox", None)
    if bbox and len(bbox) == 4:
        try:
            x0, y0, x1, y1 = (float(v) for v in bbox)
            return max(0.0, (x1 - x0) * (y1 - y0)) / (page_width * page_height)
        except (TypeError, ValueError):
            pass
    try:
        width = float(getattr(image, "width", 0.0) or 0.0)
        height = float(getattr(image, "height", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, width * height) / (page_width * page_height)


def _has_evidence_context(image: Any) -> bool:
    from app.core.parser.page_chrome_policy import _has_evidence_context as chrome_evidence

    return chrome_evidence(image)


__all__ = [
    "WatermarkPolicy",
    "WatermarkSummary",
    "WatermarkTextGroup",
    "learn_watermark_policy",
    "learn_watermark_text_groups",
]
