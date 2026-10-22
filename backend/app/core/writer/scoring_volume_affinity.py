"""写标评分项分卷亲和性。

职责: 从权威评分项 metadata 中推断目录生成应归属的分卷类别。
不做: 不做目录生成、不做关键词兜底、不改写约束或目录节点。
"""

from __future__ import annotations

from typing import Any

_VOLUME_TO_CATEGORY = {
    "technical": "technical",
    "commercial": "commercial",
    "price": "commercial",
    # 服务卷评分项(服务方案/培训方案等)在标书目录中归技术标章节
    "service": "technical",
    "qualification": "qualification",
}


def volume_category_from_metadata(clause: Any) -> str | None:
    """从 WritingClause-like 对象的 metadata_json.volume 推断目录分卷类别。"""
    metadata = getattr(clause, "metadata_json", None)
    if not isinstance(metadata, dict):
        return None
    volume = str(metadata.get("volume") or "").strip().lower()
    return _VOLUME_TO_CATEGORY.get(volume)


__all__ = ["volume_category_from_metadata"]
