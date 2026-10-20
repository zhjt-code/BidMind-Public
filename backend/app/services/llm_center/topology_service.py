"""FlowTopologyService（R2-14 拆分产物）

LLM Center 流程/阶段/节点树查询，含 5 个 DB 计数 helper。

公共方法：
- list_flows: 获取三大流程列表（含 stage_count / node_count）
- get_flow_topology: 获取指定 flow 的拓扑（含 stages + nodes + edges）

私有 helper（被 list_flows / get_flow_topology / BatchService 共享）：
- _get_version_summary(node_id) -> (active_version, total_versions)
- _has_code_update(node_id) -> bool
- _has_draft(node_id) -> bool
- _get_param_count(node_id) -> dict
- _get_edges_for_nodes(node_ids, key_map) -> list[EdgeInfo]
"""

from __future__ import annotations

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_flow import LlmFlow, LlmStage
from app.models.llm_node import LlmNode
from app.models.llm_template_version import LlmTemplateVersion
from app.models.llm_param_def import LlmParamDef
from app.models.llm_node_edge import LlmNodeEdge
from app.schemas.llm_center import (
    FlowSummary, FlowTopology, StageInfo, NodeSummary, EdgeInfo,
)


class FlowTopologyService:
    """LLM Center 流程/拓扑查询服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_flows(self) -> list[FlowSummary]:
        """获取三大流程列表"""
        flows = (await self.db.execute(
            select(LlmFlow).order_by(LlmFlow.display_order)
        )).scalars().all()

        result = []
        for f in flows:
            stage_count = (await self.db.execute(
                select(func.count(LlmStage.id)).where(LlmStage.flow_id == f.id)
            )).scalar() or 0
            # 过滤已 soft-deactivate 的节点, 使计数与 get_flow_topology 展示一致。
            node_count = (await self.db.execute(
                select(func.count(LlmNode.id))
                .join(LlmStage)
                .where(LlmStage.flow_id == f.id, LlmNode.is_active == True)
            )).scalar() or 0
            result.append(FlowSummary(
                flow_key=f.flow_key, name=f.name, description=f.description,
                route_prefix=f.route_prefix, display_order=f.display_order,
                is_enabled=f.is_enabled, stage_count=stage_count, node_count=node_count,
            ))
        return result

    async def get_flow_topology(self, flow_key: str) -> FlowTopology | None:
        """获取流程拓扑（含阶段 + 节点树 + 连接关系）"""
        flow = (await self.db.execute(
            select(LlmFlow).where(LlmFlow.flow_key == flow_key)
        )).scalar_one_or_none()
        if not flow:
            return None

        stages = (await self.db.execute(
            select(LlmStage).where(LlmStage.flow_id == flow.id).order_by(LlmStage.display_order)
        )).scalars().all()

        stage_infos = []
        all_node_ids = []
        node_key_map: dict[int, str] = {}

        for s in stages:
            # 过滤掉 soft-deactivate 的节点 (seed_llm_registry.DEPRECATED_NODE_KEYS)
            # 这些节点 DB 数据保留供审计/回滚, 但 LLM Center UI 不再展示可编辑入口。
            nodes = (await self.db.execute(
                select(LlmNode)
                .where(LlmNode.stage_id == s.id, LlmNode.is_active == True)
                .order_by(LlmNode.display_order)
            )).scalars().all()

            node_summaries = []
            for n in nodes:
                all_node_ids.append(n.id)
                node_key_map[n.id] = n.node_key

                active_ver, total_vers = await self._get_version_summary(n.id)
                param_count = await self._get_param_count(n.id)
                has_draft = await self._has_draft(n.id)
                has_code_update = await self._has_code_update(n.id)

                node_summaries.append(NodeSummary(
                    node_key=n.node_key, name=n.name, description=n.description,
                    visibility=n.visibility, editable_level=n.editable_level,
                    execution_channel=n.execution_channel, call_method=n.call_method,
                    default_temperature=n.default_temperature, default_max_tokens=n.default_max_tokens,
                    node_class=getattr(n, "node_class", None) or "judgment",
                    active_version=active_ver, total_versions=total_vers,
                    param_count=param_count, has_draft=has_draft,
                    has_code_update=has_code_update,
                ))

            stage_infos.append(StageInfo(
                stage_key=s.stage_key, name=s.name, description=s.description,
                display_order=s.display_order, nodes=node_summaries,
            ))

        edges = await self._get_edges_for_nodes(all_node_ids, node_key_map)

        return FlowTopology(
            flow_key=flow.flow_key, name=flow.name, description=flow.description,
            stages=stage_infos, edges=edges,
        )

    # ──────────────────────────────────────────
    # 内部辅助（亦被 BatchService._collect_pending_nodes 复用）
    # ──────────────────────────────────────────

    async def _get_version_summary(self, node_id: int) -> tuple[int | None, int]:
        """返回 (active_version, total_versions)"""
        active = (await self.db.execute(
            select(LlmTemplateVersion.version)
            .where(LlmTemplateVersion.node_id == node_id, LlmTemplateVersion.is_active == True)
            .limit(1)
        )).scalar_one_or_none()

        total = (await self.db.execute(
            select(func.count(LlmTemplateVersion.id)).where(LlmTemplateVersion.node_id == node_id)
        )).scalar() or 0

        return active, total

    async def _has_code_update(self, node_id: int) -> bool:
        """检查是否有未激活的 source=code 新版本（代码已更新但未采纳）"""
        count = (await self.db.execute(
            select(func.count(LlmTemplateVersion.id)).where(
                LlmTemplateVersion.node_id == node_id,
                LlmTemplateVersion.source == "code",
                LlmTemplateVersion.is_active == False,
            )
        )).scalar() or 0
        return count > 0

    async def _has_draft(self, node_id: int) -> bool:
        """检查节点是否有未发布的草稿"""
        count = (await self.db.execute(
            select(func.count(LlmTemplateVersion.id)).where(
                LlmTemplateVersion.node_id == node_id,
                LlmTemplateVersion.status.in_(["draft", "validated"]),
            )
        )).scalar() or 0
        return count > 0

    async def _get_param_count(self, node_id: int) -> dict:
        """返回 {"input": n, "output": n, "runtime": n}"""
        rows = (await self.db.execute(
            select(LlmParamDef.direction, func.count(LlmParamDef.id))
            .where(LlmParamDef.node_id == node_id, LlmParamDef.status == "active")
            .group_by(LlmParamDef.direction)
        )).all()
        return {row[0]: row[1] for row in rows}

    async def _get_edges_for_nodes(
        self, node_ids: list[int], key_map: dict[int, str]
    ) -> list[EdgeInfo]:
        """获取给定节点集合内的边"""
        if not node_ids:
            return []
        edges = (await self.db.execute(
            select(LlmNodeEdge).where(
                LlmNodeEdge.from_node_id.in_(node_ids),
                LlmNodeEdge.to_node_id.in_(node_ids),
            ).order_by(LlmNodeEdge.display_order)
        )).scalars().all()

        return [EdgeInfo(
            from_node_key=key_map.get(e.from_node_id, ""),
            to_node_key=key_map.get(e.to_node_id, ""),
            edge_type=e.edge_type,
            param_mapping=e.param_mapping,
            description=e.description,
        ) for e in edges]
