"""招标要求 Pydantic 模型

定义招标要求提取的输入输出数据结构。
"""

from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator


# 要求分类类型
RequirementCategoryType = Literal["QUAL", "TECH", "COMM", "PERF", "STAFF", "FORMAT"]

# 评审意义分级（P1）：
# - actionable: 文本必须响应类，进入审查打分（默认）
# - informational: 信息告知/合同文本类，确认页默认折叠，不进审查打分
# - procedural: 程序/递交/履约过程类，确认页默认折叠，只作提醒
ReviewRelevanceType = Literal["actionable", "informational", "procedural"]


class ExtractedRequirement(BaseModel):
    """LLM 提取的单条招标要求
    
    这是 LLM 输出的原始格式，用于校验 LLM 响应。
    """
    category: RequirementCategoryType = Field(
        ...,
        description="要求分类：QUAL/TECH/COMM/PERF/STAFF/FORMAT"
    )
    description: str = Field(
        ...,
        min_length=5,
        description="要求的完整描述"
    )
    is_mandatory: bool = Field(
        ...,
        description="是否为否决项（必须满足）"
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="关键词列表"
    )
    source_pages: list[int] = Field(
        default_factory=list,
        description="来源页码列表"
    )
    merged_count: Optional[int] = Field(
        default=None,
        description="合并数量"
    )
    sub_requirements: list[str] = Field(
        default_factory=list,
        description="子要求列表"
    )
    topic: Optional[str] = Field(
        default=None,
        description="主题标记"
    )
    subject_scope: Optional[str] = Field(
        default=None,
        description="主体范围: enterprise/personnel/both/generic"
    )
    canonical_key: Optional[str] = Field(
        default=None,
        description="事实键（不含主体维度）"
    )
    main_requirement_id: Optional[int] = Field(
        default=None,
        description="主判定要求ID"
    )
    review_relevance: ReviewRelevanceType = Field(
        default="actionable",
        description="评审意义分级: actionable/informational/procedural"
    )
    relevance_reason: Optional[str] = Field(
        default=None,
        description="评审意义归因: llm_locus/rule_floor/contract_section/behavioral_mandatory/manual"
    )
    requirement_kind: str = Field(
        default="normal",
        description="要求种类: normal/scoring_item"
    )
    score_weight: Optional[float] = Field(
        default=None,
        description="评分项满分(含分值正文条款由判定 pass 解析)"
    )
    is_key_review: bool = Field(
        default=False,
        description="是否重点评审项(含分值条款=True，触发重点评审+预估得分)"
    )
    scoring_metadata: Optional[dict] = Field(
        default=None,
        description="评分项结构化元数据(max_score/score_rules/deduction_rules/source 等)"
    )

    @field_validator("keywords", "source_pages", "sub_requirements", mode="before")
    @classmethod
    def coerce_list_fields(cls, v):
        return v if isinstance(v, list) else []

    @field_validator("review_relevance", mode="before")
    @classmethod
    def coerce_review_relevance(cls, v):
        if isinstance(v, str) and v in ("actionable", "informational", "procedural"):
            return v
        return "actionable"


class ExtractedRequirementList(BaseModel):
    """LLM 提取的招标要求列表
    
    用于批量校验 LLM 输出的招标要求。
    """
    requirements: list[ExtractedRequirement] = Field(
        default_factory=list,
        description="提取的招标要求列表"
    )

    @field_validator("requirements", mode="before")
    @classmethod
    def coerce_requirements(cls, v):
        return v if isinstance(v, list) else []


class ExtractedScoringItem(BaseModel):
    """LLM 提取的单条评分项（评标办法/评分表，P2）。"""
    category: RequirementCategoryType = Field(
        ..., description="所属分类: QUAL/TECH/COMM/PERF/STAFF/FORMAT"
    )
    category_basis: Optional[str] = Field(
        default=None,
        description="分类依据(P3-1): 确定性路径=命中关键词, llm/manual=来源标注; None=零命中未分类",
    )
    score_item_name: str = Field(..., min_length=1, description="评分项名称")
    description: str = Field(..., min_length=2, description="评分项/评分标准描述")
    score_weight: float = Field(..., ge=0, description="该评分项满分分值")
    volume: Optional[str] = Field(
        default=None, description="所属卷: technical/commercial/price/qualification 等"
    )
    volume_label: Optional[str] = Field(
        default=None, description="所属卷原文标签: 如 商务标/技术标/资信标"
    )
    lot: Optional[str] = Field(
        default=None, description="所属采购包/标段: 如 采购包1/采购包2"
    )
    scheme: Optional[str] = Field(
        default=None,
        description="所属评分方案: 同文档多套独立百分制时区分, 如 scheme_1/scheme_2",
    )
    fact_type: str = Field(
        default="base_score",
        description="评分事实类型(阶段3): base_score基础分/bonus加分/penalty扣分/price_formula价格公式",
    )
    volume_weight: Optional[float] = Field(
        default=None, ge=0, le=1,
        description="所属卷权重(0-1, 如技术40%→0.4); 来自卷定义行解析或LLM, 缺失则不参与加权折算",
    )
    volume_max: Optional[float] = Field(
        default=None, ge=0, description="所属卷满分基准(如技术卷总分100), 供按卷归一与校验",
    )
    score_rules: list[str] = Field(default_factory=list, description="得分/分档规则")
    deduction_rules: list[str] = Field(default_factory=list, description="扣分/不得分规则")
    source_pages: list[int] = Field(default_factory=list, description="来源页码")

    @field_validator("score_rules", "deduction_rules", "source_pages", mode="before")
    @classmethod
    def coerce_scoring_lists(cls, v):
        return v if isinstance(v, list) else []


class RequirementCreate(BaseModel):
    """创建招标要求的请求模型"""
    category: RequirementCategoryType = Field(
        ...,
        description="要求分类"
    )
    description: str = Field(
        ...,
        min_length=5,
        description="要求描述"
    )
    is_mandatory: bool = Field(
        default=False,
        description="是否为否决项"
    )
    source_page: Optional[int] = Field(
        default=None,
        description="来源页码"
    )
    source_pages: list[int] = Field(default_factory=list, description="来源页码列表")
    keywords: list[str] = Field(default_factory=list, description="关键词列表")
    sub_requirements: list[str] = Field(default_factory=list, description="子要求列表")
    merged_count: Optional[int] = Field(default=None, description="合并数量")
    topic: Optional[str] = Field(default=None, description="主题标记")
    subject_scope: Optional[str] = Field(default=None, description="主体范围")
    canonical_key: Optional[str] = Field(default=None, description="事实键（不含主体维度）")
    main_requirement_id: Optional[int] = Field(default=None, description="主判定要求ID")
    review_relevance: ReviewRelevanceType = Field(default="actionable", description="评审意义分级")

    @field_validator("source_pages", "keywords", "sub_requirements", mode="before")
    @classmethod
    def coerce_create_lists(cls, v):
        return v if isinstance(v, list) else []


class ManualScoringItemCreate(BaseModel):
    """手动补录评分项的请求模型"""
    category: RequirementCategoryType = Field(..., description="要求分类")
    score_item_name: str = Field(..., min_length=1, description="评分项名称")
    description: str = Field(..., min_length=2, description="评分项/评分标准描述")
    score_weight: float = Field(..., gt=0, description="该评分项满分分值")
    score_rules: list[str] = Field(default_factory=list, description="得分/分档规则")
    deduction_rules: list[str] = Field(default_factory=list, description="扣分/不得分规则")
    volume: Optional[str] = Field(default=None, description="所属卷标识")
    volume_label: Optional[str] = Field(default=None, description="所属卷原文标签")
    lot: Optional[str] = Field(default=None, description="所属采购包/标段")
    source_pages: list[int] = Field(default_factory=list, description="来源页码列表")

    @field_validator("score_rules", "deduction_rules", "source_pages", mode="before")
    @classmethod
    def coerce_manual_scoring_lists(cls, v):
        return v if isinstance(v, list) else []


class RequirementUpdate(BaseModel):
    """更新招标要求的请求模型（人工确认时使用）"""
    category: Optional[RequirementCategoryType] = Field(
        default=None,
        description="要求分类"
    )
    description: Optional[str] = Field(
        default=None,
        min_length=5,
        description="要求描述"
    )
    is_mandatory: Optional[bool] = Field(
        default=None,
        description="是否为否决项"
    )
    confirmed: Optional[bool] = Field(
        default=None,
        description="是否已确认"
    )
    source_pages: Optional[list[int]] = Field(default=None, description="来源页码列表")
    keywords: Optional[list[str]] = Field(default=None, description="关键词列表")
    sub_requirements: Optional[list[str]] = Field(default=None, description="子要求列表")
    merged_count: Optional[int] = Field(default=None, description="合并数量")
    topic: Optional[str] = Field(default=None, description="主题标记")
    subject_scope: Optional[str] = Field(default=None, description="主体范围")
    canonical_key: Optional[str] = Field(default=None, description="事实键（不含主体维度）")
    main_requirement_id: Optional[int] = Field(default=None, description="主判定要求ID")
    review_relevance: Optional[ReviewRelevanceType] = Field(default=None, description="评审意义分级（人工升降级）")
    score_weight: Optional[float] = Field(
        default=None,
        gt=0,
        description="评分项满分修正（P2 C1）：仅 scoring_item 使用，更新后 scoring_metadata.max_score 同步",
    )

    @field_validator("source_pages", "keywords", "sub_requirements", mode="before")
    @classmethod
    def coerce_update_lists(cls, v):
        if v is None:
            return None
        return v if isinstance(v, list) else []


class RequirementResponse(BaseModel):
    """招标要求响应模型"""
    id: int = Field(..., description="要求ID")
    project_id: int = Field(..., description="项目ID")
    document_id: int = Field(..., description="来源文档ID")
    category: str = Field(..., description="要求分类")
    description: str = Field(..., description="要求描述")
    is_mandatory: bool = Field(..., description="是否为否决项")
    source_page: Optional[int] = Field(default=None, description="来源页码")
    source_pages: list[int] = Field(default_factory=list, description="来源页码列表")
    keywords: list[str] = Field(default_factory=list, description="关键词列表")
    sub_requirements: list[str] = Field(default_factory=list, description="子要求列表")
    merged_count: Optional[int] = Field(default=None, description="合并数量")
    topic: Optional[str] = Field(default=None, description="主题标记")
    subject_scope: Optional[str] = Field(default=None, description="主体范围")
    canonical_key: Optional[str] = Field(default=None, description="事实键（不含主体维度）")
    main_requirement_id: Optional[int] = Field(default=None, description="主判定要求ID")
    review_relevance: ReviewRelevanceType = Field(default="actionable", description="评审意义分级")
    relevance_reason: Optional[str] = Field(default=None, description="评审意义归因")
    requirement_kind: str = Field(default="normal", description="要求种类: normal/scoring_item")
    score_weight: Optional[float] = Field(default=None, description="评分项满分分值")
    is_key_review: bool = Field(default=False, description="是否重点评审项")
    scoring_metadata: Optional[dict] = Field(default=None, description="评分项结构化元数据")
    confirmed: bool = Field(..., description="是否已人工确认")
    member_ids: list[int] = Field(
        default_factory=list,
        description="展示分组下的全部底层要求ID（折叠复合拆分子面用；确认/删除按此整组操作）",
    )

    @field_validator("source_pages", "keywords", "sub_requirements", mode="before")
    @classmethod
    def coerce_response_lists(cls, v):
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            import json
            try:
                data = json.loads(v)
            except json.JSONDecodeError:
                return []
            return data if isinstance(data, list) else []
        return []
    
    class Config:
        from_attributes = True


class RequirementListResponse(BaseModel):
    """招标要求列表响应模型"""
    total: int = Field(..., description="总数")
    confirmed_count: int = Field(..., description="已确认数量")
    pending_count: int = Field(..., description="待确认数量")
    items: list[RequirementResponse] = Field(
        default_factory=list,
        description="要求列表"
    )
    scoring_totals_report: Optional[dict] = Field(
        default=None,
        description="按卷满分校验报告: 评分项卷满分缺口/权重和异常时供确认页提示人工核对",
    )
    scoring_verdict: Optional[str] = Field(
        default=None,
        description="评分横幅三态权威判定: green/blue/amber; None=无评分数据不渲染。与提取侧VLM救援触发同公式(verdict单源)",
    )
    scoring_ledger_report: Optional[dict] = Field(
        default=None,
        description="账本对账报告(换轴P5-1): 原文组标签轴三态(reconciled/review_required)+基准层级+组级缺口; 无评分全集或无评分chunk为None",
    )


class RequirementConfirmRequest(BaseModel):
    """批量确认招标要求的请求模型"""
    requirement_ids: list[int] = Field(
        ...,
        min_length=1,
        description="要确认的要求ID列表"
    )
