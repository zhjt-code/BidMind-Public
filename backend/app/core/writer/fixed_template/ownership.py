"""固定模板唯一归属仲裁。

职责: 在同卷 fixed_template 节点间仲裁一条模板约束的唯一归属节点。
不做: 不访问数据库、不修改绑定、不生成章节内容、不调用 LLM。

背景: 复合别名/子串匹配可能让同一模板约束被多个章节同时命中
(如"投标人基本情况表"与其父章节"投标人基本情况及人员配置表及公司简介"),
导致同一张模板表被多处复读。仲裁规则: 标题亲和度最高者独占消费,
其余节点输出交叉引用提示并打人工复核 gate。
"""

from __future__ import annotations

from typing import Any, Optional

from app.core.writer.fixed_template.titles import (
    custom_template_key,
    infer_fixed_template_key,
    match_template_title,
    normalize_template_title,
)
from app.core.writer.numbering import strip_existing_number


_SCORE_SNAPSHOT_TITLE_EXACT = 4
_SCORE_ALIAS_EXACT = 3
_SCORE_KEY_MATCH = 2
_SCORE_TITLE_MATCH = 1


def select_owner_node(
    candidate_nodes: list[Any],
    snapshot: Optional[dict[str, Any]],
) -> Optional[Any]:
    """在候选节点中选出模板快照的唯一归属节点。

    Args:
        candidate_nodes: 同卷 fixed_template 节点(需具备 id/title/depth/sort_order)。
        snapshot: fixed_template 快照 dict。

    Returns:
        归属节点; 快照缺失或无任何候选命中时返回 None(调用方按现状放行)。
    """
    if not snapshot or not candidate_nodes:
        return None
    scored = [
        (score, node)
        for node in candidate_nodes
        if (score := template_affinity_score(getattr(node, "title", ""), snapshot)) > 0
    ]
    if not scored:
        return None
    best_score = max(score for score, _node in scored)
    finalists = [node for score, node in scored if score == best_score]
    # 同分优先取更深层节点(叶子优先于聚合父节点), 再按阅读顺序与 id 稳定收敛
    finalists.sort(
        key=lambda node: (
            -int(getattr(node, "depth", 0) or 0),
            int(getattr(node, "sort_order", 0) or 0),
            int(getattr(node, "id", 0) or 0),
        )
    )
    return finalists[0]


def template_affinity_score(node_title: str, snapshot: dict[str, Any]) -> int:
    """节点标题对模板快照的亲和度打分(越高越优)。"""
    clean_title = strip_existing_number(node_title or "")
    norm_node = normalize_template_title(clean_title)
    if not norm_node:
        return 0
    snap_title = str(snapshot.get("title") or "")
    template_key = snapshot.get("template_key")

    if norm_node == normalize_template_title(snap_title):
        return _SCORE_SNAPSHOT_TITLE_EXACT
    for alias in snapshot.get("aliases") or ():
        if norm_node == normalize_template_title(str(alias)):
            return _SCORE_ALIAS_EXACT
    node_key = infer_fixed_template_key(clean_title) or custom_template_key(clean_title)
    if template_key and node_key == template_key:
        return _SCORE_KEY_MATCH
    if match_template_title(clean_title, snap_title, template_key).matched:
        return _SCORE_TITLE_MATCH
    return 0


__all__ = ["select_owner_node", "template_affinity_score"]
