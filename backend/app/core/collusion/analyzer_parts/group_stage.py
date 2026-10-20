"""串标分析器 S3.5 + S4 群组方法

Task 8 Step 9：从 CollusionAnalyzer 主类迁出群组相关 6 个方法。
主类保留同名 thin forwarder 兼容外部调用。

迁出范围：
- save_groups（27 行群组 ORM 落库）
- fuse_group_evidence（44 行证据融合）
- save_group_findings（18 行群组 finding 落库）
- run_group_analysis（118 行群组 LLM 编排）
- retry_group_analysis（50 行群组重试）

子模块函数签名规范（方案 B）：函数名去前导下划线，self 名字保留。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger
from sqlalchemy import delete, select

# 补全 import 修复
from app.core.ai.prompt_registry import resolve_user_template
from app.core.collusion.analyzer_parts.synthesis_helpers import (
    _format_group_evidence_for_llm,
)
from app.core.collusion.evidence_fusion import EvidenceFusionEngine
from app.core.collusion.feature_flags import use_fused_evidence_to_llm
from app.core.collusion.global_features import GlobalFeatureResult
from app.core.collusion.graph_cluster import CollusionCluster
from app.core.collusion.prompts import COLLUSION_GROUP_ANALYSIS_PROMPT
from app.core.config import get_collusion_group_timeout
from app.core.database import async_session_factory
from app.models.collusion_group import CollusionGroup
from app.models.collusion_result import CollusionResult
from app.schemas.collusion import LLMGroupAnalysisResult


async def save_groups(self, clusters: list[CollusionCluster]) -> None:
    """将图聚类结果保存到 collusion_groups 表"""
    async with async_session_factory() as save_db:
        await save_db.execute(
            delete(CollusionGroup).where(
                CollusionGroup.project_id == self._project_id
            )
        )

        for idx, cluster in enumerate(clusters):
            save_db.add(CollusionGroup(
                project_id=self._project_id,
                group_index=idx,
                member_doc_ids=cluster.member_doc_ids,
                member_names=cluster.member_names,
                risk_level=cluster.risk_level,
                aggregated_evidence=cluster.aggregated_evidence,
                related_pair_ids=cluster.related_pair_ids,
                llm_group_analysis={
                    "group_confidence": cluster.group_confidence,
                    "uncertain_edge_count": cluster.uncertain_edge_count,
                },
            ))
        await save_db.commit()

    logger.info(f"[Analyzer] 保存{len(clusters)}个嫌疑群组")


async def fuse_group_evidence(
    self,
    clusters: list[CollusionCluster],
    doc_names: dict[int, str],
    global_features: GlobalFeatureResult,
) -> None:
    """S3.6: 将 pair 级证据融合为群组级证据"""
    from app.schemas.collusion import build_pair_id

    all_pair_ids: set[str] = set()
    for c in clusters:
        all_pair_ids.update(c.related_pair_ids or [])

    pair_findings_map: dict[str, list[dict]] = {}
    if all_pair_ids:
        async with async_session_factory() as db:
            rows = await db.execute(
                select(CollusionResult).where(
                    CollusionResult.project_id == self._project_id,
                )
            )
            for cr in rows.scalars():
                pid = build_pair_id(cr.doc_a_id, cr.doc_b_id)
                if pid in all_pair_ids:
                    pair_findings_map[pid] = cr.findings or []

    engine = EvidenceFusionEngine(doc_names, global_features)

    for cluster in clusters:
        fused = engine.fuse_for_group(
            member_doc_ids=cluster.member_doc_ids,
            related_pair_ids=cluster.related_pair_ids,
            pair_findings_map=pair_findings_map,
        )
        # P0-1: 融合产出直接挂到 cluster.fused_evidence 供 S4 LLM 优先消费；
        # aggregated_evidence 保留原始 pair 级证据，供图聚类/API/报告的既有
        # 逻辑继续倒推 D1/D2 等字段（避免占位符污染 LLM 输入）
        cluster.fused_evidence = list(fused) if isinstance(fused, list) else []
        await self._save_group_findings(
            cluster_index=clusters.index(cluster),
            fused_findings=fused,
        )


async def save_group_findings(
    self,
    cluster_index: int,
    fused_findings: list[dict],
) -> None:
    """将融合后的群组级证据写入 collusion_groups.group_findings"""
    async with async_session_factory() as save_db:
        rows = await save_db.execute(
            select(CollusionGroup).where(
                CollusionGroup.project_id == self._project_id,
                CollusionGroup.group_index == cluster_index,
            )
        )
        group_record = rows.scalar_one_or_none()
        if group_record:
            group_record.group_findings = fused_findings
        await save_db.commit()


async def _persist_group_analysis(self, idx: int, cluster: Any, result: dict) -> None:
    """将群组 LLM 研判结果回写到对应 collusion_groups 记录（首跑/重试共用）"""
    async with async_session_factory() as save_db:
        rows = await save_db.execute(
            select(CollusionGroup).where(
                CollusionGroup.project_id == self._project_id,
                CollusionGroup.group_index == idx,
            )
        )
        group_record = rows.scalar_one_or_none()
        if group_record:
            prev_meta = group_record.llm_group_analysis or {}
            group_record.risk_level = result.get("group_risk", cluster.risk_level)
            group_record.role_analysis = result.get("roles", [])
            group_record.collusion_strategy = result.get("collusion_strategy", "")
            group_record.investigation_focus = result.get("investigation_focus", [])
            group_record.network_description = result.get("key_evidence_summary", "")
            group_record.llm_group_analysis = {
                **prev_meta,
                **result,
                "group_confidence": prev_meta.get("group_confidence", cluster.group_confidence),
                "uncertain_edge_count": prev_meta.get("uncertain_edge_count", cluster.uncertain_edge_count),
            }
        await save_db.commit()


async def run_group_analysis(
    self,
    clusters: list[CollusionCluster],
    extractions: dict[int, dict],
    doc_names: dict[int, str],
    global_features: GlobalFeatureResult,
) -> None:
    """对每个嫌疑群组执行LLM研判"""
    for idx, cluster in enumerate(clusters):
        total_clusters = len(clusters)
        self._heartbeat(
            "S4",
            f"{idx}/{total_clusters}个群组",
            {
                "substage": "analyze_group",
                "current": idx,
                "total": total_clusters,
                "group_index": idx,
                "stage_progress_pct": int((idx / total_clusters) * 100) if total_clusters > 0 else 0,
                "message": f"正在研判第 {idx + 1}/{total_clusters} 个群组",
            },
        )
        try:
            member_list = json.dumps(
                [{"doc_id": d, "company_name": doc_names.get(d, f"文件{d}")}
                 for d in cluster.member_doc_ids],
                ensure_ascii=False, indent=2,
            )

            # P0-1: 灰度开启后优先消费 S3.6 融合证据（已按人名/企业关系桶去重合并）；
            # 未开启或融合结果为空时回退到原始 aggregated_evidence，保证兼容
            use_fused = (
                use_fused_evidence_to_llm(self._project_id)
                and bool(getattr(cluster, "fused_evidence", None))
            )
            agg_evidence_text = _format_group_evidence_for_llm(
                fused_evidence=cluster.fused_evidence if use_fused else None,
                aggregated_evidence=cluster.aggregated_evidence,
            )

            relevant_findings = [
                f for f in global_features.global_findings
                if set(f.get("involved_doc_ids", [])) & set(cluster.member_doc_ids)
            ]
            gf_text = "\n".join(
                f"- [{f.get('type', '?')}] {f.get('description', '')}"
                for f in relevant_findings[:5]
            ) or "(无全局特征发现)"

            async with self._semaphore:
                result = await self._call_llm(
                    user_message=resolve_user_template(
                        "collusion.group_analysis", COLLUSION_GROUP_ANALYSIS_PROMPT
                    ).format(
                        member_list=member_list,
                        aggregated_evidence=agg_evidence_text,
                        global_findings=gf_text,
                    ),
                    response_model=LLMGroupAnalysisResult,
                    timeout=get_collusion_group_timeout(),
                    max_tokens=1500,
                )

            await _persist_group_analysis(self, idx, cluster, result)

            logger.info(
                f"[Analyzer] 群组{idx}研判完成, risk={result.get('group_risk')}"
            )
        except Exception as e:
            logger.warning(f"[Analyzer] 群组{idx}研判失败: {e}")
            _idx, _cluster = idx, cluster
            _member_list, _agg_evidence_text, _gf_text = member_list, agg_evidence_text, gf_text
            self._group_retry.record(
                key=f"group_{idx}",
                retry_coro_factory=lambda _i=_idx, _c=_cluster, _ml=_member_list, _ae=_agg_evidence_text, _gf=_gf_text: self._retry_group_analysis(
                    _i, _c, _ml, _ae, _gf, doc_names,
                ),
                fallback_applied="skip_group",
            )

        self._heartbeat(
            "S4",
            f"{idx + 1}/{len(clusters)}个群组分析完成",
            {
                "substage": "analyze_group",
                "current": idx + 1,
                "total": len(clusters),
                "stage_progress_pct": int((idx + 1) / len(clusters) * 100),
                "message": f"已完成 {idx + 1}/{len(clusters)} 个群组研判",
            },
        )

    if self._group_retry.has_failures:
        await self._group_retry.retry_all()


async def retry_group_analysis(
    self,
    idx: int,
    cluster: Any,
    member_list: str,
    agg_evidence_text: str,
    gf_text: str,
    doc_names: dict,
) -> Optional[dict]:
    """群组研判重试: LLM 调用 + DB 写入"""
    async with self._semaphore:
        result = await self._call_llm(
            user_message=resolve_user_template(
                "collusion.group_analysis", COLLUSION_GROUP_ANALYSIS_PROMPT
            ).format(
                member_list=member_list,
                aggregated_evidence=agg_evidence_text,
                global_findings=gf_text,
            ),
            response_model=LLMGroupAnalysisResult,
            timeout=get_collusion_group_timeout(),
            max_tokens=1500,
        )

    await _persist_group_analysis(self, idx, cluster, result)

    logger.info(f"[Analyzer] 群组{idx}研判重试成功, risk={result.get('group_risk')}")
    return result
