"""BatchService（R2-14 拆分产物）

LLM Center 批量校验 + 批量校验发布 + 批量发布通过项。

公共方法（基于 Plan §4.5 共享 helper 设计）：
- batch_validate: 批量校验当前流程或全部待处理节点的草稿
- batch_validate_and_publish: 批量校验 + 全部通过则自动发布
- batch_publish_passed: 发布显式 node_keys 列表（不再 _collect_pending_nodes）

私有 helper：
- _collect_pending_nodes: 收集待处理节点（依赖 FlowTopologyService._has_draft）

依赖注入：
- FlowTopologyService（_collect_pending_nodes 内部用 _has_draft）
- ValidateService（batch_validate 调 validate_draft）
- VersionService（batch_validate_and_publish / batch_publish_passed 调 publish）

审计：batch_validate / batch_validate_publish / batch_publish_passed 写
audit_log（防御）。
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_flow import LlmFlow, LlmStage
from app.models.llm_node import LlmNode
from app.models.llm_node_edge import LlmNodeEdge

from app.services.llm_center.topology_service import FlowTopologyService
from app.services.llm_center.validate_service import ValidateService
from app.services.llm_center.version_service import VersionService


class BatchService:
    """
    职责: 编排 LLM Center 批量校验、发布与发布前注册同步。
    不做: 不直接实现单节点版本切换、Prompt 校验细节或拓扑查询。
    """

    def __init__(
        self,
        db: AsyncSession,
        topology: FlowTopologyService,
        validate: ValidateService,
        version: VersionService,
    ) -> None:
        self.db = db
        self.topology = topology
        self.validate = validate
        self.version = version

    async def _collect_pending_nodes(
        self,
        scope: str,
        flow_key: str | None = None,
        include_technical: bool = False,
    ) -> list[dict]:
        """收集待处理节点(有 draft/validated 版本)，返回带链路信息的列表。"""
        if scope == "flow":
            if not flow_key:
                from app.core.exceptions import ServiceError
                raise ServiceError("scope=flow 时必须指定 flow_key")
            flow_keys = [flow_key]
        else:
            flows = (await self.db.execute(
                select(LlmFlow).order_by(LlmFlow.display_order)
            )).scalars().all()
            flow_keys = [f.flow_key for f in flows]

        result: list[dict] = []
        for fk in flow_keys:
            flow = (await self.db.execute(
                select(LlmFlow).where(LlmFlow.flow_key == fk)
            )).scalar_one_or_none()
            if not flow:
                continue
            stages = (await self.db.execute(
                select(LlmStage).where(LlmStage.flow_id == flow.id)
                .order_by(LlmStage.display_order)
            )).scalars().all()
            for stage in stages:
                nodes = (await self.db.execute(
                    select(LlmNode).where(LlmNode.stage_id == stage.id)
                    .order_by(LlmNode.display_order)
                )).scalars().all()
                for node in nodes:
                    if not include_technical and node.visibility == "technical":
                        continue
                    has_pending = await self.topology._has_draft(node.id)
                    if not has_pending:
                        continue

                    downstream_edges = (await self.db.execute(
                        select(LlmNodeEdge).where(
                            LlmNodeEdge.from_node_id == node.id
                        )
                    )).scalars().all()
                    downstream_keys = []
                    for edge in downstream_edges:
                        dn = (await self.db.execute(
                            select(LlmNode.node_key).where(
                                LlmNode.id == edge.to_node_id
                            )
                        )).scalar_one_or_none()
                        if dn:
                            downstream_keys.append(dn)

                    result.append({
                        "node_id": node.id,
                        "node_key": node.node_key,
                        "node_name": node.name,
                        "flow_key": flow.flow_key,
                        "flow_name": flow.name,
                        "stage_key": stage.stage_key,
                        "stage_name": stage.name,
                        "path_text": f"{flow.name} > {stage.name} > {node.name}",
                        "downstream_nodes": downstream_keys,
                    })
        return result

    async def _registry_sync_before_collecting(self) -> dict:
        """
        职责: 在批量发布扫描前把代码注册源同步进 DB 并刷新缓存。
        不做: 不校验草稿、不发布版本、不按流程范围裁剪同步结果。
        """
        from app.core.ai.prompt_registry import get_registry
        from app.services.llm_center._helpers import _broadcast_invalidate

        stats = await get_registry().registry_sync(self.db)
        await _broadcast_invalidate(None)
        return stats

    async def batch_validate(
        self,
        scope: str,
        flow_key: str | None,
        include_technical: bool,
        username: str,
    ) -> dict:
        """批量校验待处理节点，返回 BatchRunResponse 结构的 dict。"""
        from app.schemas.llm_center import (
            BatchRunResponse, BatchRunItem, BatchRunItemCheck,
            BatchRunSummary,
        )

        pending = await self._collect_pending_nodes(
            scope, flow_key, include_technical,
        )
        items: list[BatchRunItem] = []
        passed_count = 0
        failed_count = 0

        for info in pending:
            try:
                vr = await self.validate.validate_draft(info["node_key"], username)
                checks = [
                    BatchRunItemCheck(
                        check_type=c.check_type,
                        passed=c.passed,
                        message=c.message,
                        details=c.details,
                    )
                    for c in vr.checks
                ]
                if vr.passed:
                    passed_count += 1
                    status = "validated"
                else:
                    failed_count += 1
                    status = "validation_failed"
                items.append(BatchRunItem(
                    node_key=info["node_key"],
                    node_name=info["node_name"],
                    flow_key=info["flow_key"],
                    flow_name=info["flow_name"],
                    stage_key=info["stage_key"],
                    stage_name=info["stage_name"],
                    path_text=info["path_text"],
                    status=status,
                    checks=checks,
                    downstream_nodes=info["downstream_nodes"],
                ))
            except Exception as e:
                failed_count += 1
                items.append(BatchRunItem(
                    node_key=info["node_key"],
                    node_name=info["node_name"],
                    flow_key=info["flow_key"],
                    flow_name=info["flow_name"],
                    stage_key=info["stage_key"],
                    stage_name=info["stage_name"],
                    path_text=info["path_text"],
                    status="validation_failed",
                    checks=[],
                    downstream_nodes=info["downstream_nodes"],
                    error_message=str(e),
                ))

        summary = BatchRunSummary(
            total=len(pending),
            validated_passed=passed_count,
            validated_failed=failed_count,
        )

        from app.services.audit_service import audit_log
        scope_desc = f"flow={flow_key}" if scope == "flow" else "all_pending"
        await audit_log(
            self.db, username=username, category="llm_center",
            action="batch_validate", resource_type="llm_registry",
            detail=(
                f"批量校验 ({scope_desc}): "
                f"{summary.total} 节点, {passed_count} 通过, {failed_count} 失败"
            ),
        )

        resp = BatchRunResponse(
            scope=scope,
            flow_key=flow_key,
            summary=summary,
            can_publish_all=failed_count == 0 and passed_count > 0,
            can_publish_passed=passed_count > 0,
            items=items,
        )
        return resp.model_dump()

    async def batch_validate_and_publish(
        self,
        scope: str,
        flow_key: str | None,
        include_technical: bool,
        username: str,
        change_note: str | None = None,
    ) -> dict:
        """批量校验 + 全部通过则自动发布，否则返回结果供用户决策。"""
        from app.schemas.llm_center import (
            BatchRunResponse, BatchRunItem, BatchRunItemCheck,
            BatchRunSummary,
        )

        # 发布入口必须先 registry_sync，再扫描 draft/validated，
        # 否则代码新增 LLM 节点无法在本次点击中完成注册与激活闭环。
        sync_stats = await self._registry_sync_before_collecting()
        pending = await self._collect_pending_nodes(
            scope, flow_key, include_technical,
        )
        items: list[BatchRunItem] = []
        passed_count = 0
        failed_count = 0
        passed_keys: list[str] = []

        for info in pending:
            try:
                vr = await self.validate.validate_draft(info["node_key"], username)
                checks = [
                    BatchRunItemCheck(
                        check_type=c.check_type,
                        passed=c.passed,
                        message=c.message,
                        details=c.details,
                    )
                    for c in vr.checks
                ]
                if vr.passed:
                    passed_count += 1
                    passed_keys.append(info["node_key"])
                    status = "validated"
                else:
                    failed_count += 1
                    status = "validation_failed"
                items.append(BatchRunItem(
                    node_key=info["node_key"],
                    node_name=info["node_name"],
                    flow_key=info["flow_key"],
                    flow_name=info["flow_name"],
                    stage_key=info["stage_key"],
                    stage_name=info["stage_name"],
                    path_text=info["path_text"],
                    status=status,
                    checks=checks,
                    downstream_nodes=info["downstream_nodes"],
                ))
            except Exception as e:
                failed_count += 1
                items.append(BatchRunItem(
                    node_key=info["node_key"],
                    node_name=info["node_name"],
                    flow_key=info["flow_key"],
                    flow_name=info["flow_name"],
                    stage_key=info["stage_key"],
                    stage_name=info["stage_name"],
                    path_text=info["path_text"],
                    status="validation_failed",
                    checks=[],
                    downstream_nodes=info["downstream_nodes"],
                    error_message=str(e),
                ))

        published_count = 0
        if failed_count == 0 and passed_keys:
            for nk in passed_keys:
                try:
                    await self.version.publish(nk, username, change_note)
                    published_count += 1
                    for item in items:
                        if item.node_key == nk:
                            item.status = "published"
                except Exception as e:
                    for item in items:
                        if item.node_key == nk:
                            item.status = "publish_failed"
                            item.error_message = str(e)

        summary = BatchRunSummary(
            total=len(pending),
            validated_passed=passed_count,
            validated_failed=failed_count,
            published=published_count,
        )

        from app.services.audit_service import audit_log
        scope_desc = f"flow={flow_key}" if scope == "flow" else "all_pending"
        await audit_log(
            self.db, username=username, category="llm_center",
            action="batch_validate_publish", resource_type="llm_registry",
            detail=(
                f"批量校验发布 ({scope_desc}): "
                f"{summary.total} 节点, {passed_count} 通过, "
                f"{failed_count} 失败, {published_count} 已发布; "
                f"registry_sync={json.dumps(sync_stats, ensure_ascii=False)}"
            ),
        )

        resp = BatchRunResponse(
            scope=scope,
            flow_key=flow_key,
            summary=summary,
            can_publish_all=False,
            can_publish_passed=failed_count > 0 and passed_count > 0,
            items=items,
        )
        return resp.model_dump()

    async def batch_publish_passed(
        self,
        node_keys: list[str],
        username: str,
        change_note: str | None = None,
    ) -> dict:
        """发布指定的已通过校验节点列表。"""
        from app.schemas.llm_center import (
            BatchRunResponse, BatchRunItem, BatchRunSummary,
        )

        items: list[BatchRunItem] = []
        published_count = 0
        failed_count = 0

        for nk in node_keys:
            node = (await self.db.execute(
                select(LlmNode).where(LlmNode.node_key == nk)
            )).scalar_one_or_none()
            if not node:
                failed_count += 1
                items.append(BatchRunItem(
                    node_key=nk, node_name=nk,
                    flow_key="", flow_name="",
                    stage_key="", stage_name="",
                    path_text=nk,
                    status="publish_failed",
                    error_message=f"节点 '{nk}' 不存在",
                ))
                continue

            stage = (await self.db.execute(
                select(LlmStage).where(LlmStage.id == node.stage_id)
            )).scalar_one_or_none()
            flow = None
            if stage:
                flow = (await self.db.execute(
                    select(LlmFlow).where(LlmFlow.id == stage.flow_id)
                )).scalar_one_or_none()

            path_text = f"{flow.name if flow else '?'} > {stage.name if stage else '?'} > {node.name}"

            try:
                await self.version.publish(nk, username, change_note)
                published_count += 1
                items.append(BatchRunItem(
                    node_key=nk, node_name=node.name,
                    flow_key=flow.flow_key if flow else "",
                    flow_name=flow.name if flow else "",
                    stage_key=stage.stage_key if stage else "",
                    stage_name=stage.name if stage else "",
                    path_text=path_text,
                    status="published",
                ))
            except Exception as e:
                failed_count += 1
                items.append(BatchRunItem(
                    node_key=nk, node_name=node.name,
                    flow_key=flow.flow_key if flow else "",
                    flow_name=flow.name if flow else "",
                    stage_key=stage.stage_key if stage else "",
                    stage_name=stage.name if stage else "",
                    path_text=path_text,
                    status="publish_failed",
                    error_message=str(e),
                ))

        summary = BatchRunSummary(
            total=len(node_keys),
            published=published_count,
            validated_failed=failed_count,
        )

        from app.services.audit_service import audit_log
        await audit_log(
            self.db, username=username, category="llm_center",
            action="batch_publish_passed", resource_type="llm_registry",
            detail=(
                f"批量发布通过项: {len(node_keys)} 节点, "
                f"{published_count} 已发布, {failed_count} 失败"
            ),
        )

        resp = BatchRunResponse(
            scope="selected",
            summary=summary,
            items=items,
        )
        return resp.model_dump()
