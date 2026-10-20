"""页级图片视觉识别文本的绑定与去重纯函数

职责: 页级视觉识别文本的单份保存与去重拼装
不做: VL调用、chunk切分、DB写入

背景: 整页 OCR/VL 把同一段整页识别文本复制给同页每张图片后,
逐图拼接会让图片证据 chunk 确定性膨胀(3张图重复3次), 且会覆盖
逐图识别结果。本模块提供纯函数, 把整页文本单份落在
ParsedPage.full_page_visual_content(瞬态), 拼装侧按保序去重消费。
"""

from __future__ import annotations

import copy
from typing import Any, Iterable


def dedupe_visual_texts(
    images: Iterable[Any],
    exclude: Iterable[str] = (),
) -> list[str]:
    """保序去重逐图 visual_content, exclude 中的文本视为已出现。

    Args:
        images: 含 visual_content 属性的图片对象序列
        exclude: 视为已出现的文本(如页级整页文本), 空串忽略

    Returns:
        list[str]: 按首次出现顺序排列的去重文本列表
    """
    seen = {text for text in exclude if text}
    result: list[str] = []
    for img in images:
        text = getattr(img, "visual_content", "") or ""
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def page_visual_texts(full_page_text: str, images: Iterable[Any]) -> list[str]:
    """页级整页文本一份 + 逐图去重文本(与页级文本重复的不再出现)。"""
    texts: list[str] = [full_page_text] if full_page_text else []
    texts.extend(dedupe_visual_texts(images, exclude=(full_page_text,)))
    return texts


def dedupe_images_for_display(images: Iterable[Any]) -> list[Any]:
    """重复 visual_content 的图片以浅拷贝清空展示文本, 首次出现的保留。

    用于标注拼装: 同页多图共享同一段整页识别文本时, 只有首张图
    展示该 VL 内容, 其余图片降级走 spatial_context/context 路径。
    不修改入参对象本身。
    """
    seen: set[str] = set()
    result: list[Any] = []
    for img in images:
        text = getattr(img, "visual_content", "") or ""
        if text and text in seen:
            shadow = copy.copy(img)
            shadow.visual_content = ""
            result.append(shadow)
            continue
        if text:
            seen.add(text)
        result.append(img)
    return result


def bind_full_page_visual_content(page: Any, content: str) -> None:
    """把整页识别文本绑定到页: 页级单份保存, 不覆盖逐图识别结果。

    - page.full_page_visual_content 更新为整页文本(瞬态, 不落库)
    - 已有逐图 visual_content 的图片一律不覆盖(修复整页结果覆盖逐图结果)
    - visual_content 为空的图片回填整页文本, 维持下游重试判定/计数/标注兼容
    """
    if not content:
        return
    page.full_page_visual_content = content
    for img in getattr(page, "images", None) or []:
        if not getattr(img, "visual_content", ""):
            img.visual_content = content
