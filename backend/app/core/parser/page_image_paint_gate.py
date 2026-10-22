"""
职责: 从页级资源列表中选出本页实绘图片 xref
不做: 解码落盘、尺寸过滤、页眉页脚策略、VL 识别
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

# ponytail: 绘制信息缺失时，小列表回退到资源枚举（天花板=8）。
# 超过此值按资源泄漏处理（提取空集）。若后续能从内容流恢复实绘 xref，
# 应先走绘制集，再收紧或删除此回退。
PAINTED_EMPTY_FALLBACK_MAX = 8

BBox = tuple[float, float, float, float]


def _listed_size(item: Any) -> tuple[int, int] | None:
    try:
        return int(item[2]), int(item[3])
    except (IndexError, TypeError, ValueError):
        return None


def resolve_painted_identities(
    listed_images: Iterable[Any],
    cheap_blocks: Iterable[Mapping[str, Any]],
) -> dict[int, BBox]:
    """用廉价绘制块的唯一宽高对回资源 xref，不解码资源字典。

    同一宽高对应多个 xref 时放弃该绘制实例，避免绑错图。
    """
    by_size: dict[tuple[int, int], list[int]] = {}
    for item in listed_images:
        size = _listed_size(item)
        if size is None:
            continue
        xref = int(item[0])
        bucket = by_size.setdefault(size, [])
        if xref not in bucket:
            bucket.append(xref)

    resolved: dict[int, BBox] = {}
    for block in cheap_blocks:
        bbox = block.get("bbox")
        if not bbox:
            continue
        try:
            key = (int(block["width"]), int(block["height"]))
        except (KeyError, TypeError, ValueError):
            continue
        candidates = by_size.get(key, [])
        if len(candidates) != 1:
            continue
        xref = candidates[0]
        if xref not in resolved:
            resolved[xref] = tuple(bbox)  # type: ignore[assignment]
    return resolved


def count_painted_instances(cheap_blocks: Iterable[Mapping[str, Any]]) -> int:
    """统计带 bbox 的廉价绘制实例数（身份绑定前）。"""
    return sum(1 for block in cheap_blocks if block.get("bbox"))


def select_page_image_xrefs(
    listed_xrefs: Iterable[int],
    painted_xrefs: Iterable[int],
    painted_instance_count: int = 0,
) -> set[int]:
    """选出本页应解码的图片 xref。

    - 有已绑定绘制集：只取 listed ∩ painted
    - 看见绘制实例但全部无法绑定：空集（禁止退回整份资源列表）
    - 无绘制实例且 unique listed 不超过回退上限：沿用 listed
    - 无绘制实例且 unique listed 超过上限：空集
    """
    unique_listed = {int(xref) for xref in listed_xrefs}
    painted = {int(xref) for xref in painted_xrefs}
    if painted:
        return unique_listed & painted
    if painted_instance_count > 0:
        return set()
    if len(unique_listed) > PAINTED_EMPTY_FALLBACK_MAX:
        return set()
    return unique_listed
