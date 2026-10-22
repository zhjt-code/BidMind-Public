"""标书编写 Pydantic Schema

用于 LLM 输出校验和 API 数据传输。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator

# ==================== LLM 提取输出 Schema ====================

class ExtractedClause(BaseModel):
    """LLM 提取的单条约束"""
    sub_type: str = Field(..., description="约束子类型")
    content: str = Field(..., min_length=2, description="约束的结构化描述")
    raw_text: Optional[str] = Field(default=None, description="招标原文摘录")
    source_page: Optional[int] = Field(
        default=None,
        description="默认打开页(LLM精确页或批次首页), 仅用于 PDF 跳转锚点",
    )
    source_page_start: Optional[int] = Field(
        default=None, description="条款所属批次起始页(页范围下界)"
    )
    source_page_end: Optional[int] = Field(
        default=None, description="条款所属批次结束页(页范围上界)"
    )
    score_weight: Optional[float] = Field(default=None, description="评分权重(仅scoring_criterion)")
    metadata: Optional[dict[str, Any]] = Field(
        default=None,
        description="面向下游消费的结构化补充信息，如评分规则、材料类型、适用章节等",
    )

    @field_validator("raw_text", mode="before")
    @classmethod
    def coerce_raw_text(cls, v: object) -> Optional[str]:
        """空字符串转 None"""
        if isinstance(v, str) and not v.strip():
            return None
        return v


class ClauseMergeDecision(BaseModel):
    """LLM 合并决策（决策模式输出）"""
    merge_ids: list[int] = Field(..., min_length=2, description="待合并条目的索引列表")
    content: str = Field(..., min_length=2, description="合并后的约束描述")
    sub_type: str = Field(..., description="合并后的子类型")
    metadata: Optional[dict[str, Any]] = Field(
        default=None,
        description="合并后的结构化补充信息；为空时沿用信息最完整的原条目",
    )


class BidStructureAnalysis(BaseModel):
    """标书结构分析结果（LLM输出）

    由 ClauseExtractor.analyze_bid_structure() 在约束提取尾部调用，
    判断招标文件要求的投标文件分卷结构。
    """
    structure_mode: str = Field(
        ...,
        description="tech_only / comm_only / tech_comm / qual_tech_comm / unified",
    )
    has_technical: bool = Field(default=True)
    has_commercial: bool = Field(default=True)
    has_qualification: bool = Field(default=False)
    technical_sections: list[str] = Field(default_factory=list)
    commercial_sections: list[str] = Field(default_factory=list)
    qualification_sections: list[str] = Field(default_factory=list)
    fixed_prefix_items: list[str] = Field(default_factory=list)
    appendix_items: list[str] = Field(default_factory=list)
    reasoning: str = Field(default="", description="判定依据简述")

    @field_validator("structure_mode", mode="before")
    @classmethod
    def validate_structure_mode(cls, v: object) -> str:
        allowed = {
            "tech_only", "comm_only", "tech_comm",
            "qual_tech_comm", "unified",
        }
        if isinstance(v, str) and v in allowed:
            return v
        return "tech_comm"

    @field_validator("technical_sections", "commercial_sections",
                     "qualification_sections", "fixed_prefix_items",
                     "appendix_items", mode="before")
    @classmethod
    def coerce_list(cls, v: object) -> list:
        if v is None:
            return []
        return v if isinstance(v, list) else []


# ==================== 目录原文提取 Schema ====================

class BidDirectoryItem(BaseModel):
    """招标文件指定的目录条目"""
    order: int = Field(..., description="条目序号(1基)")
    title: str = Field(..., min_length=1, description="条目标题(原文)")
    node_type: str = Field(default="free_writing", description="节点类型初步建议")
    node_type_source: str = Field(
        default="llm_suggestion",
        description="节点类型来源: llm_suggestion / rule / fallback",
    )
    source_span: Optional[str] = Field(
        default=None,
        description="目录条目在招标文件中的来源范围描述，如页码范围或章节名",
    )
    display_no: Optional[str] = Field(
        default=None,
        description="原文编号，如一、/1-1/2-1-1",
    )
    source_verified: Optional[bool] = Field(
        default=None,
        description="标题是否在原文中核验到",
    )
    children: list["BidDirectoryItem"] = Field(default_factory=list)

    @field_validator("order", mode="before")
    @classmethod
    def normalize_order(cls, v: object) -> int:
        """LLM 可能输出 0 基序号，统一归一化为 >= 1"""
        if isinstance(v, (int, float)) and int(v) >= 1:
            return int(v)
        return 1

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, v: object) -> object:
        """: 标题类字段先 strip，空白由 min_length 拒绝。"""
        if isinstance(v, str):
            return v.strip()
        return v

    @field_validator("node_type", mode="before")
    @classmethod
    def validate_node_type(cls, v: object) -> str:
        allowed = {"fixed_template", "data_fill", "free_writing"}
        if isinstance(v, str) and v in allowed:
            return v
        return "free_writing"

    @field_validator("node_type_source", mode="before")
    @classmethod
    def validate_node_type_source(cls, v: object) -> str:
        allowed = {"llm_suggestion", "rule", "fallback"}
        if isinstance(v, str) and v in allowed:
            return v
        return "llm_suggestion"

    @field_validator("children", mode="before")
    @classmethod
    def coerce_children(cls, v: object) -> list:
        return v if isinstance(v, list) else []


class BidVolumeSpec(BaseModel):
    """单卷目录规格"""
    volume_type: str = Field(
        ...,
        description="qualification / pricing / technical / commercial / other",
    )
    volume_title: str = Field(..., description="卷名(如'资格及资信证明部分')")
    items: list[BidDirectoryItem] = Field(default_factory=list)

    @field_validator("volume_type", mode="before")
    @classmethod
    def validate_volume_type(cls, v: object) -> str:
        allowed = {
            "qualification", "pricing", "technical", "commercial", "other",
        }
        if isinstance(v, str) and v in allowed:
            return v
        return "other"

    @field_validator("volume_title", mode="before")
    @classmethod
    def normalize_volume_title(cls, v: object) -> str:
        """: 卷名 strip；空串保留，照搬层省略卷根而非整任务崩溃。"""
        if v is None:
            return ""
        if isinstance(v, str):
            return v.strip()
        return str(v).strip()

    @field_validator("items", mode="before")
    @classmethod
    def coerce_items(cls, v: object) -> list:
        return v if isinstance(v, list) else []


class BidDirectorySpec(BaseModel):
    """招标文件指定的投标文件目录结构

    由 ClauseExtractor.extract_bid_directory() 在约束提取尾部调用，
    识别招标文件中是否存在明确的投标文件目录格式要求。
    """
    has_explicit_directory: bool = Field(
        ..., description="招标文件中是否存在明确的目录格式要求"
    )
    volumes: list[BidVolumeSpec] = Field(default_factory=list)
    reasoning: str = Field(default="", description="判定依据简述")

    @field_validator("volumes", mode="before")
    @classmethod
    def coerce_volumes(cls, v: object) -> list:
        return v if isinstance(v, list) else []


class RenderProfileParsed(BaseModel):
    """LLM 解析的渲染配置

    line_spacing 单位为**倍数**（如 1.5），不是磅值。
    合理范围 1.0~3.0，超出范围会在入库前被 normalize_line_spacing() 回退为默认值。
    """
    font_body: Optional[str] = None
    font_heading: Optional[str] = None
    font_size_body: Optional[float] = None
    font_size_h1: Optional[float] = None
    font_size_h2: Optional[float] = None
    font_size_h3: Optional[float] = None
    line_spacing: Optional[float] = None
    margin_top: Optional[float] = None
    margin_bottom: Optional[float] = None
    margin_left: Optional[float] = None
    margin_right: Optional[float] = None
    header_text: Optional[str] = None
    footer_text: Optional[str] = None
    page_number_position: Optional[str] = None
    submission_requirements: Optional[dict[str, Any]] = Field(
        default=None,
        description="不可直接由 Word 渲染完成的提交/装订/签章/暗标等格式风险要求",
    )


# ==================== API 响应 Schema ====================

class WritingClauseResponse(BaseModel):
    """约束条目 API 响应"""
    id: int
    project_id: int
    document_id: int
    layer: str
    sub_type: str
    content: str
    raw_text: Optional[str] = None
    source_page: Optional[int] = None
    source_page_start: Optional[int] = None
    source_page_end: Optional[int] = None
    score_weight: Optional[float] = None
    metadata_json: Optional[dict[str, Any]] = None
    confirmed: int
    is_manual: int = 0
    sort_order: int

    class Config:
        from_attributes = True


class WritingClauseCreate(BaseModel):
    """约束条目创建请求"""
    layer: str = Field(..., description="约束层级")
    sub_type: str = Field(..., min_length=1, max_length=50, description="约束子类型")
    content: str = Field(..., min_length=1, description="约束内容")
    raw_text: Optional[str] = Field(default=None, description="来源原文")
    source_page: Optional[int] = Field(default=None, ge=1, description="来源页码(可选; 留空则由 start/end 推导)")
    source_page_start: Optional[int] = Field(
        default=None, ge=1, description="来源页范围下界(可选)"
    )
    source_page_end: Optional[int] = Field(
        default=None, ge=1, description="来源页范围上界(可选)"
    )
    score_weight: Optional[float] = Field(default=None, ge=0, description="评分权重")
    metadata_json: Optional[dict[str, Any]] = Field(
        default=None, description="结构化补充信息(JSON)"
    )
    insert_after_id: Optional[int] = Field(default=None, ge=1, description="插入到某条约束之后")

    @field_validator("layer", mode="before")
    @classmethod
    def validate_layer(cls, v: object) -> str:
        allowed = {"hard_constraint", "structure_constraint", "generation_guide"}
        if isinstance(v, str) and v in allowed:
            return v
        raise ValueError("invalid writing clause layer")

    @field_validator("sub_type", mode="before")
    @classmethod
    def validate_sub_type(cls, v: object) -> str:
        allowed = {
            "disqualification", "fixed_template", "format_rule", "submission_rule",
            "submission_notice", "directory_rule", "material_required",
            "content_required", "table_format", "scoring_criterion",
            "project_context", "emphasis_point", "differentiation_hint",
            "industry_profile",
        }
        if not isinstance(v, str):
            raise ValueError("invalid writing clause sub_type")
        normalized = v.strip()
        if normalized in allowed:
            return normalized
        raise ValueError("invalid writing clause sub_type")

    @field_validator("content", mode="before")
    @classmethod
    def validate_content(cls, v: object) -> str:
        if not isinstance(v, str):
            raise ValueError("invalid writing clause content")
        normalized = v.strip()
        if not normalized:
            raise ValueError("invalid writing clause content")
        return normalized

    @field_validator("raw_text", mode="before")
    @classmethod
    def coerce_create_raw_text(cls, v: object) -> Optional[str]:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @model_validator(mode="after")
    def normalize_page_range(self) -> "WritingClauseCreate":
        """统一来源页字段边界，兼容旧调用方的单页/页范围写法。"""
        # 边界一致性: start/end 若只给一个, 补齐另一个; source_page 若缺失, 用 start 兜底
        if self.source_page_start is not None and self.source_page_end is None:
            self.source_page_end = self.source_page_start
        if self.source_page_end is not None and self.source_page_start is None:
            self.source_page_start = self.source_page_end
        if (
            self.source_page_start is not None
            and self.source_page_end is not None
            and self.source_page_start > self.source_page_end
        ):
            raise ValueError("source_page_start 不能大于 source_page_end")
        if self.source_page is None and self.source_page_start is not None:
            self.source_page = self.source_page_start
        if (
            self.source_page is not None
            and self.source_page_start is None
            and self.source_page_end is None
        ):
            self.source_page_start = self.source_page
            self.source_page_end = self.source_page
        return self


class WritingClauseUpdate(BaseModel):
    """约束条目编辑请求"""
    content: Optional[str] = None
    sub_type: Optional[str] = None
    score_weight: Optional[float] = None
    metadata_json: Optional[dict[str, Any]] = Field(
        default=None, description="结构化补充信息(JSON)"
    )
    sort_order: Optional[int] = None
    raw_text: Optional[str] = Field(default=None, description="补录招标原文摘录")
    source_page: Optional[int] = Field(
        default=None, ge=1, description="补录来源页码(默认打开页)"
    )
    source_page_start: Optional[int] = Field(
        default=None, ge=1, description="补录来源页范围下界"
    )
    source_page_end: Optional[int] = Field(
        default=None, ge=1, description="补录来源页范围上界"
    )

    @field_validator("raw_text", mode="before")
    @classmethod
    def coerce_update_raw_text(cls, v: object) -> Optional[str]:
        """空字符串归一化为 None，避免穿透到 DB"""
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @model_validator(mode="after")
    def ensure_page_range_consistency(self) -> "WritingClauseUpdate":
        """page_range 边界一致性"""
        if self.source_page_start is not None and self.source_page_end is not None:
            if self.source_page_start > self.source_page_end:
                raise ValueError("source_page_start 不能大于 source_page_end")
        return self


class WritingClauseConfirmRequest(BaseModel):
    """批量确认请求"""
    clause_ids: list[int] = Field(..., min_length=1)


class WritingProjectStepStatus(BaseModel):
    """标书编写单步进度状态"""

    label: str = Field(..., description="步骤名称")
    status: Literal["waiting", "running", "completed", "failed"] = Field(
        ..., description="步骤状态"
    )
    progress: int = Field(default=0, ge=0, le=100, description="步骤进度百分比")
    detail: Optional[str] = Field(default=None, description="步骤详细说明")


class WritingProjectStatusResponse(BaseModel):
    """标书编写项目状态响应（统一进度契约）"""

    id: int = Field(..., description="项目ID")
    name: str = Field(..., description="项目名称")
    status: str = Field(..., description="项目状态")
    error_message: Optional[str] = Field(default=None, description="失败原因")
    overall_status: Literal["idle", "running", "completed", "failed"] = Field(
        default="idle", description="统一整体状态"
    )
    overall_progress: int = Field(
        default=0, ge=0, le=100, description="统一整体进度百分比"
    )
    steps: list[WritingProjectStepStatus] = Field(
        default_factory=list, description="当前活动任务的步骤进度"
    )
    message: str = Field(default="", description="当前任务的人类可读说明")
    is_recovering: bool = Field(default=False, description="是否正在自动恢复")
    recovery_count: int = Field(default=0, description="自动恢复次数")
    current_stage: str = Field(default="", description="兼容旧版页面的当前阶段字段")
    current_detail: str = Field(default="", description="兼容旧版页面的当前详情字段")


class WritingRetryStateResponse(BaseModel):
    """显式重试状态响应。"""

    retryable: bool = Field(default=False, description="当前失败态是否允许显式重试")
    failed_node: Optional[str] = Field(default=None, description="失败节点标识")
    target_id: Optional[int] = Field(default=None, description="细粒度重试目标ID，如章节 node_id")
    failed_stage: Optional[str] = Field(default=None, description="节点内部失败阶段")
    retry_action_label: str = Field(default="", description="重试按钮文案")
    preserved_outputs: list[str] = Field(default_factory=list, description="重试会保留的产物")
    reset_scope: list[str] = Field(default_factory=list, description="重试前允许重置的范围")
    last_attempt: int = Field(default=0, description="最近一次运行 attempt")
    retry_running: bool = Field(default=False, description="显式重试是否正在运行")
    last_error: Optional[str] = Field(default=None, description="最近错误摘要")


class WritingRetryRequest(BaseModel):
    """显式重试请求。"""

    node_key: str = Field(..., description="重试节点标识")
    target_id: Optional[int] = Field(default=None, description="细粒度重试目标ID")


class WritingRetryTriggerResponse(WritingRetryStateResponse):
    """显式重试触发响应。"""

    task_name: Optional[str] = Field(default=None, description="后台任务名")
    queue_mode: str = Field(default="", description="入队模式")
    message: str = Field(default="", description="触发结果说明")


# ==================== 目录树 Schema ====================

class OutlineNodeGenerated(BaseModel):
    """LLM 生成的单个目录节点（含 children 递归）"""
    title: str = Field(..., min_length=1, description="章节标题")
    depth: int = Field(default=0, ge=0, description="嵌套深度")
    sort_order: int = Field(default=0, ge=0, description="同级排序序号")
    node_type: str = Field(..., description="节点类型: fixed_template/data_fill/free_writing")
    is_required: int = Field(default=1, description="是否必填: 0/1")
    score_weight: Optional[float] = Field(default=None, description="评分权重")
    target_word_count: Optional[int] = Field(default=None, description="建议字数")
    children: list["OutlineNodeGenerated"] = Field(default_factory=list)
    is_skeleton: bool = Field(default=False, description="是否为骨架节点(招标文件指定,不可删除)")
    volume_type: Optional[str] = Field(default=None, description="所属分卷: technical/commercial")

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, v: object) -> object:
        """: 标题先 strip，空白由 min_length 拒绝（_parse_nodes 软跳过）。"""
        if isinstance(v, str):
            return v.strip()
        return v

    @field_validator("node_type", mode="before")
    @classmethod
    def validate_node_type(cls, v: object) -> str:
        allowed = {"fixed_template", "data_fill", "free_writing"}
        if isinstance(v, str) and v in allowed:
            return v
        return "free_writing"

    @field_validator("target_word_count", mode="before")
    @classmethod
    def validate_target_word_count(cls, v: object) -> Optional[int]:
        if v in (None, ""):
            return None
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str):
            text = v.strip()
            if not text:
                return None
            if text.isdigit():
                return int(text)
        return None


class OutlineNodeResponse(BaseModel):
    """目录节点 API 响应"""
    id: int
    project_id: int
    parent_id: Optional[int] = None
    title: str
    depth: int
    sort_order: int
    node_type: str
    is_required: int
    score_weight: Optional[float] = None
    target_word_count: Optional[int] = None
    status: str
    volume_type: Optional[str] = None
    is_skeleton: int = 0
    chapter_profile: Optional[str] = None

    class Config:
        from_attributes = True


class OutlineNodeUpdate(BaseModel):
    """目录节点编辑请求"""
    title: Optional[str] = None
    node_type: Optional[str] = None
    is_required: Optional[int] = None
    score_weight: Optional[float] = None
    target_word_count: Optional[int] = Field(default=None, ge=100, le=5000)


class OutlineNodeCreate(BaseModel):
    """目录节点创建请求"""
    parent_id: Optional[int] = None
    insert_after_id: Optional[int] = Field(default=None, description="同级插入锚点节点ID")
    insert_position: Literal["first", "last", "after"] = Field(
        default="last", description="同父级插入位置"
    )
    title: str = Field(..., min_length=1, max_length=500)
    node_type: str = "free_writing"
    is_required: int = 1
    score_weight: Optional[float] = None
    target_word_count: Optional[int] = Field(default=None, ge=100, le=5000)


class OutlineReorderRequest(BaseModel):
    """目录拖拽排序请求"""
    node_ids: list[int] = Field(..., min_length=1, description="按新顺序排列的节点ID列表")
    parent_id: Optional[int] = Field(default=None, description="目标父节点ID")


class MaterialChecklistItem(BaseModel):
    """素材缺失清单条目"""
    clause_id: int
    requirement: str
    sub_requirements: list[str] = Field(
        default_factory=list,
        description="嵌套的子需求列表"
    )
    category: str = Field(
        default="material",
        description="material=需上传素材, notice=注意事项"
    )
    status: str = Field(..., description="fulfilled / missing")
    matched_material_id: Optional[int] = None


# ==================== 素材 Schema ====================

class WritingMaterialImageAssetResponse(BaseModel):
    """素材页图片资产摘要"""

    id: int
    caption: Optional[str] = None
    image_type: Optional[str] = None
    business_subtype: Optional[str] = None
    review_status: str = "validated"


class WritingMaterialResponse(BaseModel):
    """素材 API 响应"""
    id: int
    project_id: int
    document_id: Optional[int] = None
    material_type: str
    title: str
    display_title: Optional[str] = None
    effective_title: str
    content: Optional[str] = None
    file_path: Optional[str] = None
    metadata_tags: Optional[str] = None
    source_page: Optional[int] = None
    parent_material_id: Optional[int] = None
    source_page_start: Optional[int] = None
    source_page_end: Optional[int] = None
    split_confidence: Optional[float] = None
    material_role: str = "normal"
    split_review_status: str = "none"
    structure_hint_label: Optional[str] = None
    image_count: int = 0
    validated_image_count: int = 0
    review_image_count: int = 0
    image_assets: list[WritingMaterialImageAssetResponse] = Field(default_factory=list)
    material_quality_flags: list[str] = Field(
        default_factory=list,
        description="素材质量标记，如 llm_failed/image_skipped/no_chunks/label_conflict",
    )
    material_quality_summary: Optional[str] = Field(
        default=None,
        description="素材质量摘要，供素材页/匹配页提示风险",
    )
    processing_status: str = "completed"
    kb_source_id: Optional[int] = None
    kb_atom_id: Optional[int] = None
    kb_source_version: Optional[str] = None
    kb_snapshot_at: Optional[datetime] = None
    kb_source_chunk_ids: Optional[str] = None

    class Config:
        from_attributes = True


class WritingMaterialUpdateRequest(BaseModel):
    """素材拆分状态更新请求"""

    action: str = Field(
        ...,
        description="use_whole/use_split/ignore_child/restore_child",
        pattern="^(use_whole|use_split|ignore_child|restore_child)$",
    )


class WritingMaterialTitleUpdateRequest(BaseModel):
    """素材标题更新请求"""

    display_title: str = Field(..., min_length=1, max_length=500)


class WritingMaterialTitleBatchUpdateItem(BaseModel):
    """单条素材标题批量更新项"""

    material_id: int = Field(..., ge=1)
    display_title: str = Field(..., min_length=1, max_length=500)


class WritingMaterialTitleBatchUpdateRequest(BaseModel):
    """素材标题批量更新请求"""

    items: list[WritingMaterialTitleBatchUpdateItem] = Field(..., min_length=1)


class WritingMaterialTitleBatchUpdateResponse(BaseModel):
    """素材标题批量更新响应"""

    updated_count: int = 0
    updated_material_ids: list[int] = Field(default_factory=list)
    requires_rematch: bool = False


class MaterialBatchStatusResponse(BaseModel):
    """素材批处理状态响应

    前端轮询此接口获取批处理进度。

    状态契约（与 WritingService.get_batch_process_status 对齐）：
    - phase 为 "idle" / "done" / "failed" 之一才代表静默/终态；
      任务仍活跃或心跳非终态时，phase 必须反映运行中的阶段（parsing / vl_recognition /
      indexing / image_classify / tagging）。前端不得把 idle 视为完成态。
    - task_active 表示任务在 ARQ/asyncio 中仍存活或处于入队宽限期；
    - heartbeat_phase 是后端原始心跳阶段，供前端做进度细化展示。
    """
    total: int = Field(description="待处理素材总数")
    completed: int = Field(default=0, description="已完成数")
    failed: int = Field(default=0, description="失败数")
    processing: int = Field(default=0, description="处理中数")
    phase: str = Field(
        default="idle",
        description="前端感知阶段: idle/parsing/vl_recognition/indexing/image_classify/tagging/done/failed",
    )
    vl_progress: str = Field(
        default="",
        description="VL识别进度,格式如 '120/385'",
    )
    task_active: bool = Field(
        default=False,
        description="后台任务是否仍活跃（ARQ/asyncio 存活或入队宽限期内）",
    )
    heartbeat_phase: Optional[str] = Field(
        default=None,
        description="后端原始心跳阶段（含 label_resolve 等内部阶段），前端仅做展示细化",
    )
    retryable: bool = Field(default=False, description="当前失败态是否允许显式重试")
    failed_node: Optional[str] = Field(default=None, description="失败节点标识")
    target_id: Optional[int] = Field(default=None, description="细粒度重试目标ID")
    failed_stage: Optional[str] = Field(default=None, description="节点内部失败阶段")
    retry_action_label: str = Field(default="", description="重试按钮文案")
    preserved_outputs: list[str] = Field(default_factory=list, description="重试会保留的产物")
    reset_scope: list[str] = Field(default_factory=list, description="重试前允许重置的范围")
    last_attempt: int = Field(default=0, description="最近一次运行 attempt")
    retry_running: bool = Field(default=False, description="显式重试是否正在运行")
    last_error: Optional[str] = Field(default=None, description="最近错误摘要")


# ==================== 绑定 Schema ====================

class WritingBindingResponse(BaseModel):
    """绑定 API 响应"""
    id: int
    project_id: int
    outline_node_id: Optional[int] = None
    clause_id: Optional[int] = None
    material_id: Optional[int] = None
    binding_type: str
    confidence: float
    source: str
    status: str
    reason: Optional[str] = None
    scope: Optional[str] = None

    class Config:
        from_attributes = True


class WritingBindingUpdate(BaseModel):
    """绑定调整请求"""
    status: Optional[str] = Field(default=None, description="suggested/confirmed/rejected")
    confidence: Optional[float] = None
    outline_node_id: Optional[int] = Field(default=None, description="分配到目标章节ID(孤儿约束手动分配)")
    scope: Optional[str] = Field(default=None, description="作用域(分配后清除orphan标记)")
    reason: Optional[str] = Field(default=None, description="人工操作原因/备注")


class WritingBindingConfirmRequest(BaseModel):
    """批量确认绑定请求"""
    binding_ids: list[int] = Field(..., min_length=1)


# ==================== 草稿 Schema ====================

class SectionDraftResponse(BaseModel):
    """章节草稿 API 响应"""
    id: int
    outline_node_id: int
    version: int
    content: Optional[str] = None
    content_format: str = "html"
    summary: Optional[str] = None
    source_clause_ids: list[int] = Field(default_factory=list)
    source_material_ids: list[int] = Field(default_factory=list)
    is_current: int
    is_user_edited: int
    is_locked: int
    word_count: Optional[int] = None
    status: str

    # B1: 失败合同字段（默认不返回）
    failure_state: Optional[str] = None
    failure_reason: Optional[str] = None
    confidence: Optional[float] = None
    gate_trace: Optional[Any] = None
    review_required: Optional[int] = None
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[int] = None

    # P2.1b: 单章级 Gate 信号 (素材未引用 / 表格填充率 / AI 套话等)
    gate_flags: Optional[dict[str, Any]] = None
    section_plan_id: Optional[int] = None
    planning_mode: Optional[str] = None
    planning_failure_code: Optional[str] = None

    @field_validator("content", mode="before")
    @classmethod
    def strip_html_markdown_fence(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        from app.core.writer.section_structure import strip_markdown_html_fence

        return strip_markdown_html_fence(value.strip())

    class Config:
        from_attributes = True

    @field_validator("source_clause_ids", "source_material_ids", mode="before")
    @classmethod
    def parse_source_clause_ids(cls, v: object) -> list[int]:
        """将数据库 JSON 字符串转换为 API 数组（source_clause_ids / source_material_ids 共用）。"""
        if v in (None, ""):
            return []
        if isinstance(v, list):
            raw_items = v
        elif isinstance(v, str):
            try:
                parsed = json.loads(v)
            except json.JSONDecodeError:
                return []
            raw_items = parsed if isinstance(parsed, list) else []
        else:
            return []

        ids: list[int] = []
        for item in raw_items:
            try:
                ids.append(int(item))
            except (TypeError, ValueError):
                continue
        return ids

    @model_serializer(mode="wrap")
    def _strip_failure_state_fields(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        from app.core.config import settings
        _B1_FIELDS = ("failure_state", "failure_reason", "confidence", "gate_trace",
                       "review_required", "reviewed_at", "reviewed_by")
        if not settings.FAILURE_STATE_ENABLED:
            for k in _B1_FIELDS:
                data.pop(k, None)
        else:
            for k in _B1_FIELDS:
                if k in data and data[k] is None:
                    del data[k]
        # P2.1b: gate_flags 为 None 时不输出, 保持历史契约 (FAILURE_STATE_ENABLED 开关无关)
        if "gate_flags" in data and data["gate_flags"] is None:
            del data["gate_flags"]
        for k in ("section_plan_id", "planning_mode", "planning_failure_code"):
            if k in data and data[k] is None:
                del data[k]
        return data


class SectionDraftUpdate(BaseModel):
    """用户编辑草稿请求"""
    content: str = Field(..., min_length=1)


class ConsistencyIssue(BaseModel):
    """一致性检查问题"""
    type: str = Field(..., description="问题类型")
    severity: str = Field(..., description="严重程度: high/medium/low")
    node_id: Optional[int] = None
    message: str
    detail: str = ""


class ClauseCoverageSample(BaseModel):
    """未覆盖约束示例(供前端展开明细/tooltip)"""
    id: int
    summary: str


class ClauseCoverageReasonGroup(BaseModel):
    """单一未覆盖原因的聚合"""
    count: int = 0
    samples: list[ClauseCoverageSample] = Field(default_factory=list)


class ClauseCoverageResponse(BaseModel):
    """约束消费覆盖诊断响应

    counted: 计入覆盖分母的已确认约束数(排除 render_profile/submission_* 等旁路类型);
    covered/uncovered: 已/未进入任一当前草稿 source_clause_ids 的数量;
    pending_assignment/unmatched/pending_generation: 对未覆盖约束按可行动原因分类。
    """
    counted: int
    covered: int
    uncovered: int
    pending_assignment: ClauseCoverageReasonGroup
    unmatched: ClauseCoverageReasonGroup
    pending_generation: ClauseCoverageReasonGroup
