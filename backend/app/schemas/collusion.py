"""串标审查 Schema 定义

包含串标审查功能的全部请求/响应/状态/LLM校验 Pydantic 模型。
"""

import re
from datetime import datetime
from typing import Optional, List, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator

from app.models.collusion_result import CollusionRiskLevel

PairAnalysisState = Literal["completed", "partial", "failed", "skipped"]
PairDecisionState = Literal["decidable", "needs_review", "failed", "skipped"]
ProjectDecision = Literal[
    "clear_no_risk",
    "suspicious_found",
    "partial_review_needed",
    "analysis_failed",
]
DecisionConfidence = Literal["high", "medium", "low"]

ROLE_TITLE_BLACKLIST: frozenset[str] = frozenset({
    "项目经理", "技术负责人", "总工程师", "设计负责人", "安全负责人",
    "质量负责人", "施工负责人", "勘察负责人", "监理工程师", "造价工程师",
    "财务负责人", "法定代表人", "授权代表", "投标代表", "商务负责人",
    "采购负责人", "生产负责人", "总经理", "副总经理", "总监",
    "主任工程师", "工程师", "负责人", "经理", "安全员", "质量员",
    "施工员", "资料员", "材料员", "测量员", "试验员",
    "技术员", "预算员", "标准员", "机械员", "劳务员",
})

ROLE_SUFFIX_PATTERNS: tuple[str, ...] = (
    "负责人", "工程师", "总监", "经理", "主任", "员",
    "代表", "专员", "组长", "队长", "班长",
)


# 维度-证据级业务契约。LLM 输出的 evidence_level 必须按此映射强制矫正,
# 不依赖 LLM 自律。analyzer/schemas 共享此契约，单一事实来源。
# - d1_personnel/d2_company → "first"（一级证据）
# - d3_text/d4_price/d5_qualification → 限制在 {"second","third"}
# - d6_format/d7_abnormal/d8_metadata → "third"（三级证据）
DIMENSION_EVIDENCE_LEVEL_CONTRACT: dict[str, str | tuple[str, ...]] = {
    "d1_personnel":     "first",
    "d2_company":       "first",
    "d3_text":          ("second", "third"),
    "d4_price":         ("second", "third"),
    "d5_qualification": ("second", "third"),
    "d6_format":        "third",
    "d7_abnormal":      "third",
    "d8_metadata":      "third",
}
EVIDENCE_LEVEL_CORRECTED_FLAG = "evidence_level_corrected"


def coerce_evidence_level_for_dim(
    dim_key: str, current: Optional[str],
) -> tuple[str, bool]:
    """按维度业务契约强制矫正 evidence_level。

    Returns:
        (target_level, was_corrected)
    """
    contract = DIMENSION_EVIDENCE_LEVEL_CONTRACT.get(dim_key)
    if contract is None:
        return (current or "third"), False
    if isinstance(contract, tuple):
        if current in contract:
            return current, False
        return "second", current != "second"
    if current == contract:
        return current, False
    return contract, True


def is_valid_person_name(name: str) -> bool:
    """校验 name 是否为真实人名而非岗位名称"""
    if not name or not name.strip():
        return False
    name = name.strip()
    if name in ROLE_TITLE_BLACKLIST:
        return False
    if any(name.endswith(s) for s in ROLE_SUFFIX_PATTERNS) and len(name) <= 6:
        return False
    return True


_SEMANTIC_NULL_QUOTES: frozenset[str] = frozenset({
    "无", "null", "none", "n/a", "na", "不适用", "未找到",
    "无相关内容", "未提取到", "/", "-", "--", "——",
})


def sanitize_quote(val: str | None) -> str | None:
    """将 LLM 输出的语义空值标准化为 None

    LLM 有时在 quote 字段输出中文"无"或"null"等文本而非 JSON null，
    导致下游误将其当作有效原文引用。
    """
    if val is None:
        return None
    stripped = val.strip().lower()
    if not stripped or stripped in _SEMANTIC_NULL_QUOTES:
        return None
    return val


FINDING_FIELD_NAMES: frozenset[str] = frozenset({
    "summary", "detail", "quote_a", "page_a",
    "quote_b", "page_b", "similarity",
})

_FLATTENED_FIELD_PATTERN = re.compile(
    r"^(summary|detail|quote_a|page_a|quote_b|page_b|similarity)\s*[:：]\s*",
    re.IGNORECASE,
)


def is_flattened_field_string(value: Any) -> bool:
    """检测字符串是否疑似 LLM 把 finding 的字段拍平为 'key: value' 形态。

    用于识别如 `"summary: ..."` / `"quote_a: 王博士"` / `"page_a: 3"` 等
    被错误拍平的字符串项。识别命中时上层应视为格式错位，触发 LLM 重试或
    走兜底合并路径。
    """
    if not isinstance(value, str):
        return False
    return bool(_FLATTENED_FIELD_PATTERN.match(value.strip()))


# 同一 list 内拍平字符串数量 >= 该阈值时视为格式错位
FLATTENED_FINDING_REJECT_THRESHOLD = 2


def normalize_finding_list_strict(v: Any, *, dict_no_keys_to_str: bool) -> list:
    """归一 LLM 输出的 finding 列表，命中拍平结构时直接抛 ValueError。

    - list 中拍平字符串(`is_flattened_field_string`)数量 >= 阈值时
      抛 ValueError -- Pydantic 会把它转成 ValidationError，触发现有
      validate_with_retry 自动重试链。
    - 单条孤立字符串(数量为 0 或 1) 保留旧兼容: 包成 {"summary","detail"}。
    - dict 项: `dict_no_keys_to_str=True` 时,无 summary/detail 的 dict
      会被字符串化(沿用 LLMDimensionScore 旧行为);否则原样保留。
    """
    if not isinstance(v, list):
        return []
    flatten_count = sum(1 for item in v if is_flattened_field_string(item))
    if flatten_count >= FLATTENED_FINDING_REJECT_THRESHOLD:
        raise ValueError(
            "findings 字段疑似被拍平为 'key: value' 字符串列表，"
            "必须输出结构化对象数组(每个 finding 是含 summary/detail 的 dict)"
        )
    normalized: list = []
    for item in v:
        if isinstance(item, str):
            normalized.append({"summary": item, "detail": item})
        elif isinstance(item, dict):
            if dict_no_keys_to_str and "summary" not in item and "detail" not in item:
                normalized.append({"summary": str(item), "detail": str(item)})
            else:
                normalized.append(item)
        else:
            normalized.append(item)
    return normalized


# ===========================================================================
# 进度 / 状态响应模型
# ===========================================================================

class CollusionStepStatus(BaseModel):
    """单步进度状态"""
    label: str = Field(..., description="步骤名称")
    status: str = Field(..., description="步骤状态（waiting/running/completed/failed）")
    progress: int = Field(default=0, ge=0, le=100, description="步骤进度百分比（0-100）")
    detail: Optional[str] = Field(default=None, description="步骤详细描述")


class CoverageStats(BaseModel):
    """串标分析覆盖统计"""
    total_pairs: int = Field(default=0, description="总对比对数")
    analyzed_pairs: int = Field(default=0, description="已落库的对比对数")
    llm_analyzed_pairs: int = Field(default=0, description="执行过LLM分析的对数")
    algorithm_only_pairs: int = Field(default=0, description="仅算法快检的对数")
    skipped_pairs: int = Field(default=0, description="智能跳过的对数")
    failed_pairs: int = Field(default=0, description="分析失败并落为占位结果的对数")
    inconclusive_pairs: int = Field(default=0, description="最终为无法判定的对数")
    review_required_pairs: int = Field(default=0, description="需人工复核的对数")
    clear_pairs: int = Field(default=0, description="已完成且无异常的对数")
    suspicious_pairs: int = Field(default=0, description="已完成且发现异常证据的对数")
    coverage_ratio: float = Field(default=0.0, ge=0.0, le=1.0, description="已落库对数 / 总对数")


class CollusionStatus(BaseModel):
    """串标分析进度状态响应

    对标 ReviewStatus 的四步进度模型。
    """
    project_id: int = Field(..., description="项目ID")
    overall_status: str = Field(
        ...,
        description="整体状态（idle/running/completed/failed）"
    )
    overall_progress: int = Field(
        default=0, ge=0, le=100,
        description="整体进度百分比（0-100）"
    )

    # 四步骤详情
    steps: List[CollusionStepStatus] = Field(
        default_factory=list,
        description="四步进度详情"
    )

    # 计数信息（供前端显示）
    total_documents: int = Field(default=0, description="总文档数（含招标文件）")
    total_bid_docs: int = Field(default=0, description="投标文件数量")
    total_pairs: int = Field(default=0, description="总对比对数 C(N_bid,2)")
    extracted_count: int = Field(default=0, description="已完成信息提取的文档数")
    compared_count: int = Field(default=0, description="已完成对比的文件对数")

    # 智能筛对统计
    total_llm_pairs: int = Field(default=0, description="实际做LLM分析的对数")
    skipped_pairs: int = Field(default=0, description="智能跳过的对数")
    failed_pairs: int = Field(default=0, description="分析失败的对数")
    total_groups: int = Field(default=0, description="已识别的嫌疑群组数")
    global_features_done: bool = Field(default=False, description="全局特征分析是否完成")
    cluster_done: bool = Field(default=False, description="图聚类是否完成")

    # 元信息
    has_tender_doc: bool = Field(default=False, description="是否包含招标文件（白名单过滤器）")
    error_message: Optional[str] = Field(default=None, description="失败时的错误信息")

    # 恢复状态信息
    message: Optional[str] = Field(default=None, description="人类可读的状态说明")
    is_recovering: bool = Field(default=False, description="是否正在自动恢复")
    recovery_count: int = Field(default=0, description="已自动恢复次数(0=从未恢复)")
    retryable: bool = Field(default=False, description="当前失败态是否允许显式重试")
    failed_node: Optional[str] = Field(default=None, description="失败节点标识")
    target_id: Optional[int] = Field(default=None, description="细粒度重试目标ID")
    failed_stage: Optional[str] = Field(default=None, description="失败节点内部阶段")
    retry_action_label: str = Field(default="", description="显式重试按钮文案")
    preserved_outputs: List[str] = Field(default_factory=list, description="重试会保留的业务成果")
    reset_scope: List[str] = Field(default_factory=list, description="重试前允许清理或重置的范围")
    last_attempt: int = Field(default=0, description="当前节点最近一次运行 attempt")
    retry_running: bool = Field(default=False, description="用户触发的显式重试是否正在运行")
    last_error: Optional[str] = Field(default=None, description="最近错误摘要")


# ===========================================================================
# 结果响应模型
# ===========================================================================

def build_pair_id(doc_a_id: int, doc_b_id: int) -> str:
    """构建稳定的文件对标识（与顺序无关）"""
    a, b = sorted((int(doc_a_id), int(doc_b_id)))
    return f"{a}:{b}"


class EvidenceRef(BaseModel):
    """单条证据中的单文档引用"""
    doc_id: Optional[int] = Field(default=None, description="引用所属文档ID")
    doc_name: Optional[str] = Field(default=None, description="引用所属文档名")
    quote: Optional[str] = Field(default=None, description="原文摘录")
    location: Optional[str] = Field(default=None, description="位置描述（页码/章节）")
    section: Optional[str] = Field(default=None, description="章节信息（可选）")

    @field_validator("location", mode="before")
    @classmethod
    def coerce_location(cls, v):
        return str(v) if v is not None and not isinstance(v, str) else v


class FindingItem(BaseModel):
    """单条发现证据（含多文档定位信息）"""
    pair_id: Optional[str] = Field(default=None, description="文件对标识（doc_a_id:doc_b_id）")
    dimension: str = Field(..., description="所属维度（D1-D8）")
    level: str = Field(..., description="证据级别（first/second/third）")
    description: str = Field(..., description="发现概述")
    detail: Optional[str] = Field(default=None, description="详细分析（含原文引用和推理）")
    evidences: List[EvidenceRef] = Field(default_factory=list, description="结构化证据引用列表")
    evidence: Optional[str] = Field(default=None, description="具体证据引用（旧字段，保留兼容）")
    quote_a: Optional[str] = Field(default=None, description="文件A原文摘录")
    page_a: Optional[str] = Field(default=None, description="文件A位置（页码/章节）")
    quote_b: Optional[str] = Field(default=None, description="文件B原文摘录")
    page_b: Optional[str] = Field(default=None, description="文件B位置（页码/章节）")
    page_ref: Optional[str] = Field(default=None, description="页码引用（旧字段，保留兼容）")
    similarity: Optional[float] = Field(default=None, description="段落相似度")

    # 群组级证据扩展字段（证据融合后可涉及 N 个文档）
    involved_doc_ids: List[int] = Field(default_factory=list, description="涉及的文档ID列表(群组级)")
    involved_doc_names: List[str] = Field(default_factory=list, description="涉及的文档名列表(群组级)")

    # 跨字段串联引擎字段（cross_field_correlator 命中时填充）
    risk_pattern: Optional[str] = Field(
        default=None,
        description="跨字段命中规则的业务标签（如 author_eq_other_legal_rep）",
    )
    source_field_a: Optional[str] = Field(
        default=None,
        description="跨字段命中的左侧字段路径（如 metadata_info.author）",
    )
    source_field_b: Optional[str] = Field(
        default=None,
        description="跨字段命中的右侧字段路径（如 company_info.legal_representative）",
    )
    legal_reference: Optional[str] = Field(
        default=None,
        description="跨字段命中的法规对照（如《招标投标法实施条例》第40条第6项）",
    )

    @field_validator("page_a", "page_b", "page_ref", mode="before")
    @classmethod
    def coerce_page(cls, v):
        return str(v) if v is not None and not isinstance(v, str) else v

    @field_validator("involved_doc_ids", "involved_doc_names", mode="before")
    @classmethod
    def coerce_involved_lists(cls, v):
        return v if isinstance(v, list) else []

    certainty: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="确定性（0-1）"
    )

    # 维度信号项标志：该维度有得分但未形成带可复核原文引用的正式证据，
    # 由 dimension_signals 合成为参考级条目，保证"凡维度得分>0必在证据列表可见"，
    # 供前端弱化展示并交用户审定，不得作为正式定性证据。
    is_dimension_signal: bool = Field(
        default=False,
        description="是否为维度信号项（有得分但缺正式证据，参考级）",
    )


class AttentionItem(BaseModel):
    """关注项: 不构成串标证据但值得注意的发现"""
    category: str = Field(..., description="类别(business_relationship/unverified_quote/metadata_note)")
    dimension: str = Field(..., description="来源维度(D1-D8)")
    summary: str = Field(..., description="关注点概述")
    detail: Optional[str] = Field(default=None, description="详细说明")
    source_reason: str = Field(default="", description="为什么列为关注项而非证据")


class EvidenceChunk(BaseModel):
    """向量预筛选的高相似重合片段（供 pair 详情展示"到底哪几段重合"）

    职责: 透传 similarity.py 算出的 chunk 对原文与相似度信号，并标注是否
        疑似通用模板（boilerplate_flag / kb_rule_match）。
    不做: 风险判定、串标定性 -- 仅作为"重合内容可视化"辅助甄别证据。
    """
    text_a: str = Field(default="", description="文件A的重合片段原文")
    text_b: str = Field(default="", description="文件B的重合片段原文")
    page_a: Optional[int] = Field(default=None, description="文件A片段页码")
    page_b: Optional[int] = Field(default=None, description="文件B片段页码")
    score: Optional[float] = Field(default=None, description="语义相似度（0-1）")
    literal_score: Optional[float] = Field(default=None, description="字面重合度（0-1）")
    boilerplate_flag: bool = Field(default=False, description="是否疑似行业通用/模板表述")
    kb_rule_match: bool = Field(default=False, description="是否命中通用白名单规则")


class CollusionPairResult(BaseModel):
    """单对文件的对比结果摘要"""
    pair_id: str = Field(..., description="文件对标识（doc_a_id:doc_b_id）")
    doc_a_id: int = Field(..., description="文件A的文档ID")
    doc_b_id: int = Field(..., description="文件B的文档ID")
    doc_a_name: str = Field(..., description="文件A的文件名")
    doc_b_name: str = Field(..., description="文件B的文件名")
    overall_risk: CollusionRiskLevel = Field(..., description="综合风险等级")
    risk_level: Optional[CollusionRiskLevel] = Field(default=None, description="纯业务风险等级；仅在可直接决策时返回")
    similarity_score: Optional[float] = Field(default=None, description="向量相似度分数")

    # 八维度得分
    d1_personnel_overlap: Optional[float] = Field(default=None, description="D1人员重叠分数")
    d2_company_relation: Optional[float] = Field(default=None, description="D2企业关联分数")
    d3_text_similarity: Optional[float] = Field(default=None, description="D3文本雷同分数")
    d4_price_pattern: Optional[float] = Field(default=None, description="D4报价异常分数")
    d5_qualification_overlap: Optional[float] = Field(default=None, description="D5资质业绩重叠")
    d6_format_similarity: Optional[float] = Field(default=None, description="D6文档格式相似")
    d7_abnormal_consistency: Optional[float] = Field(default=None, description="D7异常一致性")
    d8_metadata_similarity: Optional[float] = Field(default=None, description="D8元数据相似")

    findings: List[FindingItem] = Field(default_factory=list, description="具体发现列表")
    evidence_chunks: List[EvidenceChunk] = Field(
        default_factory=list,
        description="向量预筛选的高相似重合片段（含疑似通用模板标注），供人工甄别重合内容",
    )
    analysis_mode: str = Field(default="algorithm_only", description="分析模式")
    review_state: Optional[str] = Field(default=None, description="审查语义状态（completed/skipped/failed）")
    analysis_state: PairAnalysisState = Field(default="completed", description="分析执行状态（completed/partial/failed/skipped）")
    decision_state: PairDecisionState = Field(default="decidable", description="是否可直接形成业务结论")
    quality_flags: List[str] = Field(default_factory=list, description="质量门禁或解释性标记")
    consistency_flags: List[str] = Field(default_factory=list, description="维度间一致性校验提示")
    risk_explanation: Optional[str] = Field(default=None, description="风险判定推理说明")
    attention_items: List[AttentionItem] = Field(
        default_factory=list,
        description="关注项: 不构成串标证据但值得注意的发现(业务往来/部分可验证引文等)",
    )
    dimension_certainties: Optional[dict] = Field(
        default=None,
        description="各维度certainty值，用于前端区分重叠度和嫌疑度",
    )
    @model_validator(mode="after")
    def ensure_pair_id(self):
        if not self.pair_id:
            self.pair_id = build_pair_id(self.doc_a_id, self.doc_b_id)
        return self

    class Config:
        from_attributes = True


class SuspectedGroup(BaseModel):
    """疑似串通组合"""
    document_ids: List[int] = Field(..., description="涉及的文档ID列表")
    document_names: List[str] = Field(..., description="涉及的文档名称列表")
    risk_level: CollusionRiskLevel = Field(..., description="该组合的风险等级")
    reason: str = Field(..., description="认定原因摘要")


class CollusionResults(BaseModel):
    """串标分析完整结果响应"""
    project_id: int = Field(..., description="项目ID")
    overall_risk: CollusionRiskLevel = Field(..., description="整体风险等级")
    project_decision: ProjectDecision = Field(default="partial_review_needed", description="项目级业务结论")
    project_risk_level: Optional[str] = Field(default=None, description="项目级纯业务风险等级")
    decision_confidence: DecisionConfidence = Field(default="low", description="项目级结论可信度")
    review_required: bool = Field(default=True, description="当前是否仍需人工复核")
    decision_basis: str = Field(default="pairs", description="项目级结论口径（pairs/groups）")
    decision_engine_version: int = Field(default=1, description="项目级裁决器版本")
    overall_risk_basis: str = Field(default="pairs", description="整体风险口径（pairs/groups）")
    risk_distribution_basis: str = Field(default="pairs", description="风险分布图统计口径（pairs/groups）")
    risk_consistency_status: str = Field(default="aligned", description="整体风险与项目级summary是否一致（aligned/diverged）")
    risk_consistency_note: Optional[str] = Field(default=None, description="风险口径说明或差异提示")
    has_tender_doc: bool = Field(default=False, description="是否有招标文件参照")
    summary_source_version: int = Field(default=1, description="项目级综合结论结构版本")
    data_quality_flags: List[str] = Field(default_factory=list, description="数据质量与覆盖告警")
    profile_quality_flags: List[str] = Field(default_factory=list, description="行业画像质量告警")
    coverage_stats: CoverageStats = Field(default_factory=CoverageStats, description="覆盖率统计")
    industry_profile_snapshot: Optional[dict] = Field(default=None, description="行业画像快照")
    industry_profile_confidence: Optional[str] = Field(default=None, description="行业画像置信度（high/medium/low/unknown）")
    industry_profile_status: Optional[str] = Field(
        default=None,
        description="行业画像状态: success/llm_returned_unknown/insufficient_signals/llm_failed",
    )
    industry_profile_signals: List[dict] = Field(default_factory=list, description="行业画像信号列表")
    factual_explanation: Optional[str] = Field(
        default=None,
        description="后端按规则拼装的项目级事实说明段（覆盖率/降级原因/行业画像状态等结构化事实）",
    )
    factual_explanation_items: List[str] = Field(
        default_factory=list,
        description="后端按规则拼装的项目级事实说明条目（供 UI 编号展示，不含使用限制提示）",
    )

    # 可疑度矩阵（供前端渲染热力表格，值为 max(score*certainty) 跨维度加权）
    similarity_matrix: List[List[Optional[float]]] = Field(
        default_factory=list,
        description="NxN可疑度矩阵（certainty加权），None表示同一文件或尚未分析"
    )
    document_labels: List[str] = Field(
        default_factory=list,
        description="矩阵行/列标签（文件名）"
    )

    # 各对详细结果
    pair_results: List[CollusionPairResult] = Field(
        default_factory=list,
        description="每对文件的详细对比结果"
    )

    # 综合研判结论
    suspected_groups: List[SuspectedGroup] = Field(
        default_factory=list,
        description="疑似串通组合列表"
    )
    synthesis_summary: Optional[str] = Field(
        default=None,
        description="综合研判结论文字摘要"
    )
    narrative_summary: Optional[str] = Field(
        default=None,
        description="项目级统一裁决后的说明文字"
    )
    investigation_suggestions: Optional[List[str]] = Field(
        default=None,
        description="调查建议列表"
    )

    # 嫌疑群组（多方群组识别主输出）
    groups: List["CollusionGroupResult"] = Field(
        default_factory=list,
        description="嫌疑群组列表（图聚类+群组研判结果）"
    )
    global_findings: List["GlobalFindingItem"] = Field(
        default_factory=list,
        description="全局特征发现列表（人员重叠/企业关联/报价异常/元数据聚类）"
    )

    # 提取阶段警告
    extraction_warnings: List[str] = Field(
        default_factory=list,
        description="提取阶段警告（如文件提取失败）"
    )

    completed_at: Optional[datetime] = Field(default=None, description="分析完成时间")

    # B1: 失败合同字段（默认不返回）
    failure_state: Optional[str] = Field(default=None, description="五状态枚举")
    failure_reason: Optional[str] = Field(default=None, description="失败原因")
    confidence: Optional[float] = Field(default=None, description="Gate 综合置信度")
    gate_trace: Optional[Any] = Field(default=None, description="Gate verdict 列表")
    failure_review_required: Optional[int] = Field(default=None, description="是否需人工复核(失败合同)")
    reviewed_at: Optional[datetime] = Field(default=None, description="复核完成时间")
    reviewed_by: Optional[int] = Field(default=None, description="复核人 user_id")

    class Config:
        from_attributes = True

    @model_serializer(mode="wrap")
    def _strip_failure_state_fields(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        from app.core.config import settings
        _B1_FIELDS = ("failure_state", "failure_reason", "confidence", "gate_trace",
                       "failure_review_required", "reviewed_at", "reviewed_by")
        if not settings.FAILURE_STATE_ENABLED:
            for k in _B1_FIELDS:
                data.pop(k, None)
        else:
            for k in _B1_FIELDS:
                if k in data and data[k] is None:
                    del data[k]
        return data


class CollusionProjectSummary(BaseModel):
    """项目级串标综合结论快照"""
    summary_source_version: int = Field(default=1, description="结构版本")
    overall_risk: str = Field(default="inconclusive", description="整体风险等级")
    project_decision: ProjectDecision = Field(default="partial_review_needed", description="项目级业务结论")
    project_risk_level: Optional[str] = Field(default=None, description="项目级纯业务风险等级")
    decision_confidence: DecisionConfidence = Field(default="low", description="项目级结论可信度")
    review_required: bool = Field(default=True, description="当前是否仍需人工复核")
    decision_basis: str = Field(default="pairs", description="项目级结论口径（pairs/groups）")
    decision_engine_version: int = Field(default=1, description="项目级裁决器版本")
    overall_risk_basis: str = Field(default="pairs", description="整体风险口径（pairs/groups）")
    risk_distribution_basis: str = Field(default="pairs", description="风险分布图统计口径（pairs/groups）")
    risk_consistency_status: str = Field(default="aligned", description="整体风险与项目级summary是否一致（aligned/diverged）")
    risk_consistency_note: Optional[str] = Field(default=None, description="风险口径说明或差异提示")
    summary: str = Field(default="", description="综合结论摘要")
    narrative_summary: str = Field(default="", description="项目级统一裁决后的说明文字")
    suspected_groups: List[SuspectedGroup] = Field(default_factory=list, description="疑似串通组合")
    investigation_suggestions: List[str] = Field(default_factory=list, description="调查建议")
    coverage_stats: CoverageStats = Field(default_factory=CoverageStats, description="覆盖统计")
    data_quality_flags: List[str] = Field(default_factory=list, description="数据质量告警")
    profile_quality_flags: List[str] = Field(default_factory=list, description="行业画像质量告警")
    industry_profile_snapshot: Optional[dict] = Field(default=None, description="行业画像快照")
    industry_profile_confidence: Optional[str] = Field(default=None, description="行业画像置信度（high/medium/low/unknown）")
    industry_profile_status: Optional[str] = Field(
        default=None,
        description="行业画像状态: success/llm_returned_unknown/insufficient_signals/llm_failed",
    )
    industry_profile_signals: List[dict] = Field(default_factory=list, description="行业画像信号列表")
    factual_explanation: Optional[str] = Field(
        default=None,
        description="后端按规则拼装的项目级事实说明段（覆盖率/降级原因/行业画像状态等结构化事实）",
    )
    factual_explanation_items: List[str] = Field(
        default_factory=list,
        description="后端按规则拼装的项目级事实说明条目（供 UI 编号展示，不含使用限制提示）",
    )
    collusion_network: Optional[str] = Field(default=None, description="串通网络描述")
    bid_accompanying_pattern: Optional[str] = Field(default=None, description="陪标模式分析")
    groups_summary: str = Field(default="", description="群组摘要")
    ungrouped_notes: Optional[str] = Field(default=None, description="未归组异常说明")
    # P0-3: 未归入任何嫌疑群组的全局特征发现（D4 报价异常 / D8 元数据聚类等），
    # 通过 API 附加到 /results 的 global_findings 列表，避免在 aggregated_evidence
    # 中被群组结构漏掉。每项结构与 GlobalFindingItem 对齐。
    global_findings_extra: List[dict] = Field(
        default_factory=list,
        description="未归入群组的全局特征发现（灰度 COLLUSION_GLOBAL_FINDINGS_ENRICH）",
    )

    @field_validator("overall_risk", mode="before")
    @classmethod
    def normalize_risk(cls, v: Any) -> str:
        risk = str(v or "inconclusive").lower()
        return risk if risk in {"high", "medium", "low", "none", "inconclusive"} else "inconclusive"

    @field_validator(
        "data_quality_flags",
        "profile_quality_flags",
        "factual_explanation_items",
        "investigation_suggestions",
        "industry_profile_signals",
        mode="before",
    )
    @classmethod
    def coerce_str_list(cls, v):
        return v if isinstance(v, list) else []


# ===========================================================================
# LLM 输出校验模型（Pydantic 严格校验 JSON 输出）
# ===========================================================================

class LLMExtractionCompanyInfo(BaseModel):
    """LLM提取 - 企业基本信息"""
    company_name: Optional[str] = Field(default=None, description="企业名称")
    legal_representative: Optional[str] = Field(default=None, description="法定代表人")
    registered_address: Optional[str] = Field(default=None, description="注册地址")
    contact_phone: Optional[str] = Field(default=None, description="联系电话")
    bank_account: Optional[str] = Field(default=None, description="银行账户（脱敏）")
    unified_social_credit_code: Optional[str] = Field(default=None, description="统一社会信用代码")
    source_page: Optional[str] = Field(default=None, description="信息首次出现的页码")

    @field_validator("source_page", mode="before")
    @classmethod
    def coerce_source_page(cls, v):
        return str(v) if v is not None else None


class LLMExtractionPersonnelItem(BaseModel):
    """LLM提取 - 单个关键人员"""
    name: Optional[str] = Field(default=None, description="人员姓名")
    role: Optional[str] = Field(default=None, description="岗位/职务")
    id_number: Optional[str] = Field(default=None, description="身份证号（脱敏）")
    certificate_numbers: Optional[List[str]] = Field(
        default=None, description="资质证书编号列表"
    )
    source_page: Optional[str] = Field(default=None, description="首次出现的页码")

    @model_validator(mode="after")
    def swap_or_clear_role_as_name(self):
        """LLM 有时将岗位名称填入 name 字段，此处拦截并修正"""
        raw_name = (self.name or "").strip()
        raw_role = (self.role or "").strip()
        if raw_name and not is_valid_person_name(raw_name):
            if raw_role and is_valid_person_name(raw_role):
                self.name, self.role = raw_role, raw_name
            else:
                self.name = None
        return self

    @field_validator("source_page", mode="before")
    @classmethod
    def coerce_source_page(cls, v):
        return str(v) if v is not None else None


class LLMExtractionPriceInfo(BaseModel):
    """LLM提取 - 报价信息"""
    total_price: Optional[str] = Field(default=None, description="投标总价")
    price_breakdown: Optional[Any] = Field(default=None, description="分项报价明细")
    guarantee_amount: Optional[str] = Field(default=None, description="投标保证金金额")
    validity_period: Optional[str] = Field(default=None, description="投标有效期")


class LLMExtractionResult(BaseModel):
    """LLM文档信息提取完整输出

    对应串标信息提取节点 (collusion.route_extract) 的输出结构。
    """
    company_info: Optional[LLMExtractionCompanyInfo] = Field(
        default=None, description="企业基本信息"
    )
    personnel_list: List[LLMExtractionPersonnelItem] = Field(
        default_factory=list, description="关键人员列表"
    )
    price_info: Optional[LLMExtractionPriceInfo] = Field(
        default=None, description="报价信息"
    )
    technical_highlights: Optional[List[str]] = Field(
        default=None, description="技术方案核心特征摘要（3-5条）"
    )
    qualifications: Optional[List[str]] = Field(
        default=None, description="资质证书列表"
    )
    past_projects: Optional[List[str]] = Field(
        default=None, description="业绩项目列表"
    )
    subcontractors: Optional[List[str]] = Field(
        default=None, description="分包商/供应商列表"
    )

    @field_validator("personnel_list", mode="before")
    @classmethod
    def coerce_personnel_list(cls, v):
        return v if isinstance(v, list) else []

    @model_validator(mode="after")
    def filter_incomplete_personnel(self):
        """过滤 name 或 role 缺失/空的人员条目，保证下游数据质量"""
        if self.personnel_list:
            self.personnel_list = [
                p for p in self.personnel_list
                if p.name and p.name.strip() and p.role and p.role.strip()
            ]
        return self


# ===========================================================================
# 串标 analyzer 完整 extraction 契约（LLM 输出 + 算法后处理）
# ---------------------------------------------------------------------------
# `LLMExtractionResult` 保持"纯 LLM 输出"语义（chat_with_validation 的 response_model）。
# 以下三个模型覆盖"算法后处理"字段，避免 extraction dict 里的子键靠口头约定。
# 消费侧仍是 dict-based（通过 model_dump(exclude_none=True) 得到），最小破坏。
# ===========================================================================

class CollusionExtractionMetadata(BaseModel):
    """串标 D8 / 算法后处理的 PDF 元数据契约（非 LLM 输出）。

 由 `analyzer._build_collusion_metadata_info` 和 `_inject_d8_from_metadata`
 / `GlobalFeatureAnalyzer._cluster_metadata` 等消费。`extra="ignore"`
 允许存量 DB 数据包含遗留子键（如 已修复前残留的 subcontractors
 错位字段），不会破坏校验；新写入路径已剥离该错位键，subcontractors
 独立存储于 `CollusionExtraction.subcontractors_info` 列。
 """
    creator_tool: Optional[str] = Field(default=None, description="PDF Creator")
    author: Optional[str] = Field(default=None, description="PDF Author")
    producer: Optional[str] = Field(default=None, description="PDF Producer")
    title: Optional[str] = Field(default=None, description="PDF Title")
    subject: Optional[str] = Field(default=None, description="PDF Subject")
    keywords: Optional[str] = Field(default=None, description="PDF Keywords")
    creation_date: Optional[str] = Field(default=None, description="PDF 创建时间")
    mod_date: Optional[str] = Field(default=None, description="PDF 修改时间")
    raw_pdf_metadata: Optional[dict] = Field(
        default=None, description="原始 PDF metadata dict（pdfplumber 原样保留）"
    )
    extraction_trace: Optional[dict] = Field(
        default=None, description="多路提取 trace（extraction_routes 合并时写入）"
    )

    model_config = ConfigDict(extra="ignore")


class CollusionDocxStructure(BaseModel):
    """P2-1 parser 补采的 D6 维度文档结构契约。

    由 `docx_structure_extractor.extract_docx_structure` 产出，
    `D6FormatAlgorithmicGate` 消费。`extra="ignore"` 兼容未来扩展字段。
    """
    toc_headings: List[str] = Field(default_factory=list, description="目录/标题序列")
    font_families: List[str] = Field(default_factory=list, description="使用的字体族")
    margins: dict[str, float] = Field(
        default_factory=dict, description="页边距 mm（top/bottom/left/right）"
    )
    headers_footers: List[str] = Field(
        default_factory=list, description="页眉/页脚文本去重列表"
    )

    model_config = ConfigDict(extra="ignore")


class CollusionExtractionPayload(LLMExtractionResult):
    """串标 analyzer 完整 extraction 顶层契约 = LLM 原生输出 + 算法后处理。

    用于 `_save_extraction` 落盘前做顶层兜底校验；下游消费仍通过 dict 访问，
    `model_dump(exclude_none=True)` 保持和原有 dict 结构一致的键名。
    """
    metadata_info: Optional[CollusionExtractionMetadata] = Field(
        default=None, description="算法后处理的 PDF 元数据"
    )
    docx_structure: Optional[CollusionDocxStructure] = Field(
        default=None, description="P2-1 parser 补采的文档结构"
    )

    model_config = ConfigDict(extra="ignore")


class LLMTenderFormatResult(BaseModel):
    """LLM招标格式要求提取输出

    对应 COLLUSION_TENDER_FORMAT_PROMPT 的输出结构。
    """
    toc_requirements: Optional[List[str]] = Field(
        default=None, description="目录结构要求"
    )
    fixed_wording: Optional[List[str]] = Field(
        default=None, description="投标函/承诺书等固定措辞"
    )
    price_table_format: Optional[str] = Field(
        default=None, description="报价表格式要求描述"
    )
    unified_format_rules: Optional[List[str]] = Field(
        default=None, description="其他统一格式规定"
    )


class LLMDimensionScore(BaseModel):
    """LLM对比 - 单维度评分结果"""
    score: float = Field(..., ge=0.0, le=1.0, description="分数（0-1）")
    findings: List[Any] = Field(default_factory=list, description="发现列表（结构化对象或字符串）")
    evidence_level: str = Field(
        default="third",
        description="证据级别（first/second/third）"
    )
    certainty: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="确定性（0-1）"
    )

    @field_validator("evidence_level")
    @classmethod
    def validate_evidence_level(cls, v: str) -> str:
        allowed = {"first", "second", "third"}
        if v not in allowed:
            return "third"
        return v

    @field_validator("findings", mode="before")
    @classmethod
    def normalize_findings(cls, v: Any) -> list:
        """兼容旧格式：字符串自动转为结构化对象。

        若检测到 LLM 把 finding 的字段拍平为 "key: value" 字符串列表，
        将抛 ValueError 触发外层 validate_with_retry。
        """
        return normalize_finding_list_strict(v, dict_no_keys_to_str=True)


class LLMCompareResult(BaseModel):
    """LLM深度对比分析完整输出

    对应串标分组对比节点 (collusion.pair_compare_*) 的输出结构。
    """
    overall_risk: str = Field(..., description="综合风险等级（high/medium/low/none/inconclusive）")
    d1_personnel: Optional[LLMDimensionScore] = Field(default=None, description="D1人员重叠")
    d2_company: Optional[LLMDimensionScore] = Field(default=None, description="D2企业关联")
    d3_text: Optional[LLMDimensionScore] = Field(default=None, description="D3文本雷同")
    d4_price: Optional[LLMDimensionScore] = Field(default=None, description="D4报价异常")
    d5_qualification: Optional[LLMDimensionScore] = Field(default=None, description="D5资质业绩")
    d6_format: Optional[LLMDimensionScore] = Field(default=None, description="D6文档格式")
    d7_abnormal: Optional[LLMDimensionScore] = Field(default=None, description="D7异常一致")
    d8_metadata: Optional[LLMDimensionScore] = Field(default=None, description="D8元数据")
    key_findings: List[Any] = Field(default_factory=list, description="关键发现（结构化对象或字符串）")
    risk_explanation: Optional[str] = Field(default=None, description="风险等级判定说明")

    @field_validator("overall_risk")
    @classmethod
    def validate_risk(cls, v: str) -> str:
        allowed = {"high", "medium", "low", "none", "inconclusive"}
        if v.lower() not in allowed:
            return "inconclusive"
        return v.lower()

    @field_validator("key_findings", mode="before")
    @classmethod
    def normalize_key_findings(cls, v: Any) -> list:
        """兼容旧格式：字符串自动转为结构化对象。

        若检测到 LLM 把 finding 的字段拍平为 "key: value" 字符串列表，
        将抛 ValueError 触发外层 validate_with_retry。
        """
        return normalize_finding_list_strict(v, dict_no_keys_to_str=False)

    @model_validator(mode="after")
    def enforce_dim_contract(self) -> "LLMCompareResult":
        return _enforce_dim_contract_on_model(self)


def _enforce_dim_contract_on_model(model_obj: Any) -> Any:
    """: 在 LLM 分组输出 model_validator 中按维度契约矫正 evidence_level。

 遍历 DIMENSION_EVIDENCE_LEVEL_CONTRACT 中的每个维度字段，若该字段存在且为
 LLMDimensionScore 实例，则按契约覆写 evidence_level。审计 flag 由 analyzer 端
 在 dict 视图上写入（避免与 Pydantic 模型 extra=forbid 默认行为冲突）。
 """
    for dim_key in DIMENSION_EVIDENCE_LEVEL_CONTRACT:
        dim = getattr(model_obj, dim_key, None)
        if dim is None:
            continue
        target, corrected = coerce_evidence_level_for_dim(
            dim_key, getattr(dim, "evidence_level", None)
        )
        if corrected:
            dim.evidence_level = target
    return model_obj


class LLMEntityResult(BaseModel):
    """分组对比输出 -- 组A: D1人员重叠 + D2企业关联"""
    d1_personnel: Optional[LLMDimensionScore] = Field(default=None, description="D1人员重叠")
    d2_company: Optional[LLMDimensionScore] = Field(default=None, description="D2企业关联")
    risk_explanation: Optional[str] = Field(default=None, description="判定说明")

    @model_validator(mode="after")
    def enforce_dim_contract(self) -> "LLMEntityResult":
        return _enforce_dim_contract_on_model(self)


class LLMTextPatternResult(BaseModel):
    """分组对比输出 -- 组B: D3文本雷同 + D6文档格式 + D7异常一致"""
    d3_text: Optional[LLMDimensionScore] = Field(default=None, description="D3文本雷同")
    d6_format: Optional[LLMDimensionScore] = Field(default=None, description="D6文档格式")
    d7_abnormal: Optional[LLMDimensionScore] = Field(default=None, description="D7异常一致")
    risk_explanation: Optional[str] = Field(default=None, description="判定说明")

    @model_validator(mode="after")
    def enforce_dim_contract(self) -> "LLMTextPatternResult":
        return _enforce_dim_contract_on_model(self)


class LLMBusinessResult(BaseModel):
    """分组对比输出 -- 组C: D4报价异常 + D5资质业绩"""
    d4_price: Optional[LLMDimensionScore] = Field(default=None, description="D4报价异常")
    d5_qualification: Optional[LLMDimensionScore] = Field(default=None, description="D5资质业绩")
    risk_explanation: Optional[str] = Field(default=None, description="判定说明")

    @model_validator(mode="after")
    def enforce_dim_contract(self) -> "LLMBusinessResult":
        return _enforce_dim_contract_on_model(self)


class LLMSingleDimResult(BaseModel):
    """深搜单维度重试输出"""
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="维度分数")
    findings: List[Any] = Field(default_factory=list, description="发现列表")
    evidence_level: str = Field(default="third", description="证据级别")
    certainty: float = Field(default=0.5, ge=0.0, le=1.0, description="确定性")
    risk_explanation: Optional[str] = Field(default=None, description="判定说明")

    @field_validator("evidence_level")
    @classmethod
    def validate_evidence_level(cls, v: str) -> str:
        allowed = {"first", "second", "third"}
        return v if v in allowed else "third"

    @field_validator("findings", mode="before")
    @classmethod
    def normalize_findings(cls, v: Any) -> list:
        """兼容旧格式；命中拍平结构时抛 ValueError 触发重试。"""
        return normalize_finding_list_strict(v, dict_no_keys_to_str=False)


class LLMSynthesisResult(BaseModel):
    """LLM综合研判输出

    对应串标综合研判节点 (collusion.project_summary / collusion.overall_synthesis) 的输出结构。
    """
    overall_risk: str = Field(..., description="整体风险等级")
    suspected_groups: List["LLMSuspectedGroup"] = Field(
        default_factory=list,
        description="疑似串通组合列表"
    )
    collusion_network: Optional[str] = Field(
        default=None, description="串通网络描述"
    )
    bid_accompanying_pattern: Optional[str] = Field(
        default=None, description="陪标模式分析"
    )
    summary: str = Field(default="", description="综合结论摘要")
    investigation_suggestions: List[str] = Field(
        default_factory=list, description="调查建议"
    )

    @field_validator("investigation_suggestions", mode="before")
    @classmethod
    def coerce_investigation_suggestions(cls, v):
        return v if isinstance(v, list) else []

    @field_validator("overall_risk")
    @classmethod
    def validate_risk(cls, v: str) -> str:
        allowed = {"high", "medium", "low", "none", "inconclusive"}
        if v.lower() not in allowed:
            return "inconclusive"
        return v.lower()

    @field_validator("suspected_groups", mode="before")
    @classmethod
    def normalize_suspected_groups(cls, v: Any) -> list:
        if not isinstance(v, list):
            return []
        normalized = []
        for item in v:
            if isinstance(item, dict):
                normalized.append(item)
            elif isinstance(item, list):
                normalized.append({
                    "document_names": [str(x) for x in item if str(x).strip()],
                    "risk_level": "medium",
                    "reason": "模型返回了非结构化分组结果，已自动归一化。",
                })
            elif isinstance(item, str):
                normalized.append({
                    "document_names": [item],
                    "risk_level": "medium",
                    "reason": item,
                })
        return normalized


class LLMSuspectedGroup(BaseModel):
    """LLM综合研判中的疑似组合"""
    document_ids: List[int] = Field(default_factory=list, description="涉及文档ID")
    document_names: List[str] = Field(default_factory=list, description="涉及文档名称")
    risk_level: str = Field(default="medium", description="风险等级（high/medium/low）")
    reason: str = Field(default="", description="认定理由")

    @field_validator("document_ids", "document_names", mode="before")
    @classmethod
    def coerce_list_fields(cls, v):
        return v if isinstance(v, list) else []

    @field_validator("risk_level", mode="before")
    @classmethod
    def normalize_risk_level(cls, v: Any) -> str:
        risk = str(v or "medium").lower()
        return risk if risk in {"high", "medium", "low"} else "medium"


# ===========================================================================
# 全局特征 / 嫌疑群组 响应模型（多方群组识别）
# ===========================================================================

class GlobalFindingItem(BaseModel):
    """全局特征发现（人员重叠/企业关联/报价异常/元数据聚类/跨字段串联）"""
    type: str = Field(
        ...,
        description="发现类型: personnel_overlap/company_relation/pricing_pattern/metadata_cluster/cross_field_correlation",
    )
    involved_doc_ids: List[int] = Field(default_factory=list, description="涉及的文档ID列表")
    involved_names: List[str] = Field(default_factory=list, description="涉及的公司/文档名列表")
    description: str = Field(default="", description="发现描述")

    # 跨字段串联引擎扩展字段（仅 type=cross_field_correlation 时填充）
    risk_pattern: Optional[str] = Field(default=None, description="跨字段命中规则的业务标签")
    target_dim: Optional[str] = Field(default=None, description="归类的维度: D1-D8")
    level: Optional[str] = Field(default=None, description="证据级别: first/second/third")
    certainty: Optional[float] = Field(default=None, description="确定性 0-1")
    legal_reference: Optional[str] = Field(default=None, description="法规对照")
    matched_value: Optional[str] = Field(default=None, description="命中的归一化值")
    left_field: Optional[str] = Field(default=None, description="左侧字段路径")
    right_field: Optional[str] = Field(default=None, description="右侧字段路径")

    @field_validator("involved_doc_ids", "involved_names", mode="before")
    @classmethod
    def coerce_lists(cls, v):
        return v if isinstance(v, list) else []


class GroupRoleItem(BaseModel):
    """群组内成员角色"""
    doc_id: int = Field(..., description="文档ID")
    company_name: str = Field(default="", description="公司名称")
    role: str = Field(default="角色不明", description="角色: 主标方/陪标方/组织者/独立方/角色不明")
    role_evidence: str = Field(default="", description="角色判定依据（1句话）")


class CollusionGroupResult(BaseModel):
    """单个嫌疑群组的结果"""
    group_index: int = Field(..., description="群组序号")
    member_doc_ids: List[int] = Field(default_factory=list, description="成员文档ID列表")
    member_names: List[str] = Field(default_factory=list, description="成员公司/文档名列表")
    risk_level: str = Field(default="medium", description="群组风险等级")
    roles: List[GroupRoleItem] = Field(default_factory=list, description="成员角色分析")
    collusion_strategy: Optional[str] = Field(default=None, description="串通策略推断")
    key_evidence_summary: Optional[str] = Field(default=None, description="关键证据概述")
    investigation_focus: List[str] = Field(default_factory=list, description="调查建议")
    related_pair_ids: List[str] = Field(default_factory=list, description="关联的pair标识列表")
    network_description: Optional[str] = Field(default=None, description="串通网络描述")
    findings: List[FindingItem] = Field(default_factory=list, description="群组级融合证据")
    group_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="群组结论可信度")
    uncertain_edge_count: int = Field(default=0, description="群组内待复核边数")

    @field_validator(
        "member_doc_ids", "member_names", "roles",
        "investigation_focus", "related_pair_ids", "findings",
        mode="before",
    )
    @classmethod
    def coerce_lists(cls, v):
        return v if isinstance(v, list) else []

    @field_validator("risk_level", mode="before")
    @classmethod
    def normalize_risk(cls, v: Any) -> str:
        risk = str(v or "medium").lower()
        return risk if risk in {"high", "medium", "low"} else "medium"

    class Config:
        from_attributes = True


# ===========================================================================
# LLM 群组研判输出校验模型
# ===========================================================================

class LLMGroupRoleItem(BaseModel):
    """LLM群组研判 - 单成员角色"""
    doc_id: int = Field(..., description="文档ID")
    company_name: str = Field(default="", description="公司名称")
    role: str = Field(default="角色不明", description="角色判定")
    role_evidence: str = Field(default="", description="角色判定依据")


class LLMGroupAnalysisResult(BaseModel):
    """LLM群组研判输出（对应 COLLUSION_GROUP_ANALYSIS_PROMPT）"""
    group_risk: str = Field(default="medium", description="群组风险等级")
    roles: List[LLMGroupRoleItem] = Field(default_factory=list, description="成员角色列表")
    collusion_strategy: str = Field(default="", description="串通策略推断（限200字）")
    key_evidence_summary: str = Field(default="", description="关键证据概述")
    investigation_focus: List[str] = Field(default_factory=list, description="调查建议（最多5条）")

    @field_validator("roles", "investigation_focus", mode="before")
    @classmethod
    def coerce_lists(cls, v):
        return v if isinstance(v, list) else []

    @field_validator("group_risk", mode="before")
    @classmethod
    def normalize_risk(cls, v: Any) -> str:
        risk = str(v or "medium").lower()
        return risk if risk in {"high", "medium", "low", "none", "inconclusive"} else "medium"


# =============================================================================
# S1d 行业画像
# =============================================================================

class IndustryDomainItem(BaseModel):
    """行业画像中的单个内容域条目"""
    domain: str = Field(default="", description="内容域名称")
    reason: str = Field(default="", description="分类理由")


class IndustryQualItem(BaseModel):
    """行业通用资质/业绩条目"""
    name: str = Field(default="", description="资质或业绩类型名称")
    reason: str = Field(default="", description="为何该类型在本行业中普遍持有/常见")


class IndustryPricingPattern(BaseModel):
    """行业通用报价模式条目"""
    pattern: str = Field(default="", description="报价模式描述")
    reason: str = Field(default="", description="为何该模式属于行业常态")


class IndustrySignal(BaseModel):
    """从上传材料中抽取的行业信号"""
    signal_type: str = Field(default="", description="信号类型（domain/qualification/pricing/term/title等）")
    value: str = Field(default="", description="信号文本")
    source: str = Field(default="", description="来源类型（tender/bid_extraction/chunk_heading等）")
    source_refs: List[str] = Field(default_factory=list, description="来源定位（页码/章节/文档）")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="该信号自身置信度")

    @field_validator("source_refs", mode="before")
    @classmethod
    def coerce_refs(cls, v):
        return v if isinstance(v, list) else []


class LLMIndustrySignalResult(BaseModel):
    """LLM行业信号抽取输出"""
    confidence: str = Field(default="unknown", description="信号抽取整体置信度（high/medium/low/unknown）")
    signals: List[IndustrySignal] = Field(default_factory=list, description="材料中抽取的行业信号")
    summary: str = Field(default="", description="信号抽取摘要")

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, v: Any) -> str:
        conf = str(v or "unknown").lower()
        return conf if conf in {"high", "medium", "low", "unknown"} else "unknown"

    @field_validator("signals", mode="before")
    @classmethod
    def coerce_signals(cls, v):
        return v if isinstance(v, list) else []


class LLMIndustryProfileResult(BaseModel):
    """LLM行业画像输出（对应 COLLUSION_INDUSTRY_PROFILE_PROMPT_V2）"""
    industry_type: str = Field(default="", description="行业 + 项目细分类型")
    confidence: str = Field(default="unknown", description="画像置信度（high/medium/low/unknown）")
    summary: str = Field(default="", description="基于材料信号的行业归纳说明")
    standardized_domains: List[IndustryDomainItem] = Field(
        default_factory=list,
        description="标准化内容域：该行业中不同投标方自然相似的内容类型",
    )
    creative_domains: List[IndustryDomainItem] = Field(
        default_factory=list,
        description="个性化内容域：应展现差异性的内容类型",
    )
    common_qualification_types: List[IndustryQualItem] = Field(
        default_factory=list,
        description="行业通用资质/业绩类型：本行业投标方普遍持有的资质或常见业绩类型",
    )
    common_pricing_patterns: List[IndustryPricingPattern] = Field(
        default_factory=list,
        description="行业通用报价模式：本行业中报价结构/定价方式的常见模式",
    )
    source_mode: str = Field(default="material_only", description="画像来源模式（material_only/insufficient）")

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, v: Any) -> str:
        conf = str(v or "unknown").lower()
        return conf if conf in {"high", "medium", "low", "unknown"} else "unknown"
