"""LLM Center Schema 定义

包含流程拓扑、节点详情、模板版本、参数定义、校验结果、
调试运行、影响分析等全部请求/响应数据模型。
"""

from datetime import datetime
from typing import Literal, Optional, Any

from pydantic import BaseModel, Field


# ══════════════════════════════════════════════
# 流程拓扑
# ══════════════════════════════════════════════

class FlowSummary(BaseModel):
    """流程列表项"""
    flow_key: str
    name: str
    description: Optional[str] = None
    route_prefix: Optional[str] = None
    display_order: int
    is_enabled: bool
    stage_count: int = 0
    node_count: int = 0

    class Config:
        from_attributes = True


class EdgeInfo(BaseModel):
    """节点连接关系"""
    from_node_key: str
    to_node_key: str
    edge_type: str = Field(description="data_flow / conditional / optional")
    param_mapping: Optional[dict] = None
    description: Optional[str] = None


class NodeSummary(BaseModel):
    """节点摘要（流程图卡片用）"""
    node_key: str
    name: str
    description: Optional[str] = None
    visibility: str = Field(description="main / technical")
    editable_level: str = Field(description="readonly / advanced / admin_only")
    execution_channel: str = Field(description="review / vision")
    call_method: str
    default_temperature: float
    default_max_tokens: int
    node_class: str = "judgment"
    active_version: Optional[int] = None
    total_versions: int = 0
    param_count: dict = Field(default_factory=dict, description='{"input": 3, "output": 2, "runtime": 2}')
    has_code_update: bool = False
    has_draft: bool = False
    last_published_at: Optional[datetime] = None
    recent_error_rate: Optional[float] = None

    class Config:
        from_attributes = True


class StageInfo(BaseModel):
    """阶段信息"""
    stage_key: str
    name: str
    description: Optional[str] = None
    display_order: int
    nodes: list[NodeSummary] = Field(default_factory=list)

    class Config:
        from_attributes = True


class FlowTopology(BaseModel):
    """流程拓扑（前端渲染用）"""
    flow_key: str
    name: str
    description: Optional[str] = None
    stages: list[StageInfo] = Field(default_factory=list)
    edges: list[EdgeInfo] = Field(default_factory=list)

    class Config:
        from_attributes = True


# ══════════════════════════════════════════════
# 模板版本
# ══════════════════════════════════════════════

class TemplateVersionInfo(BaseModel):
    """模板版本信息"""
    id: int
    template_role: str = Field(description="system / user")
    version: int
    content: str
    is_active: bool
    source: str = Field(description="code / db")
    status: str = Field(description="draft / validated / published / archived")
    change_note: Optional[str] = None
    created_by: Optional[str] = None
    validated_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class TemplateVersionCreate(BaseModel):
    """创建/更新草稿"""
    template_role: str = Field(default="system", description="system / user")
    content: str = Field(..., min_length=1, description="Prompt 模板文本")
    change_note: Optional[str] = None


# ══════════════════════════════════════════════
# 参数定义
# ══════════════════════════════════════════════

class ParamDefInfo(BaseModel):
    """参数定义（前端 schema-driven 渲染用）"""
    id: int
    param_key: str
    direction: str = Field(description="input / output / runtime")
    data_type: str
    is_required: bool
    default_value: Optional[str] = None
    description: Optional[str] = None
    source_type: str = Field(description="upstream / manual / system / derived")
    source_hint: Optional[str] = None
    example_value: Optional[str] = None
    ui_hint: str = Field(description="tooltip / tag / hidden")
    status: str = Field(description="active / deprecated / removed")

    class Config:
        from_attributes = True


class ParamDefCreate(BaseModel):
    """新增参数定义"""
    param_key: str = Field(..., min_length=1, max_length=80)
    direction: str = Field(..., description="input / output / runtime")
    data_type: str = Field(default="string")
    is_required: bool = True
    default_value: Optional[str] = None
    description: Optional[str] = None
    source_type: str = Field(default="manual")
    source_hint: Optional[str] = None
    example_value: Optional[str] = None
    ui_hint: str = Field(default="tooltip")


class ParamDefUpdate(BaseModel):
    """更新参数定义"""
    description: Optional[str] = None
    default_value: Optional[str] = None
    source_type: Optional[str] = None
    source_hint: Optional[str] = None
    example_value: Optional[str] = None
    ui_hint: Optional[str] = None
    status: Optional[str] = None


# ══════════════════════════════════════════════
# 节点详情
# ══════════════════════════════════════════════

class NodeDetail(BaseModel):
    """节点完整详情"""
    node_key: str
    name: str
    description: Optional[str] = None
    builder_key: Optional[str] = None
    resolver_key: Optional[str] = None
    source_file: Optional[str] = None
    execution_channel: str
    call_method: str
    default_temperature: float
    default_max_tokens: int
    output_schema_name: Optional[str] = None
    visibility: str
    editable_level: str
    supports_hot_reload: bool
    active_prompt: Optional[TemplateVersionInfo] = None
    all_versions: list[TemplateVersionInfo] = Field(default_factory=list)
    params: list[ParamDefInfo] = Field(default_factory=list)

    class Config:
        from_attributes = True


class NodeConfigUpdate(BaseModel):
    """更新节点运行时配置"""
    default_temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    default_max_tokens: Optional[int] = Field(default=None, ge=100, le=32000)
    node_class: Optional[Literal["mechanical", "judgment"]] = None


# ══════════════════════════════════════════════
# 校验与发布
# ══════════════════════════════════════════════

class CheckItem(BaseModel):
    """单项校验结果"""
    check_type: str = Field(description="placeholder_match / required_source / downstream_compat / running_impact")
    passed: bool
    message: str
    details: Optional[dict] = None


class ValidationResult(BaseModel):
    """校验结果"""
    passed: bool
    checks: list[CheckItem] = Field(default_factory=list)


class PublishRequest(BaseModel):
    """发布请求"""
    change_note: Optional[str] = None


class ImpactAnalysisResponse(BaseModel):
    """变更影响分析"""
    affected_downstream_nodes: list[str] = Field(default_factory=list)
    running_projects_count: int = 0
    risk_level: str = Field(description="safe / warning / danger")
    recommendation: str = ""


# ══════════════════════════════════════════════
# 调试运行
# ══════════════════════════════════════════════

class TestRunRequest(BaseModel):
    """调试运行请求"""
    param_values: dict[str, Any] = Field(default_factory=dict, description="输入参数的测试值")
    version_id: Optional[int] = Field(default=None, description="指定版本，None 用当前激活版")


class TestRunResponse(BaseModel):
    """调试运行结果"""
    raw_output: str = ""
    parsed_output: Optional[dict] = None
    token_usage: dict = Field(default_factory=dict)
    latency_ms: float = 0.0
    model_name: str = ""
    success: bool = False
    error: Optional[str] = None


# ══════════════════════════════════════════════
# 执行轨迹
# ══════════════════════════════════════════════

class ExecutionTraceInfo(BaseModel):
    """执行轨迹记录"""
    id: int
    project_id: Optional[int] = None
    node_key: str
    template_version_id: Optional[int] = None
    execution_channel: Optional[str] = None
    model_name: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    trace_mode: str
    call_count: int = 0
    total_latency_ms: float = 0.0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    error_count: int = 0
    retry_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TraceModeUpdate(BaseModel):
    """切换追踪模式"""
    trace_mode: str = Field(..., description="aggregate / full")


# ══════════════════════════════════════════════
# Registry 状态
# ══════════════════════════════════════════════

class RegistryStatus(BaseModel):
    """Registry 状态"""
    total_flows: int = 0
    total_stages: int = 0
    total_nodes: int = 0
    total_active_versions: int = 0
    code_update_warnings: int = 0
    last_sync_at: Optional[datetime] = None


class SyncReport(BaseModel):
    """Registry 同步报告"""
    new_nodes: list[str] = Field(default_factory=list)
    updated_nodes: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)


# ══════════════════════════════════════════════
# 批量校验与发布
# ══════════════════════════════════════════════

class BatchScopeRequest(BaseModel):
    """批量操作请求 -- 指定范围与发布策略"""
    scope: str = Field(
        ..., description="flow(当前流程) / all_pending(全部待处理节点)"
    )
    flow_key: Optional[str] = Field(
        default=None, description="scope=flow 时必填"
    )
    include_technical: bool = Field(
        default=False, description="是否包含 visibility=technical 的节点"
    )
    change_note: Optional[str] = None


class BatchPublishPassedRequest(BaseModel):
    """发布已通过节点 -- 传入上次批量校验结果中的通过节点列表"""
    node_keys: list[str] = Field(
        ..., min_length=1, description="已通过校验的 node_key 列表"
    )
    change_note: Optional[str] = None


class BatchRunItemCheck(BaseModel):
    """单节点在批量运行中的校验检查项"""
    check_type: str
    passed: bool
    message: str
    details: Optional[dict] = None


class BatchRunItem(BaseModel):
    """批量运行中每个节点的结果"""
    node_key: str
    node_name: str
    flow_key: str
    flow_name: str
    stage_key: str
    stage_name: str
    path_text: str = Field(description="流程 > 阶段 > 节点 的可读链路")
    status: str = Field(
        description="validated / validation_failed / published / publish_failed / skipped"
    )
    checks: list[BatchRunItemCheck] = Field(default_factory=list)
    downstream_nodes: list[str] = Field(default_factory=list)
    error_message: Optional[str] = None


class BatchRunSummary(BaseModel):
    """批量运行统计摘要"""
    total: int = 0
    validated_passed: int = 0
    validated_failed: int = 0
    published: int = 0
    skipped: int = 0


class BatchRunResponse(BaseModel):
    """批量校验/发布的统一返回结构"""
    scope: str
    flow_key: Optional[str] = None
    summary: BatchRunSummary = Field(default_factory=BatchRunSummary)
    can_publish_all: bool = False
    can_publish_passed: bool = False
    items: list[BatchRunItem] = Field(default_factory=list)
