"""审查结果 Pydantic 模型

定义AI审查结果的数据结构。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Literal, Optional

from loguru import logger
from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator

# 符合程度类型
ComplianceLevelType = Literal["fully", "partially", "not", "missing"]

# 风险等级类型
RiskLevelType = Literal["none", "low", "medium", "high"]

# 逐规则判定状态
RuleAssessmentStatusType = Literal["met", "partial", "not", "unknown"]

_VALID_RULE_STATUSES: frozenset[str] = frozenset({"met", "partial", "not", "unknown"})


class RuleAssessmentItem(BaseModel):
    """评分项逐规则判定（单条评分/扣分规则的得分情况）

    Wave2-S5 P1 B1: LLM 输出新增字段，评分项必填，非评分项可 null/[]。
    """

    rule_excerpt: str = Field(..., description="评分规则原文摘录(30字以内)")
    status: RuleAssessmentStatusType = Field(
        ..., description="判定状态: met=满足/partial=部分满足/not=不满足/unknown=无法判定"
    )
    points: Optional[float] = Field(
        default=None, ge=0, description="本规则对应预估得分(可量化时填数值, 否则 null)"
    )
    reason: str = Field(..., description="判定依据说明(简洁)")
    evidence_pages: list[int] = Field(default_factory=list, description="证据页码列表")

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status(cls, v: Any) -> Any:
        """非法 status 字段降级为 unknown，保证审查不因 LLM 幻觉字段失败。"""
        if isinstance(v, str) and v.strip() not in _VALID_RULE_STATUSES:
            return "unknown"
        return v


# Fix-03 / 问题 5：category 缩写 → 全称归一化映射
# 内部统一使用全称（qualification/technical/commercial/performance/staffing/format），
# LLM Prompt 允许输出缩写，validator 在入 schema 时归一。
_CATEGORY_ABBR_TO_FULL: dict[str, str] = {
    "QUAL": "qualification",
    "TECH": "technical",
    "COMM": "commercial",
    "PERF": "performance",
    "STAFF": "staffing",
    "FORMAT": "format",
}


def normalize_category(value: Any) -> Any:
    """把 LLM 输出的类别缩写归一化为内部全称。

    保留非字符串与未知值原样返回，交给下游 Literal/字符串校验自行处理。
    """
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    return _CATEGORY_ABBR_TO_FULL.get(stripped, stripped)


class ExtractedReviewResult(BaseModel):
    """LLM 输出的单项审查结果
    
    这是 LLM 输出的原始格式，用于校验 LLM 响应。
    """
    requirement_id: int = Field(
        ...,
        description="招标要求ID"
    )
    bid_response_id: Optional[int] = Field(
        default=None,
        description="投标响应ID（无响应时为null）"
    )
    category: str = Field(
        ...,
        description="要求分类（统一为全称：qualification/technical/commercial/performance/staffing/format）"
    )

    @field_validator("category", mode="before")
    @classmethod
    def _normalize_category(cls, v: Any) -> Any:
        """Fix-03: LLM 输出 QUAL/TECH/COMM/PERF/STAFF/FORMAT 归一化为全称。"""
        return normalize_category(v)
    requirement_summary: str = Field(
        ...,
        max_length=50,
        description="招标要求摘要"
    )
    response_summary: str = Field(
        ...,
        max_length=100,
        description="投标响应摘要"
    )
    is_compliant: bool = Field(
        ...,
        description="是否符合"
    )
    compliance_level: ComplianceLevelType = Field(
        ...,
        description="符合程度"
    )
    score: int = Field(
        ...,
        ge=0,
        le=100,
        description="评分(0-100)"
    )
    comment: str = Field(
        ...,
        min_length=10,
        description="详细审查意见"
    )
    risk_level: RiskLevelType = Field(
        ...,
        description="风险等级"
    )
    canonical_key: Optional[str] = Field(
        default=None,
        description="跨类别归并键"
    )
    fact_key: Optional[str] = Field(
        default=None,
        description="事实键"
    )
    family_type: Optional[Literal["performance", "audit_finance", "qualification", "generic"]] = Field(
        default=None,
        description="证据族类型"
    )
    attribution_type: Optional[Literal["shared", "independent", "none"]] = Field(
        default=None,
        description="归因关系"
    )
    consistency_flag: Optional[Literal["aligned", "needs_review", "conflicted", "unchecked"]] = Field(
        default=None,
        description="一致性标记"
    )
    main_requirement_id: Optional[int] = Field(
        default=None,
        description="主判定要求ID"
    )
    subject_scope: Optional[Literal["enterprise", "personnel", "both", "generic"]] = Field(
        default=None,
        description="主体范围"
    )
    evidence_relation: Optional[Literal["shared", "independent", "undetermined"]] = Field(
        default=None,
        description="证据关系"
    )
    reconcile_note: Optional[str] = Field(
        default=None,
        description="收敛解释说明"
    )
    source_pages: Optional[str] = Field(
        default=None,
        description="来源页码"
    )
    relevance_score: Optional[int] = Field(
        default=None,
        ge=0,
        le=100,
        description="响应内容与招标要求的领域关联度(0-100)"
    )
    relevance_note: Optional[str] = Field(
        default=None,
        description="关联性说明(关联性不足时必填)"
    )
    estimated_score: Optional[float] = Field(
        default=None,
        ge=0,
        description="评分项预估得分(仅评分项填写, 0~满分; AI 预估仅供参考)"
    )
    rule_assessments: Optional[list[RuleAssessmentItem]] = Field(
        default=None,
        description=(
            "评分项逐规则判定列表(Wave2-S5 P1 B1)。"
            "评分项必填(可为空列表[]); 非评分项可 null。"
            "每项对应一条评分/扣分规则的判定情况。"
        ),
    )

    @model_validator(mode="after")
    def _enforce_compliance_consistency(self) -> "ExtractedReviewResult":
        """强制 is_compliant 与 compliance_level 自洽(防 LLM 幻觉产生矛盾审查结论)。

        以语义更细的 compliance_level 为权威推导二元 is_compliant:
        - fully          -> 必须 is_compliant=True
        - not / missing  -> 必须 is_compliant=False
        - partially      -> 灰区, 尊重 LLM 给定值(不强制)
        发现并纠正矛盾时记 warning, 便于观测 LLM 输出质量。审查结论属高业务风险输出,
        字段自相矛盾会使 UI 展示与下游聚合失真, 故在入 schema 时即收敛。
        """
        expected: Optional[bool] = None
        if self.compliance_level == "fully":
            expected = True
        elif self.compliance_level in ("not", "missing"):
            expected = False

        if expected is not None and self.is_compliant != expected:
            logger.warning(
                "审查结论矛盾已纠正: requirement_id={}, compliance_level={}, "
                "is_compliant {} -> {}",
                self.requirement_id, self.compliance_level,
                self.is_compliant, expected,
            )
            self.is_compliant = expected
        return self


class ReviewJudgementPayload(BaseModel):
    """LLM 审查判断载荷（最小输出契约，）

 只收录必须由模型判断的字段。`requirement_id` / `category` / `requirement_summary`
 / `source_pages` 等服务端已知或根本不落库的字段一律移出，不再进入模型失败面——
 这些字段由 `reviewer_parts.result_assembly` 用权威数据装配。

 两个设计约束:
 - 宽进严出: Pydantic 默认忽略未声明的额外字段，因此即使 Prompt 未同步精简、
 模型仍回显旧字段，也不会校验失败。
 - 长度不设上限: `response_summary` 超长在装配边界确定性截断，而非拒绝整条输出
 （旧契约 max_length=50/100 曾造成大量纯长度失败）。
 """

    compliance_level: ComplianceLevelType = Field(..., description="符合程度")
    score: int = Field(..., ge=0, le=100, description="评分(0-100)")
    comment: str = Field(..., min_length=10, description="详细审查意见")
    risk_level: RiskLevelType = Field(..., description="风险等级")

    # 以下为可选：缺失时由装配层按 compliance_level 推导或从服务端数据回填
    is_compliant: Optional[bool] = Field(
        default=None,
        description="是否符合；缺失时由 compliance_level 推导（partially 视为不符合）",
    )
    response_summary: Optional[str] = Field(
        default=None, description="投标响应摘要；装配时截断至落库上限"
    )
    estimated_score: Optional[float] = Field(
        default=None, ge=0, description="评分项预估得分(仅评分项填写)"
    )
    rule_assessments: Optional[list[RuleAssessmentItem]] = Field(
        default=None, description="评分项逐规则判定列表"
    )
    relevance_score: Optional[int] = Field(
        default=None, ge=0, le=100, description="响应与要求的领域关联度"
    )
    relevance_note: Optional[str] = Field(
        default=None, description="关联性说明"
    )


class ReviewResultResponse(BaseModel):
    """单项审查结果响应模型"""
    id: int = Field(..., description="审查结果ID")
    project_id: int = Field(..., description="项目ID")
    requirement_id: int = Field(..., description="招标要求ID")
    bid_response_id: Optional[int] = Field(default=None, description="投标响应ID")
    category: str = Field(..., description="招标要求分类")
    requirement_summary: str = Field(..., description="招标要求摘要")
    requirement_description: Optional[str] = Field(default=None, description="招标要求原文")
    response_summary: Optional[str] = Field(default=None, description="投标响应摘要")
    response_content: Optional[str] = Field(default=None, description="投标响应原文")
    is_compliant: bool = Field(..., description="是否符合")
    compliance_level: str = Field(..., description="符合程度")
    pending_judgment: bool = Field(
        default=False,
        description="审查未产出有效符合结论，需人工判定；不是第五种符合程度",
    )
    score: int = Field(..., description="评分")
    comment: str = Field(..., description="审查意见")
    risk_level: str = Field(..., description="风险等级")
    canonical_key: Optional[str] = Field(default=None, description="跨类别归并键")
    fact_key: Optional[str] = Field(default=None, description="事实键")
    family_type: Optional[str] = Field(default=None, description="证据族类型")
    attribution_type: Optional[str] = Field(default=None, description="归因关系")
    consistency_flag: Optional[str] = Field(default=None, description="一致性标记")
    main_requirement_id: Optional[int] = Field(default=None, description="主判定要求ID")
    subject_scope: Optional[str] = Field(default=None, description="主体范围")
    evidence_relation: Optional[str] = Field(default=None, description="证据关系")
    reconcile_note: Optional[str] = Field(default=None, description="收敛解释说明")
    source_pages: Optional[str] = Field(default=None, description="来源页码")
    source_page_numbers: list[int] = Field(default_factory=list, description="来源页码列表")
    requirement_source_page: Optional[int] = Field(default=None, description="招标要求首个来源页码")
    requirement_source_pages: list[int] = Field(default_factory=list, description="招标要求来源页码列表")
    requirement_keywords: list[str] = Field(default_factory=list, description="招标要求关键词")
    requirement_sub_requirements: list[str] = Field(default_factory=list, description="招标要求子项列表")
    requirement_merged_count: Optional[int] = Field(default=None, description="招标要求合并数量")
    response_completeness: Optional[str] = Field(default=None, description="响应完整性")
    response_missing_elements: list[str] = Field(default_factory=list, description="缺失要素列表")
    response_linked_to: Optional[int] = Field(default=None, description="关联主响应ID")
    response_evidence_anchor: Optional[str] = Field(default=None, description="证据锚点")
    relevance_score: Optional[int] = Field(default=None, description="领域关联度(0-100)")
    relevance_note: Optional[str] = Field(default=None, description="关联性说明")
    # P2 闭环：评分项满分/预估得分（满分来自 requirement，预估得分来自 review_result）
    requirement_kind: str = Field(default="normal", description="要求种类: normal/scoring_item")
    max_score: Optional[float] = Field(default=None, description="评分项满分(score_weight)")
    is_key_review: bool = Field(default=False, description="是否重点评审项")
    # P2.2: 要求当前评审意义（非 actionable 且非评分链路的明细行不计入汇总评分）
    review_relevance: str = Field(default="actionable", description="要求当前评审意义: actionable/informational/procedural")
    estimated_score: Optional[float] = Field(default=None, description="评分项 AI 预估得分(仅供参考)")
    # 评分项卷归属（按卷聚合口径；供结果页"评分项视图"按卷分组下钻）。
    # 值已由 service 从 requirement.scoring_metadata 装配进结果 dict，此处仅声明字段以随响应下发。
    volume: Optional[str] = Field(default=None, description="评分项所属卷标识(technical/price/commercial...)")
    volume_label: Optional[str] = Field(default=None, description="评分项所属卷原文标签(如 商务标)")
    volume_max: Optional[float] = Field(default=None, description="所属卷满分合计(评分表声明)")
    volume_weight: Optional[float] = Field(default=None, description="所属卷权重(0-1, 加权制)")
    # 采购包/标段归属（多包文档结果页按包下钻；单包文档为 None）
    lot: Optional[str] = Field(default=None, description="评分项所属采购包/标段(文档原文, 如'采购包1')")
    retrieval_chunks: List[Any] = Field(
        default_factory=list,
        description="Fix-06: 多路检索命中（chunk_id/page_num/similarity/role/matched_sub_query/document_id），空列表表示历史数据或无命中",
    )
    # 投标信封分卷来源(整本/历史数据均为空, 前端回退单文档路径)
    source_document_id: Optional[int] = Field(
        default=None, description="投标响应归因文档ID(最高命中所在文档)"
    )
    source_parts: List[str] = Field(
        default_factory=list,
        description="来源卷角色列表(commercial/technical, 整本为空)",
    )
    source_page_locations: List[Any] = Field(
        default_factory=list,
        description="页码定位列表[{document_id,part,page,excerpt?}], 供按文档跳转预览",
    )
    source_page_excerpts: List[Any] = Field(
        default_factory=list,
        description="页级原文摘录[{document_id,page,excerpt}], 供 PDF 片段高亮; 无摘录则为空",
    )

    # Wave2-S5 P1 B1+B2+B3: 评分项规则字段（来自 requirement.scoring_metadata）
    score_item_name: Optional[str] = Field(
        default=None, description="评分项名称(来自 scoring_metadata.score_item_name)"
    )
    score_rules: list[Any] = Field(
        default_factory=list,
        description="评分/分档规则列表(来自 scoring_metadata.score_rules)",
    )
    deduction_rules: list[Any] = Field(
        default_factory=list,
        description="扣分/不得分规则列表(来自 scoring_metadata.deduction_rules)",
    )
    rule_assessments: Optional[list[dict]] = Field(
        default=None,
        description=(
            "逐规则判定结果列表(仅评分项; 非评分项为 null)。"
            "每项: {rule_excerpt, status, points, reason, evidence_pages}。"
        ),
    )

    # B1: 失败合同字段（默认不返回，FAILURE_STATE_ENABLED=true 时才序列化）
    failure_state: Optional[str] = Field(default=None, description="五状态枚举")
    failure_reason: Optional[str] = Field(default=None, description="失败原因")
    confidence: Optional[float] = Field(default=None, description="Gate 综合置信度")
    gate_trace: Optional[Any] = Field(default=None, description="Gate verdict 列表")
    review_required: Optional[int] = Field(default=None, description="是否需人工复核")
    reviewed_at: Optional[datetime] = Field(default=None, description="复核完成时间")
    reviewed_by: Optional[int] = Field(default=None, description="复核人 user_id")

    class Config:
        from_attributes = True

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
        return data


class ReviewSummaryResponse(BaseModel):
    """审查汇总响应模型"""
    project_id: int = Field(..., description="项目ID")
    total_count: int = Field(..., description="审查项总数")
    compliant_count: int = Field(..., description="符合数量")
    non_compliant_count: int = Field(..., description="不符合数量")
    
    # 按符合程度统计
    fully_count: int = Field(..., description="完全符合数量")
    partially_count: int = Field(..., description="部分符合数量")
    not_count: int = Field(..., description="不符合数量")
    missing_count: int = Field(..., description="缺失响应数量")
    
    # 风险统计
    high_risk_count: int = Field(..., description="高风险数量")
    medium_risk_count: int = Field(..., description="中风险数量")
    low_risk_count: int = Field(..., description="低风险数量")
    
    # 分数统计
    total_score: float = Field(..., description="总得分(加权)")
    average_score: float = Field(..., description="平均分")
    weighted_score: float = Field(default=0.0, description="综合加权得分")

    # P2 闭环：评分项预估总得分（AI 预估，仅供参考）
    scoring_item_count: int = Field(default=0, description="评分项数量")
    estimated_item_count: int = Field(default=0, description="已产出预估得分的评分项数(0=需重新审查)")
    estimated_total_score: float = Field(default=0.0, description="评分项预估总得分")
    max_total_score: float = Field(default=0.0, description="评分项满分合计")
    estimated_score_by_volume: dict = Field(
        default_factory=dict,
        description="按卷的预估/满分分布: {volume: {estimated, max, count}}; 多包文档键为'包·卷'复合标签",
    )
    scoring_total_reliable: bool = Field(
        default=True,
        description="满分合计是否落在单一100分制可信区间; False=多卷/加权/非100制/多包, 前端降级不展示统一总分",
    )
    estimated_score_by_lot: dict = Field(
        default_factory=dict,
        description=(
            "按采购包的预估/满分分布: {lot: {estimated, max, count, estimated_count,"
            " reliable, unassigned, by_volume}}; 仅多包(≥2)文档非空, 消费端须优先于统一总分展示"
        ),
    )
    
    # 最终结论
    conclusion: Literal["pass", "fail", "pending"] = Field(
        ...,
        description="最终结论：通过/不通过/待澄清"
    )
    conclusion_reason: str = Field(..., description="结论说明")


class ReviewResultListResponse(BaseModel):
    """审查结果列表响应模型"""
    summary: ReviewSummaryResponse = Field(..., description="审查汇总")
    results: list[ReviewResultResponse] = Field(
        default_factory=list,
        description="审查结果列表"
    )
    high_risk_items: list[ReviewResultResponse] = Field(
        default_factory=list,
        description="高风险项列表"
    )


class ReviewStepStatus(BaseModel):
    """审查流程单步进度状态（与 CollusionStepStatus 同构）"""
    label: str = Field(..., description="步骤名称")
    status: Literal["waiting", "running", "completed", "failed"] = Field(
        ..., description="步骤状态"
    )
    progress: int = Field(default=0, ge=0, le=100, description="步骤进度百分比(0-100)")
    detail: Optional[str] = Field(default=None, description="步骤详细描述")


class ReviewStatus(BaseModel):
    """审查状态模型

    steps[] 结构与串标审查 CollusionStatus 同构，方便前端复用共享进度组件。
    保留旧字段（stage/extraction_done 等）以便平滑过渡。
    """
    project_id: int = Field(..., description="项目ID")
    status: Literal["pending", "processing", "completed", "failed"] = Field(
        ..., description="整体审查状态"
    )
    overall_status: Literal["idle", "running", "completed", "failed"] = Field(
        default="idle", description="整体状态（与串标审查同构）"
    )
    overall_progress: int = Field(
        default=0, ge=0, le=100, description="整体进度百分比(0-100)"
    )
    steps: List[ReviewStepStatus] = Field(
        default_factory=list, description="四步进度详情"
    )
    error_message: Optional[str] = Field(default=None, description="失败时的错误信息")

    # 恢复状态信息
    is_recovering: bool = Field(default=False, description="是否正在自动恢复")
    recovery_count: int = Field(default=0, description="已自动恢复次数")
    retryable: bool = Field(default=False, description="当前失败态是否允许用户显式重试")
    failed_node: Optional[str] = Field(default=None, description="失败节点标识")
    target_id: Optional[int] = Field(default=None, description="细粒度重试目标ID")
    failed_stage: Optional[str] = Field(default=None, description="节点内部失败阶段")
    retry_action_label: str = Field(default="", description="重试按钮文案")
    preserved_outputs: List[str] = Field(default_factory=list, description="重试会保留的业务成果")
    reset_scope: List[str] = Field(default_factory=list, description="重试前允许清理或重置的范围")
    last_attempt: int = Field(default=0, description="当前节点最近一次运行 attempt")
    retry_running: bool = Field(default=False, description="用户触发的重试是否正在运行")
    last_error: Optional[str] = Field(default=None, description="最近失败原因")

    # --- 旧字段（保留兼容，过渡期后可移除） ---
    stage: Literal[
        "pending",
        "extracting",
        "extracting_responses",
        "extracted",
        "matching",
        "assembling_pairs",
        "reviewing",
        "reviewing_responses",
        "reporting",
        "result_ready",
        "completed",
        "failed",
    ] = Field(default="pending", description="当前阶段（旧字段）")
    total_items: int = Field(default=0, description="待审查项总数")
    processed_items: int = Field(default=0, description="已处理项数量")
    progress: float = Field(default=0.0, description="进度(0-1)（旧字段）")
    message: Optional[str] = Field(default=None, description="状态消息")
    extraction_total: int = Field(default=0, description="提取总数")
    extraction_done: int = Field(default=0, description="已提取数量")
    review_done: int = Field(default=0, description="已审查数量")
    report_ready: bool = Field(default=False, description="报告是否就绪")
    result_ready: bool = Field(default=False, description="审查结果是否就绪")
    report_available: bool = Field(default=False, description="是否可生成/下载报告")