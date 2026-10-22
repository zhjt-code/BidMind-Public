"""图聚类模块

基于 Union-Find 实现连通分量识别，不引入 networkx 依赖。
将 pair 级对比结果 + 全局特征发现融合为关系图，
提取连通分量作为嫌疑群组。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from app.schemas.collusion import build_pair_id


@dataclass
class CollusionCluster:
    """单个嫌疑群组"""
    member_doc_ids: list[int] = field(default_factory=list)
    member_names: list[str] = field(default_factory=list)
    risk_level: str = "medium"
    aggregated_evidence: list[dict] = field(default_factory=list)
    related_pair_ids: list[str] = field(default_factory=list)
    group_confidence: float = 0.0
    uncertain_edge_count: int = 0
    # P0-1: S3.6 EvidenceFusionEngine 融合产出（按人名/企业关系桶去重合并），
    # 供 S4 群组 LLM 研判优先消费；未启用灰度时仍回退 aggregated_evidence
    fused_evidence: list[dict] = field(default_factory=list)


class _UnionFind:
    """Union-Find (Disjoint Set Union) with path compression and union by rank."""

    def __init__(self, elements: list[int]) -> None:
        self._parent: dict[int, int] = {e: e for e in elements}
        self._rank: dict[int, int] = {e: 0 for e in elements}

    def find(self, x: int) -> int:
        if self._parent[x] != x:
            self._parent[x] = self.find(self._parent[x])
        return self._parent[x]

    def union(self, x: int, y: int) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self._rank[rx] < self._rank[ry]:
            rx, ry = ry, rx
        self._parent[ry] = rx
        if self._rank[rx] == self._rank[ry]:
            self._rank[rx] += 1

    def components(self) -> dict[int, set[int]]:
        groups: dict[int, set[int]] = {}
        for elem in self._parent:
            root = self.find(elem)
            groups.setdefault(root, set()).add(elem)
        return groups


_RISK_PRIORITY = {"high": 3, "medium": 2, "low": 1, "none": 0, "inconclusive": 0}


def build_collusion_graph(
    pair_results: list[dict],
    global_features,
    doc_names: Optional[dict[int, str]] = None,
    risk_threshold: str = "medium",
) -> list[CollusionCluster]:
    """融合 pair 结果 + 全局特征，构建关系图并提取嫌疑群组

    Args:
        pair_results: analyzer 输出的 pair 对比结果列表
        global_features: GlobalFeatureResult 实例
        doc_names: {doc_id: filename} 映射
        risk_threshold: 构成边的最低风险等级（含）

    Returns:
        嫌疑群组列表，按风险等级降序排列
    """
    doc_names = doc_names or {}
    threshold_val = _RISK_PRIORITY.get(risk_threshold, 2)

    all_doc_ids: set[int] = set()
    edges: list[tuple[int, int, str, list[dict], str]] = []

    for res in pair_results:
        if not isinstance(res, dict):
            continue
        pair_ref = res.get("pair_ref") or {}
        doc_a = pair_ref.get("doc_a_id")
        doc_b = pair_ref.get("doc_b_id")
        if not doc_a or not doc_b:
            continue

        all_doc_ids.add(doc_a)
        all_doc_ids.add(doc_b)

        risk = res.get("overall_risk", "none")
        risk_val = _RISK_PRIORITY.get(risk, 0)
        review_state = str(res.get("review_state", "completed") or "completed")
        similarity_score = float(res.get("similarity_score", 0.0) or 0.0)
        include_uncertain_edge = (
            review_state in {"failed", "unreviewed", "degraded"}
            and similarity_score >= 0.7
        )
        if risk_val >= threshold_val or include_uncertain_edge:
            top_findings = _extract_top_findings(res, max_count=3)
            edge_risk = risk if risk_val >= threshold_val else "medium"
            if include_uncertain_edge and not top_findings:
                top_findings = [{
                    "dimension": "uncertain",
                    "description": f"该文件对相似度 {similarity_score:.0%}，但当前仅有待复核结果，需人工确认。",
                    "source": "pair_uncertain",
                }]
            edges.append((doc_a, doc_b, edge_risk, top_findings, review_state))

    if global_features:
        for person_key, doc_ids in (global_features.personnel_overlaps or {}).items():
            if len(doc_ids) >= 2:
                from itertools import combinations
                for a, b in combinations(doc_ids, 2):
                    all_doc_ids.add(a)
                    all_doc_ids.add(b)
                    parts = person_key.split("|", 1)
                    name = parts[0]
                    edges.append((a, b, "high", [{
                        "dimension": "D1",
                        "description": f"全局人员重叠: {name} 跨标出现",
                        "source": "global_features",
                    }], "completed"))

        for id_a, id_b, relations in (global_features.company_edges or []):
            all_doc_ids.add(id_a)
            all_doc_ids.add(id_b)
            edges.append((id_a, id_b, "high", [{
                "dimension": "D2",
                "description": f"全局企业关联: {'; '.join(relations)}",
                "source": "global_features",
            }], "completed"))

        # 跨字段串联引擎命中：first/second 级别贡献图聚类边
        cross_hits = getattr(global_features, "cross_field_hits", None) or []
        for h in cross_hits:
            level = getattr(h, "level", "third")
            if level not in ("first", "second"):
                continue
            id_a = getattr(h, "left_doc_id", None)
            id_b = getattr(h, "right_doc_id", None)
            if not id_a or not id_b:
                continue
            all_doc_ids.add(id_a)
            all_doc_ids.add(id_b)
            edge_risk = "high" if level == "first" else "medium"
            edges.append((id_a, id_b, edge_risk, [{
                "dimension": getattr(h, "target_dim", "D8"),
                "description": getattr(h, "description", "跨字段串联命中"),
                "source": "cross_field",
                "risk_pattern": getattr(h, "risk_pattern", None),
                "legal_reference": getattr(h, "legal_reference", None),
            }], "completed"))

    if not edges:
        logger.info("[GraphCluster] 无有效边，未识别到嫌疑群组")
        return []

    uf = _UnionFind(list(all_doc_ids))
    for a, b, _, _, _ in edges:
        uf.union(a, b)

    components = uf.components()

    edge_index: dict[tuple[int, int], tuple[str, list[dict], str]] = {}
    for a, b, risk, findings, review_state in edges:
        key = (min(a, b), max(a, b))
        if key not in edge_index:
            edge_index[key] = (risk, findings, review_state)
        else:
            old_risk, old_findings, old_review_state = edge_index[key]
            merged_risk = risk if _RISK_PRIORITY.get(risk, 0) > _RISK_PRIORITY.get(old_risk, 0) else old_risk
            merged_findings = old_findings + findings
            merged_state = (
                old_review_state
                if old_review_state in {"failed", "unreviewed"}
                else review_state
            )
            edge_index[key] = (merged_risk, merged_findings[:6], merged_state)

    clusters: list[CollusionCluster] = []
    for _, members in sorted(components.items()):
        if len(members) < 2:
            continue

        member_ids = sorted(members)
        member_ns = [doc_names.get(d, f"文件{d}") for d in member_ids]

        max_risk = "low"
        agg_evidence: list[dict] = []
        related_pairs: list[str] = []
        uncertain_edge_count = 0
        confirmed_edge_count = 0
        total_risk_weight = 0.0

        from itertools import combinations
        for a, b in combinations(member_ids, 2):
            key = (min(a, b), max(a, b))
            if key in edge_index:
                risk, findings, review_state = edge_index[key]
                if _RISK_PRIORITY.get(risk, 0) > _RISK_PRIORITY.get(max_risk, 0):
                    max_risk = risk
                agg_evidence.extend(findings)
                related_pairs.append(build_pair_id(a, b))
                total_risk_weight += _RISK_PRIORITY.get(risk, 0)
                if review_state in {"failed", "unreviewed"}:
                    uncertain_edge_count += 1
                else:
                    confirmed_edge_count += 1

        total_edges = confirmed_edge_count + uncertain_edge_count
        avg_risk = total_risk_weight / max(total_edges, 1)
        certainty_ratio = confirmed_edge_count / max(total_edges, 1)
        group_confidence = round(
            max(0.0, min(1.0, (avg_risk / 3.0) * 0.55 + certainty_ratio * 0.45)),
            3,
        )

        clusters.append(CollusionCluster(
            member_doc_ids=member_ids,
            member_names=member_ns,
            risk_level=max_risk,
            aggregated_evidence=agg_evidence[:20],
            related_pair_ids=related_pairs,
            group_confidence=group_confidence,
            uncertain_edge_count=uncertain_edge_count,
        ))

    clusters.sort(key=lambda c: _RISK_PRIORITY.get(c.risk_level, 0), reverse=True)

    logger.info(
        f"[GraphCluster] 识别到 {len(clusters)} 个嫌疑群组, "
        f"风险分布: {[c.risk_level for c in clusters]}"
    )
    return clusters


# P0-3: D4/D8 全局发现 → aggregated_evidence 的 dimension 映射
_GLOBAL_FINDING_TYPE_TO_DIM = {
    "personnel_overlap": "D1",
    "company_relation": "D2",
    "pricing_pattern": "D4",
    "metadata_cluster": "D8",
}


def enrich_clusters_with_global_findings(
    clusters: list[CollusionCluster],
    global_features,
    doc_names: Optional[dict[int, str]] = None,
) -> list[dict]:
    """P0-3: 把 D4/D8 的 GlobalFindingItem 注入到覆盖群组的 aggregated_evidence，
    并返回未归入任何群组的"游离全局发现"列表（供 analyzer 保存到 Project 层）。

    该函数不生成新的图边（避免把"报价/元数据相似"直接升格为群组关系，
    防止污染 S3.5 的连通分量结果）；它只负责"附带呈现"——把 pattern/cluster
    注入它所覆盖的既有群组的证据，以及收集"游离部分"。

    Args:
        clusters: build_collusion_graph 返回的群组列表
        global_features: GlobalFeatureResult
        doc_names: {doc_id: filename}

    Returns:
        游离的 global_findings 列表，格式与 GlobalFindingItem 对齐：
        {type, involved_doc_ids, involved_names, description}
    """
    doc_names = doc_names or {}
    if global_features is None:
        return []

    global_findings = list(getattr(global_features, "global_findings", None) or [])
    if not global_findings:
        return []

    # D3 修订：处理全部四类全局发现（D1/D2/D4/D8）
    # 同源治理 — 以前 D1/D2 只作为图边，不进 aggregated_evidence，
    # 导致左侧"全局发现"展示了 D2 关联，但中栏 pair-level 找不到对应证据。
    extra: list[dict] = []
    for finding in global_findings:
        if not isinstance(finding, dict):
            continue
        ftype = str(finding.get("type") or "")
        if ftype not in _GLOBAL_FINDING_TYPE_TO_DIM:
            continue

        involved = list(finding.get("involved_doc_ids") or [])
        if not involved:
            continue

        dim = _GLOBAL_FINDING_TYPE_TO_DIM.get(ftype, "unknown")
        desc = finding.get("description") or ""
        evidence_item = {
            "dimension": dim,
            "description": desc,
            "source": "global_features",
            "finding_type": ftype,
            "involved_doc_ids": involved,
            "involved_doc_names": [
                doc_names.get(d, f"文件{d}") for d in involved
            ],
        }

        matched = False
        involved_set = set(involved)
        for cluster in clusters:
            member_set = set(cluster.member_doc_ids or [])
            if involved_set & member_set:
                # 覆盖到该群组 → 追加到 aggregated_evidence（不去重：融合阶段会去重）
                cluster.aggregated_evidence = (cluster.aggregated_evidence or []) + [evidence_item]
                matched = True

        if not matched:
            extra.append({
                "type": ftype,
                "involved_doc_ids": involved,
                "involved_names": [doc_names.get(d, f"文件{d}") for d in involved],
                "description": desc,
            })

    if extra:
        logger.info(
            f"[GraphCluster] 游离全局发现 {len(extra)} 条未命中任何群组，将保存到 Project.collusion_summary"
        )

    return extra


def _extract_top_findings(pair_result: dict, max_count: int = 3) -> list[dict]:
    """从 pair 对比结果中提取 top-N findings"""
    findings = pair_result.get("key_findings") or []
    result: list[dict] = []
    for f in findings[:max_count]:
        if isinstance(f, dict):
            result.append({
                "dimension": f.get("dimension") or f.get("dimension_label", ""),
                "description": f.get("summary") or f.get("description") or f.get("detail", ""),
                "source": "pair_comparison",
            })
        elif isinstance(f, str) and f.strip():
            result.append({
                "dimension": "unknown",
                "description": f,
                "source": "pair_comparison",
            })
    return result
