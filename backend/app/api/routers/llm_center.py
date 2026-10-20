"""LLM Center -- 只读 API 路由（Phase 1）

提供流程拓扑、节点详情、版本列表、参数定义的只读查询。
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin
from app.core.database import get_db
from app.schemas.llm_center import (
    BatchPublishPassedRequest,
    BatchRunResponse,
    BatchScopeRequest,
    FlowSummary,
    FlowTopology,
    ImpactAnalysisResponse,
    NodeConfigUpdate,
    NodeDetail,
    ParamDefCreate,
    ParamDefInfo,
    ParamDefUpdate,
    PublishRequest,
    RegistryStatus,
    SyncReport,
    TemplateVersionCreate,
    TemplateVersionInfo,
    TestRunRequest,
    ValidationResult,
)
from app.services.llm_center_service import LlmCenterService

router = APIRouter(prefix="/admin/llm-center", tags=["LLM Center"])


def _get_service(db: AsyncSession = Depends(get_db)) -> LlmCenterService:
    return LlmCenterService(db)


# ── 流程拓扑（只读）──

@router.get("/flows", response_model=list[FlowSummary])
async def list_flows(
    service: LlmCenterService = Depends(_get_service),
    _user=Depends(require_admin),
):
    """获取三大流程列表"""
    return await service.list_flows()


@router.get("/flows/{flow_key}", response_model=FlowTopology)
async def get_flow_topology(
    flow_key: str,
    service: LlmCenterService = Depends(_get_service),
    _user=Depends(require_admin),
):
    """获取流程拓扑（含阶段 + 节点树 + 连接关系）"""
    topo = await service.get_flow_topology(flow_key)
    if not topo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"流程 '{flow_key}' 不存在")
    return topo


# ── 节点详情 ──

@router.get("/nodes/{node_key}", response_model=NodeDetail)
async def get_node_detail(
    node_key: str,
    service: LlmCenterService = Depends(_get_service),
    _user=Depends(require_admin),
):
    """获取节点完整信息"""
    detail = await service.get_node_detail(node_key)
    if not detail:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"节点 '{node_key}' 不存在")
    return detail


# ── Prompt 版本 ──

@router.get("/nodes/{node_key}/versions", response_model=list[TemplateVersionInfo])
async def list_node_versions(
    node_key: str,
    service: LlmCenterService = Depends(_get_service),
    _user=Depends(require_admin),
):
    """获取节点的全部版本列表"""
    return await service.get_node_versions(node_key)


@router.get("/nodes/{node_key}/versions/{id1}/diff/{id2}")
async def diff_versions(
    node_key: str,
    id1: int,
    id2: int,
    service: LlmCenterService = Depends(_get_service),
    _user=Depends(require_admin),
):
    """两版本 diff 对比"""
    return await service.diff_versions(node_key, id1, id2)


# ── 参数定义 ──

@router.get("/nodes/{node_key}/params", response_model=list[ParamDefInfo])
async def list_node_params(
    node_key: str,
    service: LlmCenterService = Depends(_get_service),
    _user=Depends(require_admin),
):
    """获取节点参数定义"""
    return await service.get_node_params(node_key)


# ── Registry 状态 ──

@router.get("/registry/status", response_model=RegistryStatus)
async def get_registry_status(
    service: LlmCenterService = Depends(_get_service),
    _user=Depends(require_admin),
):
    """获取 Registry 状态"""
    from app.core.ai.prompt_registry import get_registry
    registry = get_registry()
    flows = await service.list_flows()
    return RegistryStatus(
        total_flows=len(flows),
        total_stages=sum(f.stage_count for f in flows),
        total_nodes=sum(f.node_count for f in flows),
        total_active_versions=len(registry.list_node_keys()) if registry.is_loaded else 0,
    )


# ══════════════════════════════════════════════
# Phase 2: 受控编辑
# ══════════════════════════════════════════════

@router.delete("/nodes/{node_key}/draft")
async def discard_draft(
    node_key: str,
    service: LlmCenterService = Depends(_get_service),
    user=Depends(require_admin),
):
    """丢弃未发布的草稿版本"""
    count = await service.discard_draft(node_key, user.username)
    return {"message": f"已丢弃 {count} 个草稿版本", "discarded": count}


@router.post("/nodes/{node_key}/draft", response_model=TemplateVersionInfo)
async def create_draft(
    node_key: str,
    data: TemplateVersionCreate,
    service: LlmCenterService = Depends(_get_service),
    user=Depends(require_admin),
):
    """创建/更新草稿版本"""
    return await service.create_or_update_draft(node_key, data, user.username)


@router.post("/nodes/{node_key}/validate", response_model=ValidationResult)
async def validate_draft(
    node_key: str,
    service: LlmCenterService = Depends(_get_service),
    user=Depends(require_admin),
):
    """校验草稿"""
    return await service.validate_draft(node_key, user.username)


@router.post("/nodes/{node_key}/publish", response_model=TemplateVersionInfo)
async def publish_version(
    node_key: str,
    data: PublishRequest = PublishRequest(),
    service: LlmCenterService = Depends(_get_service),
    user=Depends(require_admin),
):
    """发布（校验通过后）"""
    from app.core.exceptions import ServiceError
    try:
        return await service.publish(node_key, user.username, data.change_note)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post("/nodes/{node_key}/rollback", response_model=TemplateVersionInfo)
async def rollback_version(
    node_key: str,
    service: LlmCenterService = Depends(_get_service),
    user=Depends(require_admin),
):
    """回滚到上一个 published 版本"""
    from app.core.exceptions import ServiceError
    try:
        return await service.rollback(node_key, user.username)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post("/nodes/{node_key}/test-run")
async def test_run(
    node_key: str,
    data: TestRunRequest,
    service: LlmCenterService = Depends(_get_service),
    user=Depends(require_admin),
):
    """调试运行（使用指定版本执行单次 LLM 调用）"""
    return await service.test_run(node_key, data.param_values, data.version_id, user.username)


@router.put("/nodes/{node_key}/config")
async def update_node_config(
    node_key: str,
    data: NodeConfigUpdate,
    service: LlmCenterService = Depends(_get_service),
    _user=Depends(require_admin),
):
    """更新节点运行时配置（temperature/max_tokens）"""
    updated = await service.update_node_config(node_key, data)
    return {"message": "配置已更新", "updated": updated}


# ══════════════════════════════════════════════
# Phase 3: 参数管理 + 扩展接口
# ══════════════════════════════════════════════

@router.post("/nodes/{node_key}/params", response_model=ParamDefInfo)
async def create_param(
    node_key: str,
    data: ParamDefCreate,
    service: LlmCenterService = Depends(_get_service),
    _user=Depends(require_admin),
):
    """新增参数定义"""
    return await service.create_param(node_key, data)


@router.patch("/nodes/{node_key}/params/{param_key}", response_model=ParamDefInfo)
async def update_param(
    node_key: str,
    param_key: str,
    data: ParamDefUpdate,
    service: LlmCenterService = Depends(_get_service),
    _user=Depends(require_admin),
):
    """更新参数（说明/状态/默认值）"""
    return await service.update_param(node_key, param_key, data)


@router.post("/nodes/{node_key}/impact-analysis", response_model=ImpactAnalysisResponse)
async def impact_analysis(
    node_key: str,
    service: LlmCenterService = Depends(_get_service),
    _user=Depends(require_admin),
):
    """变更影响分析（发布前）"""
    return await service.impact_analysis(node_key)


@router.post("/registry/resync", response_model=SyncReport)
async def resync_registry(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """手动触发代码 -> DB 同步"""
    from loguru import logger

    from app.core.ai.prompt_registry import get_registry
    from app.services.audit_service import audit_log
    from app.services.llm_center_service import _broadcast_invalidate
    stats = await get_registry().registry_sync(db)
    logger.info(f"[LLM Center] resync_registry: {stats} (by {user.username})")
    # 通知其它进程 (worker / 多 uvicorn worker) 做全量 reload
    await _broadcast_invalidate(None)
    await audit_log(
        db, username=user.username, category="llm_center",
        action="resync_registry", resource_type="llm_registry",
        detail=f"手动同步 Registry: {stats}",
    )
    return SyncReport(
        new_nodes=[f"{stats.get('nodes', 0)} nodes"],
        updated_nodes=[],
        conflicts=[],
        skipped=[],
    )


# ══════════════════════════════════════════════
# Phase 5: 审计与可观测性
# ══════════════════════════════════════════════

@router.get("/executions")
async def query_executions(
    node_key: str | None = None,
    project_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    service: LlmCenterService = Depends(_get_service),
    _user=Depends(require_admin),
):
    """查询执行轨迹"""
    return await service.query_execution_traces(node_key, project_id, limit, offset)


@router.put("/nodes/{node_key}/trace-mode")
async def set_trace_mode(
    node_key: str,
    trace_mode: str = "aggregate",
    service: LlmCenterService = Depends(_get_service),
    _user=Depends(require_admin),
):
    """切换追踪模式"""
    return await service.set_trace_mode(node_key, trace_mode)


@router.get("/nodes/{node_key}/publish-history")
async def get_publish_history(
    node_key: str,
    service: LlmCenterService = Depends(_get_service),
    _user=Depends(require_admin),
):
    """查询节点发布历史"""
    return await service.query_publish_history(node_key)


@router.post("/executions/archive")
async def archive_traces(
    days: int = 30,
    service: LlmCenterService = Depends(_get_service),
    _user=Depends(require_admin),
):
    """归档旧的 full 模式执行轨迹"""
    count = await service.archive_old_traces(days)
    return {"message": f"已归档 {count} 条 full 模式轨迹 (>{days}天)", "archived": count}


# ══════════════════════════════════════════════
# Phase 6: 批量校验与发布
# ══════════════════════════════════════════════

@router.post("/batch/validate", response_model=BatchRunResponse)
async def batch_validate(
    data: BatchScopeRequest,
    service: LlmCenterService = Depends(_get_service),
    user=Depends(require_admin),
):
    """批量校验当前流程或全部待处理节点的草稿"""
    return await service.batch_validate(
        scope=data.scope,
        flow_key=data.flow_key,
        include_technical=data.include_technical,
        username=user.username,
    )


@router.post("/batch/validate-publish", response_model=BatchRunResponse)
async def batch_validate_publish(
    data: BatchScopeRequest,
    service: LlmCenterService = Depends(_get_service),
    user=Depends(require_admin),
):
    """批量校验并发布：全部通过则自动发布，否则返回结果供决策"""
    return await service.batch_validate_and_publish(
        scope=data.scope,
        flow_key=data.flow_key,
        include_technical=data.include_technical,
        username=user.username,
        change_note=data.change_note,
    )


@router.post("/batch/publish-passed", response_model=BatchRunResponse)
async def batch_publish_passed(
    data: BatchPublishPassedRequest,
    service: LlmCenterService = Depends(_get_service),
    user=Depends(require_admin),
):
    """发布已通过校验的节点列表（显式二次确认）"""
    return await service.batch_publish_passed(
        node_keys=data.node_keys,
        username=user.username,
        change_note=data.change_note,
    )
