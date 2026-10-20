"""标书预览图片内联

职责: 将章节预览 HTML 中的"[图片占位]"段落替换为 base64 内联 <img>,
      判定逻辑(章节亲和 / 文件存在 / 独占去重)与 Word 渲染
      (DocRenderer._insert_figure_images)对齐, 保证"预览所见 == 导出所得"。
不做: 不解析 HTML 块结构、不生成 Word、不查询数据库
      (图片记录与消费状态由调用方传入)。
"""

from __future__ import annotations

import base64
import re
from html import escape
from pathlib import Path
from typing import Optional

from loguru import logger

from app.core.writer.identity_image_layout import (
    IdentityImageLayout,
    IdentityImageSlot,
    build_identity_image_layout,
)
from app.core.writer.image_node_affinity import (
    filter_images_for_node,
    is_exclusive_image,
)
from app.models.writing_material_image import ImageType, WritingMaterialImage

# 预览占位段落: HtmlPreviewBuilder._decorate_text_block 输出 <p>[图片占位]</p>
_PLACEHOLDER_P_RE = re.compile(r"<p[^>]*>\[图片占位\]</p>")

# 与 DocRenderer._insert_figure_images 同款 figure 提取规则
_FIGURE_RE = re.compile(
    r'data-image-id="(\d+)"[^>]*>.*?<figcaption>(.*?)</figcaption>',
    re.DOTALL,
)

_MIME_BY_EXT: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "webp": "image/webp",
}

# 图片宽度策略 (mm), 与 DocRenderer._calc_image_width 数值对齐
_MAX_WIDTH_MM = 160
_MIN_WIDTH_MM = 60
_CERT_MIN_WIDTH_MM = 120
_DEFAULT_CERT_WIDTH_MM = 140
_DEFAULT_WIDTH_MM = 120
_CERT_IMAGE_TYPES = (
    ImageType.license,
    ImageType.personnel_cert,
    ImageType.qualification,
)


def extract_image_map(raw_html: str) -> list[tuple[str, str]]:
    """从原始章节 HTML 提取 (image_id, caption) 顺序列表。

    采用 dict 去重 + 保序, 与 Word 路径 image_map 语义一致。
    """
    ordered: dict[str, str] = {}
    for match in _FIGURE_RE.finditer(raw_html or ""):
        ordered[match.group(1)] = match.group(2).strip()
    return list(ordered.items())


def inline_section_images(
    rendered_html: str,
    raw_html: str,
    section_images: dict[int, WritingMaterialImage],
    section_title: str,
    consumed_image_keys: Optional[dict[str, str]] = None,
) -> str:
    """将渲染后 HTML 中的图片占位按顺序替换为内联图片或人工确认标记。

    Args:
        rendered_html: ``_render_section_blocks`` 输出的章节 HTML
        raw_html: 预处理前的 draft.content(用于提取 figure 与 image_id)
        section_images: 已按章节加载的候选图片 {image_id: record}
        section_title: 章节标题(用于亲和性最终闸门)
        consumed_image_keys: 跨章节独占图片消费登记(同 Word 去重),可为 None

    Returns:
        替换后的章节 HTML。无图片时原样返回。
    """
    image_map = extract_image_map(raw_html)
    if not image_map:
        return rendered_html

    identity_candidates: list[WritingMaterialImage] = []
    for img_id_str, _caption in image_map:
        try:
            rec = section_images.get(int(img_id_str))
        except (TypeError, ValueError):
            rec = None
        if rec is None:
            continue
        if section_title and not filter_images_for_node([rec], section_title):
            continue
        identity_candidates.append(rec)
    identity_layout = build_identity_image_layout(identity_candidates)
    identity_ids = {str(image_id) for image_id in identity_layout.ordered_image_ids}
    identity_rendered = {"done": False}
    captions_by_id = {int(img_id): caption for img_id, caption in image_map}
    cursor = {"index": 0}

    def _replace(_match: "re.Match[str]") -> str:
        index = cursor["index"]
        cursor["index"] = index + 1
        if index >= len(image_map):
            # 占位符多于 figure 标记: 残留标记为待人工确认
            return _pending_html("")
        img_id_str, caption = image_map[index]
        if img_id_str in identity_ids and identity_rendered["done"]:
            return ""
        if img_id_str in identity_ids and not identity_rendered["done"]:
            identity_rendered["done"] = True
            return _identity_layout_html(
                identity_layout,
                captions_by_id,
                section_title,
                consumed_image_keys,
            )
        return _render_single_placeholder(
            img_id_str, caption, section_images, section_title, consumed_image_keys,
        )

    return _PLACEHOLDER_P_RE.sub(_replace, rendered_html)


def _render_single_placeholder(
    img_id_str: str,
    caption: str,
    section_images: dict[int, WritingMaterialImage],
    section_title: str,
    consumed_image_keys: Optional[dict[str, str]],
) -> str:
    """对单个占位符执行与 Word 一致的判定并返回对应 HTML 片段。"""
    try:
        rec = section_images.get(int(img_id_str))
    except (TypeError, ValueError):
        rec = None

    if rec is None:
        return _pending_html(caption)

    if section_title and not filter_images_for_node([rec], section_title):
        logger.warning(
            "预览图片亲和拦截: image_id={}, section={!r}, caption={!r}",
            img_id_str, section_title, caption,
        )
        return _pending_html(caption)

    data_uri = _encode_data_uri(rec.image_path)
    if data_uri is None:
        return _missing_html(caption)

    if consumed_image_keys is not None and is_exclusive_image(rec):
        consume_key = rec.file_hash or rec.image_path
        prev_section = consumed_image_keys.get(consume_key)
        if prev_section and prev_section != section_title:
            logger.warning(
                "预览图片重复复用拦截: image_id={}, prev_section={!r}, current={!r}",
                img_id_str, prev_section, section_title,
            )
            return _pending_html(caption)
        consumed_image_keys[consume_key] = section_title

    return _figure_html(data_uri, caption, rec)


def _encode_data_uri(image_path: str) -> Optional[str]:
    """读取图片文件并编码为 data URI。文件缺失或读取失败返回 None。"""
    if not image_path:
        return None
    path = Path(image_path)
    if not path.exists():
        return None
    ext = path.suffix.lower().lstrip(".")
    mime = _MIME_BY_EXT.get(ext, "image/png")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        logger.warning("预览图片读取失败: path={}, err={}", image_path, exc)
        return None
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _calc_width_mm(rec: WritingMaterialImage) -> int:
    """计算预览图片宽度(mm), 与 DocRenderer._calc_image_width 数值对齐。"""
    image_type = getattr(rec, "image_type", None)
    is_cert = image_type in _CERT_IMAGE_TYPES if image_type else False
    width_px = getattr(rec, "width", 0) or 0
    if width_px > 0:
        physical_mm = width_px * 25.4 / 150
        if is_cert:
            physical_mm = max(physical_mm, _CERT_MIN_WIDTH_MM)
        return int(max(min(physical_mm, _MAX_WIDTH_MM), _MIN_WIDTH_MM))
    return _DEFAULT_CERT_WIDTH_MM if is_cert else _DEFAULT_WIDTH_MM


def _figure_html(data_uri: str, caption: str, rec: WritingMaterialImage) -> str:
    """构造内联图片 figure 片段(图片 + 居中题注)。"""
    width_mm = _calc_width_mm(rec)
    safe_caption = escape(caption or "")
    return (
        '<figure class="bid-figure" style="text-align:center;margin:12pt 0;">'
        f'<img src="{data_uri}" alt="{safe_caption}" '
        f'style="width:{width_mm}mm;max-width:100%;height:auto;" />'
        f'<figcaption style="font-size:9pt;color:#333;margin-top:4pt;">'
        f'{safe_caption}</figcaption>'
        '</figure>'
    )


def _identity_layout_html(
    layout: IdentityImageLayout,
    captions_by_id: dict[int, str],
    section_title: str,
    consumed_image_keys: Optional[dict[str, str]],
) -> str:
    """构造身份证双列预览片段。"""
    if not layout.rows:
        return _pending_html("")
    rows: list[str] = []
    for row in layout.rows:
        cells = [
            _identity_cell_html(
                row.front, captions_by_id, section_title, consumed_image_keys,
            ),
            _identity_cell_html(
                row.back, captions_by_id, section_title, consumed_image_keys,
            ),
        ]
        rows.append(
            f'<div class="identity-card-row" data-role="{escape(row.role)}" '
            'style="display:flex;justify-content:center;gap:8mm;margin:6pt 0;">'
            f'{"".join(cells)}</div>'
        )
        for slot in row.unknown_slots:
            rows.append(
                '<div class="identity-card-row" data-role="unknown" '
                'style="display:flex;justify-content:center;gap:8mm;margin:6pt 0;">'
                f'{_identity_cell_html(slot, captions_by_id, section_title, consumed_image_keys)}'
                '<div class="identity-card-cell identity-card-empty" '
                'style="width:80mm;"></div></div>'
            )
    return (
        '<div class="identity-card-layout" '
        'style="text-align:center;margin:12pt 0;">'
        f'{"".join(rows)}</div>'
    )


def _identity_cell_html(
    slot: IdentityImageSlot | None,
    captions_by_id: dict[int, str],
    section_title: str,
    consumed_image_keys: Optional[dict[str, str]],
) -> str:
    if slot is None:
        return (
            '<div class="identity-card-cell identity-card-empty" '
            'style="width:80mm;"></div>'
        )

    rec = slot.image
    caption = captions_by_id.get(slot.image_id) or slot.caption
    if section_title and not filter_images_for_node([rec], section_title):
        return _identity_placeholder_cell(_pending_html(caption))

    data_uri = _encode_data_uri(rec.image_path)
    if data_uri is None:
        return _identity_placeholder_cell(_missing_html(caption))

    if consumed_image_keys is not None and is_exclusive_image(rec):
        consume_key = rec.file_hash or rec.image_path
        prev_section = consumed_image_keys.get(consume_key)
        if prev_section and prev_section != section_title:
            return _identity_placeholder_cell(_pending_html(caption))
        consumed_image_keys[consume_key] = section_title

    safe_caption = escape(caption or "")
    return (
        '<figure class="identity-card-cell" style="width:80mm;margin:0;">'
        f'<img src="{data_uri}" alt="{safe_caption}" '
        'style="width:78mm;max-width:100%;height:auto;" />'
        '<figcaption style="font-size:9pt;color:#333;margin-top:4pt;">'
        f'{safe_caption}</figcaption></figure>'
    )


def _identity_placeholder_cell(inner_html: str) -> str:
    return (
        '<div class="identity-card-cell" style="width:80mm;margin:0;">'
        f'{inner_html}</div>'
    )


def _pending_html(caption: str) -> str:
    """图片待人工确认标记(章节不匹配 / 未绑定 / 重复)。"""
    safe_caption = escape(caption or "")
    label = f"[图片待人工确认: {safe_caption}]" if safe_caption else "[图片待人工确认]"
    return f'<p class="placeholder">{label}</p>'


def _missing_html(caption: str) -> str:
    """图片文件缺失标记。"""
    safe_caption = escape(caption or "")
    return f'<p class="placeholder">[图片缺失: {safe_caption}]</p>'
