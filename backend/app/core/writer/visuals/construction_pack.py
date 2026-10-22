"""建筑领域图示选图包。

职责: 章节-图种允许/禁止、施工向模板标识、真图优先判定、空间工程章禁算法图。
不做: 不编译 IR、不渲染 PNG、不访问数据库、不调用 LLM。
"""

from __future__ import annotations

import re

ZONE_LAYOUT_CAPTION_SUFFIX = "（示意图，非施工平面图）"

ZONE_VOCAB: frozenset[str] = frozenset({
    "办公区",
    "生活区",
    "施工区",
    "材料堆场",
    "加工区",
    "大门",
    "临时道路",
    "围挡",
    "消防通道",
    "钢筋加工棚",
    "木工棚",
    "搅拌站",
    "仓库",
    "停车场",
    "洗车池",
})

# 算法侧禁止出图的空间工程图章节。真图走素材亲和。
_SPATIAL_DRAWING_TERMS = (
    "剖面",
    "支护",
    "脚手架",
    "BIM",
    "bim",
    "效果图",
    "三维",
)

_NETWORK_TERMS = ("网络图", "关键线路", "PERT", "pert", "CPM", "cpm", "紧前")
_WBS_TERMS = ("WBS", "wbs", "工作分解", "任务分解")
_ZONE_TERMS = ("平面布置", "总平面", "临设", "场地布置", "功能分区")
# 场地/临设章节禁止出流程图，避免「施工平面布置」被当成工艺流程。
_LAYOUT_CHAPTER_TERMS = ("平面布置", "总平面", "临设", "场地布置")

MATERIAL_SUPERSEDES_DIAGRAM: dict[str, str] = {
    "zone_layout": "site_layout",
    "network": "network_drawing",
}

_COMPACT_RE = re.compile(r"\s+")


def _compact(text: str) -> str:
    return _COMPACT_RE.sub("", text or "")


def is_spatial_drawing_chapter(*texts: str) -> bool:
    """剖面/支护/脚手架/BIM/效果图等章节禁止任何算法生成图。"""

    blob = _compact("".join(texts))
    return any(term in blob for term in _SPATIAL_DRAWING_TERMS)


def forbids_flowchart_on_layout_chapter(*texts: str) -> bool:
    """平面/临设章节即使图题带「流程」也不出 flowchart。"""

    blob = _compact("".join(texts))
    return any(term in blob for term in _LAYOUT_CHAPTER_TERMS)


def extra_type_terms(diagram_type: str) -> tuple[str, ...]:
    """新图种的资格关键词；未知类型返回空。"""

    if diagram_type == "network":
        return _NETWORK_TERMS
    if diagram_type == "wbs":
        return _WBS_TERMS
    if diagram_type == "zone_layout":
        return _ZONE_TERMS
    return ()


def superseded_by_material(
    diagram_type: str,
    material_subtypes: set[str] | frozenset[str] | None,
) -> bool:
    """用户已有对应真图时，跳过同主题算法示意图。"""

    if not material_subtypes:
        return False
    required = MATERIAL_SUPERSEDES_DIAGRAM.get(diagram_type)
    return required is not None and required in material_subtypes


def ensure_zone_layout_caption(title: str) -> str:
    """编译期强制标明分区示意不是施工平面图。"""

    cleaned = (title or "").strip()
    if "非施工平面图" in cleaned:
        return cleaned
    combined = f"{cleaned}{ZONE_LAYOUT_CAPTION_SUFFIX}" if cleaned else (
        f"场地分区{ZONE_LAYOUT_CAPTION_SUFFIX}"
    )
    return combined[:200]


def normalize_zone_names(raw_zones: list[str] | None) -> list[str]:
    """只保留封闭词表中的分区名，去重并保序。"""

    seen: set[str] = set()
    kept: list[str] = []
    for item in raw_zones or []:
        name = str(item or "").strip()
        if name not in ZONE_VOCAB or name in seen:
            continue
        seen.add(name)
        kept.append(name)
    return kept


__all__ = [
    "MATERIAL_SUPERSEDES_DIAGRAM",
    "ZONE_LAYOUT_CAPTION_SUFFIX",
    "ZONE_VOCAB",
    "ensure_zone_layout_caption",
    "extra_type_terms",
    "is_spatial_drawing_chapter",
    "forbids_flowchart_on_layout_chapter",
    "normalize_zone_names",
    "superseded_by_material",
]
