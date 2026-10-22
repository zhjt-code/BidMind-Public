"""LLM Center Service

封装 LLM Center 的数据库查询与管理操作，供路由层调用。

R2-14 拆分进行中：sub-service 实现位于 `app/services/llm_center/` 子包，
主类 LlmCenterService 通过 __init__ 组合 + thin forwarder 维持外部 API 不变。
模块级 `_broadcast_invalidate` 通过 re-export 保持 routers/llm_center.py:249
的 `from app.services.llm_center_service import _broadcast_invalidate` 导入路径
byte-equal（防御）。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.llm_center import (
    FlowSummary,
    FlowTopology,
    ImpactAnalysisResponse,
    NodeConfigUpdate,
    NodeDetail,
    ParamDefCreate,
    ParamDefInfo,
    ParamDefUpdate,
    TemplateVersionCreate,
    TemplateVersionInfo,
    ValidationResult,
)
from app.services.llm_center._helpers import _broadcast_invalidate  # noqa: F401  # re-export
from app.services.llm_center.batch_service import BatchService
from app.services.llm_center.draft_service import DraftService
from app.services.llm_center.node_detail_service import NodeDetailService
from app.services.llm_center.topology_service import FlowTopologyService
from app.services.llm_center.trace_service import TraceService
from app.services.llm_center.validate_service import ValidateService
from app.services.llm_center.version_service import VersionService


class LlmCenterService:
    """LLM Center 服务（R2-14 拆分后：组合 sub-service + thin forwarder）"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        # Sub-services（R2-14 渐进式组合，Step 3-9 逐个填入）
        self.topology = FlowTopologyService(db)
        self.node_detail = NodeDetailService(db)
        self.draft = DraftService(db)
        self.validate = ValidateService(db)
        self.version = VersionService(db)
        self.trace = TraceService(db)
        self.batch = BatchService(
            db,
            topology=self.topology,
            validate=self.validate,
            version=self.version,
        )

    # ──────────────────────────────────────────
    # FlowTopologyService thin forwarders
    # ──────────────────────────────────────────

    async def list_flows(self) -> list[FlowSummary]:
        """获取三大流程列表"""
        return await self.topology.list_flows()

    async def get_flow_topology(self, flow_key: str) -> FlowTopology | None:
        """获取流程拓扑（含阶段 + 节点树 + 连接关系）"""
        return await self.topology.get_flow_topology(flow_key)

    # ──────────────────────────────────────────
    # NodeDetailService thin forwarders
    # ──────────────────────────────────────────

    async def get_node_detail(self, node_key: str) -> NodeDetail | None:
        """获取节点完整详情"""
        return await self.node_detail.get_node_detail(node_key)

    async def diff_versions(self, node_key: str, id1: int, id2: int) -> dict:
        """两版本 diff 对比"""
        return await self.node_detail.diff_versions(node_key, id1, id2)

    async def get_node_versions(self, node_key: str) -> list[TemplateVersionInfo]:
        """获取节点的全部版本列表"""
        return await self.node_detail.get_node_versions(node_key)

    async def get_node_params(self, node_key: str) -> list[ParamDefInfo]:
        """获取节点参数定义"""
        return await self.node_detail.get_node_params(node_key)

    # ──────────────────────────────────────────
    # DraftService thin forwarders
    # ──────────────────────────────────────────

    async def discard_draft(self, node_key: str, username: str = "") -> int:
        """丢弃未发布的草稿版本，返回删除数量"""
        return await self.draft.discard_draft(node_key, username)

    async def create_or_update_draft(
        self, node_key: str, data: TemplateVersionCreate, username: str
    ) -> TemplateVersionInfo:
        """创建/更新草稿版本"""
        return await self.draft.create_or_update_draft(node_key, data, username)

    # ──────────────────────────────────────────
    # ValidateService thin forwarder
    # ──────────────────────────────────────────

    async def validate_draft(self, node_key: str, username: str = "") -> ValidationResult:
        """校验草稿（占位符匹配 + 必填参数来源 + 下游兼容三检）"""
        return await self.validate.validate_draft(node_key, username)

    # ──────────────────────────────────────────
    # VersionService thin forwarders
    # ──────────────────────────────────────────

    async def publish(self, node_key: str, username: str, change_note: str | None = None) -> TemplateVersionInfo:
        """发布（将 validated 草稿设为 active，按 template_role 隔离，调用 _broadcast_invalidate(node_key) 通知其它进程刷新 PromptRegistry， 防御）"""
        return await self.version.publish(node_key, username, change_note)

    async def rollback(self, node_key: str, username: str) -> TemplateVersionInfo:
        """回滚到上一个 published 版本（调用 _broadcast_invalidate(node_key) 通知其它进程刷新 PromptRegistry， 防御）"""
        return await self.version.rollback(node_key, username)

    # ──────────────────────────────────────────
    # TraceService thin forwarders
    # ──────────────────────────────────────────

    async def test_run(self, node_key: str, param_values: dict, version_id: int | None = None, username: str = "") -> dict:
        """调试运行：使用指定参数执行单次 LLM 调用"""
        return await self.trace.test_run(node_key, param_values, version_id, username)

    async def update_node_config(self, node_key: str, data: NodeConfigUpdate) -> dict:
        """更新节点运行时配置（temperature/max_tokens，调用 _broadcast_invalidate(node_key) 通知其它进程刷新 PromptRegistry， 防御）"""
        return await self.version.update_node_config(node_key, data)

    # ══════════════════════════════════════════════
    # Phase 3: 参数管理 + 扩展接口
    # ══════════════════════════════════════════════

    async def create_param(self, node_key: str, data: ParamDefCreate) -> ParamDefInfo:
        """新增参数定义"""
        return await self.version.create_param(node_key, data)

    async def update_param(self, node_key: str, param_key: str, data: ParamDefUpdate) -> ParamDefInfo:
        """更新参数定义"""
        return await self.version.update_param(node_key, param_key, data)

    async def impact_analysis(self, node_key: str) -> ImpactAnalysisResponse:
        """变更影响分析"""
        return await self.version.impact_analysis(node_key)

    # ══════════════════════════════════════════════
    # Phase 5: 审计与可观测性
    # ══════════════════════════════════════════════

    async def query_execution_traces(
        self, node_key: str | None = None, project_id: int | None = None,
        limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        """查询执行轨迹"""
        return await self.trace.query_execution_traces(node_key, project_id, limit, offset)

    async def set_trace_mode(self, node_key: str, trace_mode: str) -> dict:
        """切换追踪模式"""
        return await self.trace.set_trace_mode(node_key, trace_mode)

    async def query_publish_history(self, node_key: str) -> list[dict]:
        """查询节点发布历史"""
        return await self.trace.query_publish_history(node_key)

    async def archive_old_traces(self, days: int = 30) -> int:
        """归档 N 天前的 full 模式明细记录"""
        return await self.trace.archive_old_traces(days)

    async def record_execution(
        self, node_key: str, project_id: int | None, latency_ms: float,
        prompt_tokens: int = 0, completion_tokens: int = 0,
        error: bool = False, model_name: str = "",
        execution_channel: str = "review",
    ) -> None:
        """记录一次 LLM 执行（聚合模式）"""
        return await self.trace.record_execution(
            node_key, project_id, latency_ms,
            prompt_tokens, completion_tokens,
            error, model_name, execution_channel,
        )

    # ══════════════════════════════════════════════
    # Phase 6: 批量校验与发布
    # ══════════════════════════════════════════════

    # ──────────────────────────────────────────
    # BatchService thin forwarders（Phase 6 共享 _collect_pending_nodes
    # 经 BatchService 内部 self.topology._has_draft 调用， 防御
    # 仍由 BatchService 委托 self.version.publish 与 self.validate.validate_draft，
    # 保持 _broadcast_invalidate 链路不变）
    # ──────────────────────────────────────────

    async def batch_validate(
        self,
        scope: str,
        flow_key: str | None,
        include_technical: bool,
        username: str,
    ) -> dict:
        """批量校验待处理节点 — 委托 BatchService（内部调用 _collect_pending_nodes）。"""
        return await self.batch.batch_validate(scope, flow_key, include_technical, username)

    async def batch_validate_and_publish(
        self,
        scope: str,
        flow_key: str | None,
        include_technical: bool,
        username: str,
        change_note: str | None = None,
    ) -> dict:
        """批量校验 + 全部通过则自动发布 — 委托 BatchService（内部调用 _collect_pending_nodes）。"""
        return await self.batch.batch_validate_and_publish(
            scope, flow_key, include_technical, username, change_note,
        )

    async def batch_publish_passed(
        self,
        node_keys: list[str],
        username: str,
        change_note: str | None = None,
    ) -> dict:
        """发布显式 node_keys 列表 — 委托 BatchService。

        注意：接受显式 node_keys 参数直接发布，不走 pending 收集（语义边界保护）。
        """
        return await self.batch.batch_publish_passed(node_keys, username, change_note)
