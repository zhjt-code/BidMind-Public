"""LLM Center -- Registry Seed 脚本

幂等 + 增量：已有记录不覆盖，新增节点自动补充。
每个节点的 v1 版本从代码常量提取，source=code, status=published, is_active=True。

用法:
    cd backend
    python -m scripts.seed_llm_registry
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from loguru import logger
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

# 确保 backend/ 在 path 中
_backend_root = Path(__file__).resolve().parent.parent
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))


# ═══════════════════════════════════════════════
# 数据声明
# ═══════════════════════════════════════════════

@dataclass
class FlowDef:
    flow_key: str
    name: str
    description: str
    route_prefix: str
    display_order: int


@dataclass
class StageDef:
    flow_key: str
    stage_key: str
    name: str
    description: str = ""
    display_order: int = 0


@dataclass
class PromptRef:
    """从代码模块获取 Prompt 文本的引用"""
    module: str
    constant: str


@dataclass
class NodeDef:
    stage_key: str  # 需要组合 flow_key 来定位
    flow_key: str
    node_key: str
    name: str
    description: str = ""
    builder_key: str = ""
    source_file: str = ""
    execution_channel: str = "review"
    call_method: str = "chat_json"
    default_temperature: float = 0.1
    default_max_tokens: int = 2000
    output_schema_name: str = ""
    visibility: str = "main"
    editable_level: str = "advanced"
    display_order: int = 0
    system_prompt: Optional[PromptRef] = None
    user_prompt: Optional[PromptRef] = None
    node_class: str = ""


@dataclass
class EdgeDef:
    from_node_key: str
    to_node_key: str
    edge_type: str = "data_flow"
    description: str = ""


# ───────────────────────────────────────────────
# 三大流程
# ───────────────────────────────────────────────

FLOWS: list[FlowDef] = [
    FlowDef("writing", "标书编写", "基于招标约束自动生成投标文件内容", "/admin/llm-center/writing", 0),
    FlowDef("bid_review", "投标审查", "从招标文件提取要求，与投标响应逐项对比审查", "/admin/llm-center/bid-review", 1),
    FlowDef("collusion", "串标审查", "多份投标文件交叉对比，多维度识别串通投标行为", "/admin/llm-center/collusion", 2),
]

# ───────────────────────────────────────────────
# 阶段定义
# ───────────────────────────────────────────────

STAGES: list[StageDef] = [
    # ── 投标审查 ──
    StageDef("bid_review", "tender_extract", "招标要求提取", "从招标文件中提取结构化要求", 0),
    StageDef("bid_review", "bid_extract", "投标响应提取", "从投标文件中提取对应响应", 1),
    StageDef("bid_review", "review", "对比审查", "逐项对比招标要求与投标响应", 2),
    StageDef("bid_review", "image_recognition", "图片识别", "VL 模型识别投标文件中的图片内容", 3),
    # ── 串标审查 ──
    StageDef("collusion", "tender_format", "招标格式提取", "提取招标文件中的统一格式要求作为白名单", 0),
    StageDef("collusion", "extraction", "投标信息提取", "从投标文件中提取五路结构化信息", 1),
    StageDef("collusion", "comparison", "两两对比", "多维度交叉对比投标文件", 2),
    StageDef("collusion", "judgement", "文件对判定", "基于对比材料做串标嫌疑判定", 3),
    StageDef("collusion", "group_analysis", "群组研判", "分析嫌疑群组的内部关系", 4),
    StageDef("collusion", "synthesis", "综合结论", "项目级串标风险综合评估", 5),
    StageDef("collusion", "industry", "行业画像", "行业信号提取与画像归纳", 6),
    StageDef("collusion", "image_recognition", "图片识别", "VL 模型提取投标图片中的可比对事实", 7),
    # ── 标书编写 ──
    StageDef("writing", "clause_extract", "约束提取", "从招标文件提取三层编写约束", 0),
    StageDef("writing", "clause_classify", "约束分类", "对约束条目进行分类聚合", 1),
    StageDef("writing", "industry_profile", "行业画像", "汇总约束生成行业画像", 2),
    StageDef("writing", "outline_generate", "目录生成", "LLM 生成投标文件目录树", 3),
    StageDef("writing", "material_tag", "素材打标", "LLM 标注素材属性", 4),
    StageDef("writing", "material_match", "素材匹配", "约束-章节-素材三元匹配", 5),
    StageDef("writing", "content_generate", "内容生成", "三分法路由生成章节内容", 6),
    StageDef("writing", "quality_check", "质量检查", "一致性与交叉引用检查", 7),
]

# ───────────────────────────────────────────────
# Prompt 模块路径常量
# ───────────────────────────────────────────────

_P_TENDER = "app.core.ai.prompts.tender_extract"
_P_MANDATORY_ATOMIC = "app.core.ai.prompts.mandatory_atomic"
_P_BID = "app.core.ai.prompts.bid_extract"
_P_REVIEW = "app.core.ai.prompts.review"
_P_SCORING = "app.core.ai.prompts.scoring_extract"
_P_SCORING_REPAIR = "app.core.ai.prompts.scoring_repair"
_P_SCORING_FULL_CANDIDATE = "app.core.ai.prompts.scoring_full_candidate"
_P_SCHEME_READER = "app.core.ai.prompts.scheme_reader"
_P_SCORING_TABLE_STRUCTURE = "app.core.ai.prompts.scoring_table_structure"
_P_SCORING_TABLE_CANDIDATE = "app.core.ai.prompts.scoring_table_candidate"
_P_SCORING_VLM_RESCUE = "app.core.ai.prompts.scoring_vlm_rescue"
_P_SCORING_PDF_RESCUE = "app.core.ai.prompts.scoring_pdf_rescue"
_P_RELEVANCE = "app.core.ai.prompts.relevance_classify"
_P_IMAGE = "app.core.parser.image_analyzer"
_P_COLLUSION = "app.core.collusion.prompts"
# P1-1: 4 个非"对比/研判"节点的专用 system prompt 独立模块, 避免继续撑大 prompts.py
_P_COLLUSION_SYS = "app.core.collusion.system_prompts"
_P_W_CLAUSE = "app.core.writer.prompts.clause_extract"
_P_W_UNIFIED = "app.core.writer.prompts.clause_extract_unified"
_P_W_CLS_CL = "app.core.writer.prompts.clause_classify"
_P_W_IND = "app.core.writer.prompts.industry_profile"
_P_W_OUTLINE = "app.core.writer.prompts.outline_generate"
_P_W_OUTLINE_SCOPE = "app.core.writer.prompts.outline_scope"
_P_W_TAG = "app.core.writer.prompts.material_tag"
_P_W_IMG_CL = "app.core.writer.prompts.image_classify"
_P_W_MATCH = "app.core.writer.prompts.material_match"
_P_W_CHAP = "app.core.writer.prompts.chapter_profile"
_P_W_PAIR_JUDGE = "app.core.writer.prompts.material_pair_judge"
_P_W_REL = "app.core.writer.prompts.writing_relevance"
_P_W_COMPOSE = "app.core.writer.prompts.section_compose"
_P_W_PLAN = "app.core.writer.prompts.section_plan"
_P_W_VALIDATE = "app.core.writer.fixed_template.prompts"
_P_JUDGE = "app.core.ai.critic.prompts.judge_templates"

# ───────────────────────────────────────────────
# 节点定义 -- 投标审查
# ───────────────────────────────────────────────

_BID_REVIEW_NODES: list[NodeDef] = [
    NodeDef("tender_extract", "bid_review", "bid_review.tender_extract_page", "分页提取",
            "从招标文件单页提取结构化要求", "tender_extract.get_tender_extract_prompt",
            "core/ai/prompts/tender_extract.py", "review", "chat_json", 0.1, 4000, "",
            "main", "advanced", 0,
            PromptRef(_P_TENDER, "TENDER_EXTRACT_SYSTEM"), PromptRef(_P_TENDER, "TENDER_EXTRACT_USER"), node_class="mechanical"),
    NodeDef("tender_extract", "bid_review", "bid_review.mandatory_atomic_extract", "高风险原子条件召回",
            "从废标/资格/★等作用域逐条抽取可独立无效投标的条件",
            "mandatory_atomic.get_mandatory_atomic_prompt",
            "core/ai/prompts/mandatory_atomic.py", "review", "chat_json", 0.0, 4000, "",
            "main", "advanced", 1,
            PromptRef(_P_MANDATORY_ATOMIC, "MANDATORY_ATOMIC_SYSTEM"),
            PromptRef(_P_MANDATORY_ATOMIC, "MANDATORY_ATOMIC_USER"), node_class="mechanical"),
    NodeDef("tender_extract", "bid_review", "bid_review.tender_reflection", "反思校验",
            "去重质量审核", "tender_extract.get_reflection_prompt",
            "core/ai/prompts/tender_extract.py", "review", "chat_json", 0.1, 3000, "",
            "technical", "advanced", 2,
            PromptRef(_P_TENDER, "REFLECTION_SYSTEM"), PromptRef(_P_TENDER, "REFLECTION_USER"), node_class="mechanical"),
    NodeDef("tender_extract", "bid_review", "bid_review.category_merge_decision", "分类合并决策",
            "轻量分类合并决策", "tender_extract.get_category_merge_decision_prompt",
            "core/ai/prompts/tender_extract.py", "review", "chat_json", 0.1, 4000, "",
            "technical", "advanced", 4,
            PromptRef(_P_TENDER, "CATEGORY_MERGE_SYSTEM"), PromptRef(_P_TENDER, "CATEGORY_MERGE_DECISION_USER"), node_class="mechanical"),
    NodeDef("tender_extract", "bid_review", "bid_review.secondary_merge_decision", "二次合并决策",
            "轻量二次合并决策", "tender_extract.get_secondary_merge_decision_prompt",
            "core/ai/prompts/tender_extract.py", "review", "chat_json", 0.1, 4000, "",
            "technical", "advanced", 6,
            PromptRef(_P_TENDER, "TENDER_EXTRACT_SYSTEM"), PromptRef(_P_TENDER, "SECONDARY_MERGE_DECISION_USER"), node_class="mechanical"),
    NodeDef("bid_extract", "bid_review", "bid_review.bid_extract_match", "投标响应提取",
            "从投标文件中提取对应响应", "bid_extract.get_bid_extract_prompt",
            "core/ai/prompts/bid_extract.py", "review", "chat_json", 0.1, 4000, "",
            "main", "advanced", 0,
            PromptRef(_P_BID, "BID_EXTRACT_SYSTEM"), PromptRef(_P_BID, "BID_EXTRACT_USER"), node_class="mechanical"),
    # 预算 1500 → 3000。评分项审查在同一节点上额外要求 comment 200-400 字
    # 与 rule_assessments 数组, 输出量约为普通要求的 3~4 倍(实测普通要求 comment
    # 中位 113 字), 1500 下输出撞顶被截断致必填字段缺失、校验必败(实测评分项
    # 132/138 失败, 普通要求 0/289)。两个节点都注入 _scoring_supplement, 故同步上调。
    NodeDef("review", "bid_review", "bid_review.review_item", "逐项审查",
            "逐项对比招标要求与投标响应", "review.build_review_prompt",
            "core/ai/prompts/review.py", "review", "chat_json", 0.1, 3000, "",
            "main", "advanced", 0,
            PromptRef(_P_REVIEW, "REVIEW_SYSTEM_PROMPT"), PromptRef(_P_REVIEW, "REVIEW_PROMPT"), node_class="judgment"),
    NodeDef("review", "bid_review", "bid_review.review_no_response", "未响应评估",
            "无对应投标响应时的审查", "review.build_no_response_prompt",
            "core/ai/prompts/review.py", "review", "chat_json", 0.1, 3000, "",
            "main", "advanced", 1,
            PromptRef(_P_REVIEW, "REVIEW_SYSTEM_PROMPT"), PromptRef(_P_REVIEW, "NO_RESPONSE_PROMPT"), node_class="judgment"),
    NodeDef("image_recognition", "bid_review", "bid_review.image_recognition", "图片识别",
            "VL 模型识别投标文件图片内容", "",
            "core/parser/image_analyzer.py", "vision", "chat_with_images", 0.1, 2000, "",
            "technical", "advanced", 0,
            PromptRef(_P_IMAGE, "VL_SYSTEM_PROMPT"), None, node_class="judgment"),
    NodeDef("review", "bid_review", "document_qa.verify", "文档质检复核",
            "规则候选交提取池判断是否真实文档缺陷", "",
            "core/document_qa/verify/finding_verifier.py", "review", "chat_with_validation", 0.0, 1200, "",
            "technical", "advanced", 20,
            PromptRef("app.core.document_qa.verify.prompts", "VERIFY_SYSTEM"),
            PromptRef("app.core.document_qa.verify.prompts", "VERIFY_USER"),
            node_class="mechanical"),
    NodeDef("tender_extract", "bid_review", "bid_review.scoring_extract", "评分项提取",
            "从评分表整表提取结构化评分项（满分/分档/扣分规则）", "scoring_extract.get_scoring_extract_prompt",
            "core/ai/prompts/scoring_extract.py", "review", "chat_json", 0.1, 3000, "",
            "main", "advanced", 8,
            PromptRef(_P_SCORING, "SCORING_EXTRACT_SYSTEM"), PromptRef(_P_SCORING, "SCORING_EXTRACT_USER"), node_class="mechanical"),
    NodeDef("tender_extract", "bid_review", "bid_review.scoring_repair", "评分项修补",
            "评分合计校验失败时生成可锚定的增量修补建议", "scoring_repair.get_scoring_repair_prompt",
            "core/ai/prompts/scoring_repair.py", "review", "chat_json", 0.0, 2000, "",
            "technical", "advanced", 9,
            PromptRef(_P_SCORING_REPAIR, "SCORING_REPAIR_SYSTEM"), PromptRef(_P_SCORING_REPAIR, "SCORING_REPAIR_USER"), node_class="mechanical"),
    NodeDef("tender_extract", "bid_review", "bid_review.relevance_classify", "评审意义判定",
            "对合并后招标要求整表批量判定可评审性（actionable/informational/procedural）并识别含分值评分项",
            "relevance_classify.get_relevance_classify_prompt",
            "core/ai/prompts/relevance_classify.py", "review", "chat_json", 0.1, 3000, "",
            "main", "advanced", 10,
            PromptRef(_P_RELEVANCE, "RELEVANCE_CLASSIFY_SYSTEM"), PromptRef(_P_RELEVANCE, "RELEVANCE_CLASSIFY_USER"), node_class="mechanical"),
    NodeDef("tender_extract", "bid_review", "bid_review.scoring_full_candidate", "评分完整候选提取",
            "确定性提取与修补均未完成时，L3 全量候选 scope 完整提取（chat_with_validation）",
            "scoring_full_candidate.get_scoring_full_candidate_prompt",
            "core/ai/prompts/scoring_full_candidate.py", "review", "chat_with_validation", 0.0, 5000,
            "FullCandidateScope",
            "technical", "advanced", 11,
            PromptRef(_P_SCORING_FULL_CANDIDATE, "SCORING_FULL_CANDIDATE_SYSTEM"),
            PromptRef(_P_SCORING_FULL_CANDIDATE, "SCORING_FULL_CANDIDATE_USER"), node_class="mechanical"),
    NodeDef("tender_extract", "bid_review", "bid_review.scheme_reader", "评分结构判读",
            "装配前信号快照判读评分方案/卷边界，产出可锚定 SchemeMap（锚点核验+逐卷对账后才生效）",
            "scheme_reader.get_scheme_reader_prompt",
            "core/ai/prompts/scheme_reader.py", "review", "chat_json", 0.0, 2500, "",
            "technical", "advanced", 12,
            PromptRef(_P_SCHEME_READER, "SCHEME_READER_SYSTEM"),
            PromptRef(_P_SCHEME_READER, "SCHEME_READER_USER"), node_class="mechanical"),
    NodeDef("tender_extract", "bid_review", "bid_review.scoring_table_structure", "评分表跨页分组",
            "装配前物理表证据脱敏摘要判读跨页分组与方案/卷归属，仅输出对已有table_id的稳定ID引用",
            "scoring_table_structure.get_scoring_table_structure_prompt",
            "core/ai/prompts/scoring_table_structure.py", "review", "chat_json", 0.0, 2500, "",
            "technical", "advanced", 13,
            PromptRef(_P_SCORING_TABLE_STRUCTURE, "SCORING_TABLE_STRUCTURE_SYSTEM"),
            PromptRef(_P_SCORING_TABLE_STRUCTURE, "SCORING_TABLE_STRUCTURE_USER"), node_class="mechanical"),
    NodeDef("tender_extract", "bid_review", "bid_review.scoring_table_candidate", "评分逻辑表候选读取",
            "对单个逻辑表组的全部物理行判读角色/item_key/单元格与分值信号引用，仅输出稳定ID引用",
            "scoring_table_candidate.get_scoring_table_candidate_prompt",
            "core/ai/prompts/scoring_table_candidate.py", "review", "chat_json", 0.0, 8000, "",
            "technical", "advanced", 14,
            PromptRef(_P_SCORING_TABLE_CANDIDATE, "SCORING_TABLE_CANDIDATE_SYSTEM"),
            PromptRef(_P_SCORING_TABLE_CANDIDATE, "SCORING_TABLE_CANDIDATE_USER"), node_class="mechanical"),
    NodeDef("tender_extract", "bid_review", "bid_review.scoring_vlm_rescue", "评分视觉救援",
            "确定性链非绿时，评分区整页图片送 VLM 按层级本体提取评分细项（锚定+闭合双门禁后原子采纳）",
            "scoring_vlm_rescue.get_scoring_vlm_rescue_prompt",
            "core/ai/prompts/scoring_vlm_rescue.py", "vision", "chat_with_images", 0.0, 6000, "",
            "technical", "advanced", 15,
            PromptRef(_P_SCORING_VLM_RESCUE, "SCORING_VLM_RESCUE_SYSTEM"),
            PromptRef(_P_SCORING_VLM_RESCUE, "SCORING_VLM_RESCUE_USER"), node_class="judgment"),
    NodeDef("tender_extract", "bid_review", "bid_review.scoring_pdf_rescue", "评分PDF直读救援",
            "确定性链非绿时，评分区子PDF直传支持文件输入的模型按层级本体提取评分细项（安全网第一梯队，先于VLM；共用锚定+闭合双门禁与verdict终点）",
            "scoring_pdf_rescue.get_scoring_pdf_rescue_prompt",
            "core/ai/prompts/scoring_pdf_rescue.py", "review", "chat_with_pdf", 0.0, 6000, "",
            "technical", "advanced", 16,
            PromptRef(_P_SCORING_PDF_RESCUE, "SCORING_PDF_RESCUE_SYSTEM"),
            PromptRef(_P_SCORING_PDF_RESCUE, "SCORING_PDF_RESCUE_USER"), node_class="judgment"),
]

# ───────────────────────────────────────────────
# 节点定义 -- 串标审查
# ───────────────────────────────────────────────

_COLLUSION_NODES: list[NodeDef] = [
    # P1-1: 抽取类节点专用 system prompt（角色定位 = "客观抽取员"，避免"找异常"思维偏向）
    NodeDef("tender_format", "collusion", "collusion.tender_format_extract", "招标格式提取",
            "提取招标文件中的统一格式要求", "",
            "core/collusion/prompts.py", "review", "chat_with_validation", 0.1, 3000, "",
            "main", "advanced", 0,
            PromptRef(_P_COLLUSION_SYS, "COLLUSION_TENDER_FORMAT_SYSTEM_PROMPT"), PromptRef(_P_COLLUSION, "COLLUSION_TENDER_FORMAT_PROMPT"), node_class="mechanical"),
    NodeDef("extraction", "collusion", "collusion.route_extract", "五路提取",
            "五路并行提取 (entity/pricing/qualification/technical/evidence)", "",
            "core/collusion/prompts.py", "review", "chat_with_validation", 0.1, 4000, "",
            "technical", "advanced", 1,
            PromptRef(_P_COLLUSION_SYS, "COLLUSION_EXTRACT_ROUTE_SYSTEM_PROMPT"), PromptRef(_P_COLLUSION, "COLLUSION_EXTRACT_ROUTE_PROMPT"), node_class="mechanical"),
    NodeDef("comparison", "collusion", "collusion.pair_compare_entity", "企业信息对比",
            "分组对比: 企业与人员信息", "",
            "core/collusion/prompts.py", "review", "chat_with_validation", 0.1, 4000, "",
            "main", "advanced", 1,
            PromptRef(_P_COLLUSION, "COLLUSION_SYSTEM_PROMPT"), PromptRef(_P_COLLUSION, "COLLUSION_ENTITY_PROMPT"), node_class="judgment"),
    NodeDef("comparison", "collusion", "collusion.pair_compare_text", "文本模式对比",
            "分组对比: 文本模式分析", "",
            "core/collusion/prompts.py", "review", "chat_with_validation", 0.1, 4000, "",
            "main", "advanced", 2,
            PromptRef(_P_COLLUSION, "COLLUSION_SYSTEM_PROMPT"), PromptRef(_P_COLLUSION, "COLLUSION_TEXT_PATTERN_PROMPT"), node_class="judgment"),
    NodeDef("comparison", "collusion", "collusion.pair_compare_business", "商务信息对比",
            "分组对比: 商务与报价", "",
            "core/collusion/prompts.py", "review", "chat_with_validation", 0.1, 4000, "",
            "main", "advanced", 3,
            PromptRef(_P_COLLUSION, "COLLUSION_SYSTEM_PROMPT"), PromptRef(_P_COLLUSION, "COLLUSION_BUSINESS_PROMPT"), node_class="judgment"),
    NodeDef("comparison", "collusion", "collusion.single_dim_search", "单维深搜",
            "对高相似度维度进行单维度深度分析", "",
            "core/collusion/prompts.py", "review", "chat_with_validation", 0.1, 3000, "",
            "technical", "advanced", 4,
            PromptRef(_P_COLLUSION, "COLLUSION_SYSTEM_PROMPT"), PromptRef(_P_COLLUSION, "COLLUSION_SINGLE_DIM_PROMPT"), node_class="judgment"),
    NodeDef("judgement", "collusion", "collusion.pair_judgement", "文件对判定",
            "基于整理的材料事实做串标嫌疑判定", "",
            "core/collusion/prompts.py", "review", "chat_with_validation", 0.1, 4000, "",
            "main", "advanced", 0,
            PromptRef(_P_COLLUSION, "COLLUSION_SYSTEM_PROMPT"), PromptRef(_P_COLLUSION, "COLLUSION_PAIR_JUDGEMENT_PROMPT"), node_class="judgment"),
    NodeDef("group_analysis", "collusion", "collusion.group_analysis", "群组研判",
            "分析嫌疑串通群组内部关系和角色分工", "",
            "core/collusion/prompts.py", "review", "chat_with_validation", 0.1, 4000, "",
            "main", "advanced", 0,
            PromptRef(_P_COLLUSION, "COLLUSION_SYSTEM_PROMPT"), PromptRef(_P_COLLUSION, "COLLUSION_GROUP_ANALYSIS_PROMPT"), node_class="judgment"),
    NodeDef("synthesis", "collusion", "collusion.project_summary", "项目摘要",
            "生成项目级串标综合结论", "",
            "core/collusion/prompts.py", "review", "chat_with_validation", 0.1, 4000, "",
            "technical", "advanced", 0,
            PromptRef(_P_COLLUSION, "COLLUSION_SYSTEM_PROMPT"), PromptRef(_P_COLLUSION, "COLLUSION_PROJECT_SUMMARY_PROMPT"), node_class="mechanical"),
    # P0-1: overall_synthesis 与 project_summary 不再复用同一模板;
    # 有群组路径专用 COLLUSION_OVERALL_SYNTHESIS_PROMPT (明示 groups_summary JSON 结构),
    # 无群组路径仍走 project_summary -> COLLUSION_PROJECT_SUMMARY_PROMPT。
    NodeDef("synthesis", "collusion", "collusion.overall_synthesis", "全局综合",
            "基于群组研判和全局数据给出整体风险评估", "",
            "core/collusion/prompts.py", "review", "chat_with_validation", 0.1, 4000, "",
            "main", "advanced", 1,
            PromptRef(_P_COLLUSION, "COLLUSION_SYSTEM_PROMPT"), PromptRef(_P_COLLUSION, "COLLUSION_OVERALL_SYNTHESIS_PROMPT"), node_class="judgment"),
    # P1-1: 行业归纳类节点专用 system prompt（角色定位 = "行业归纳分析员"，禁用预置行业先验）
    NodeDef("industry", "collusion", "collusion.industry_signal", "行业信号提取",
            "从上传材料中抽取行业信号", "",
            "core/collusion/prompts.py", "review", "chat_with_validation", 0.1, 3000, "",
            "technical", "advanced", 0,
            PromptRef(_P_COLLUSION_SYS, "COLLUSION_INDUSTRY_SIGNAL_SYSTEM_PROMPT"), PromptRef(_P_COLLUSION, "COLLUSION_INDUSTRY_SIGNAL_PROMPT"), node_class="mechanical"),
    NodeDef("industry", "collusion", "collusion.industry_profile_gen", "行业画像归纳",
            "基于行业信号归纳项目行业画像", "",
            "core/collusion/prompts.py", "review", "chat_with_validation", 0.1, 3000, "",
            "technical", "advanced", 1,
            PromptRef(_P_COLLUSION_SYS, "COLLUSION_INDUSTRY_PROFILE_SYSTEM_PROMPT"), PromptRef(_P_COLLUSION, "COLLUSION_INDUSTRY_PROFILE_PROMPT_V2"), node_class="judgment"),
    NodeDef("image_recognition", "collusion", "collusion.image_recognition", "图片识别",
            "VL 模型提取投标图片中的可比对事实", "",
            "core/parser/image_analyzer.py", "vision", "chat_with_images", 0.1, 2000, "",
            "technical", "advanced", 0,
            PromptRef(_P_IMAGE, "COLLUSION_IMAGE_RECOGNITION_SYSTEM"), None, node_class="judgment"),
]

# ───────────────────────────────────────────────
# 节点定义 -- 标书编写
# ───────────────────────────────────────────────

_WRITING_NODES: list[NodeDef] = [
    NodeDef("clause_extract", "writing", "writing.hard_clause", "硬约束提取",
            "识别废标条款/固定模板/格式规定/提交规则", "clause_extract.get_hard_constraint_prompt",
            "core/writer/prompts/clause_extract.py", "review", "chat_json", 0.1, 4000, "ExtractedClause",
            "technical", "advanced", 0,
            PromptRef(_P_W_CLAUSE, "HARD_CONSTRAINT_SYSTEM"), PromptRef(_P_W_CLAUSE, "HARD_CONSTRAINT_USER"), node_class="mechanical"),
    NodeDef("clause_extract", "writing", "writing.structure_clause", "结构约束提取",
            "识别目录规则/必备材料/内容要求", "clause_extract.get_structure_constraint_prompt",
            "core/writer/prompts/clause_extract.py", "review", "chat_json", 0.1, 4000, "ExtractedClause",
            "technical", "advanced", 1,
            PromptRef(_P_W_CLAUSE, "STRUCTURE_CONSTRAINT_SYSTEM"), PromptRef(_P_W_CLAUSE, "STRUCTURE_CONSTRAINT_USER"), node_class="mechanical"),
    NodeDef("clause_extract", "writing", "writing.guide_clause", "生成引导提取",
            "识别评分标准/项目背景/差异化提示", "clause_extract.get_generation_guide_prompt",
            "core/writer/prompts/clause_extract.py", "review", "chat_json", 0.1, 4000, "ExtractedClause",
            "technical", "advanced", 2,
            PromptRef(_P_W_CLAUSE, "GENERATION_GUIDE_SYSTEM"), PromptRef(_P_W_CLAUSE, "GENERATION_GUIDE_USER"), node_class="mechanical"),
    NodeDef("clause_extract", "writing", "writing.unified_clause", "三层合并约束提取",
            "单次过正文同时提取 hard/structure/guide",
            "clause_extract_unified.get_unified_clause_extract_prompt",
            "core/writer/prompts/clause_extract_unified.py", "review", "chat_json", 0.1, 6000, "",
            "technical", "advanced", 4,
            PromptRef(_P_W_UNIFIED, "UNIFIED_CLAUSE_EXTRACT_SYSTEM"),
            PromptRef(_P_W_UNIFIED, "UNIFIED_CLAUSE_EXTRACT_USER"), node_class="mechanical"),
    NodeDef("clause_extract", "writing", "writing.render_config", "渲染配置提取",
            "从版面要求中提取排版参数", "clause_extract.get_render_profile_prompt",
            "core/writer/prompts/clause_extract.py", "review", "chat_json", 0.1, 3000, "RenderProfileParsed",
            "technical", "advanced", 3,
            PromptRef(_P_W_CLAUSE, "RENDER_PROFILE_SYSTEM"), PromptRef(_P_W_CLAUSE, "RENDER_PROFILE_USER"), node_class="mechanical"),
    NodeDef("clause_extract", "writing", "writing.clause_dedup_decision", "去重决策",
            "轻量去重决策判定", "clause_extract.get_dedup_decision_prompt",
            "core/writer/prompts/clause_extract.py", "review", "chat_json", 0.1, 3000, "ClauseMergeDecision",
            "technical", "advanced", 5,
            PromptRef(_P_W_CLAUSE, "DEDUP_DECISION_SYSTEM"), PromptRef(_P_W_CLAUSE, "DEDUP_DECISION_USER"), node_class="mechanical"),
    NodeDef("clause_extract", "writing", "writing.bid_structure_analysis", "投标结构分析",
            "基于招标约束分析投标文件结构", "clause_extract.get_bid_structure_analysis_prompt",
            "core/writer/prompts/clause_extract.py", "review", "chat_json", 0.1, 4000, "BidStructureAnalysis",
            "technical", "advanced", 6,
            PromptRef(_P_W_CLAUSE, "BID_STRUCTURE_ANALYSIS_SYSTEM"), PromptRef(_P_W_CLAUSE, "BID_STRUCTURE_ANALYSIS_USER"), node_class="mechanical"),
    NodeDef("clause_extract", "writing", "writing.bid_directory_extract", "投标目录提取",
            "从招标文件识别目录结构", "clause_extract.get_bid_directory_extract_prompt",
            "core/writer/prompts/clause_extract.py", "review", "chat_json", 0.1, 4000, "BidDirectorySpec",
            "technical", "advanced", 7,
            PromptRef(_P_W_CLAUSE, "BID_DIRECTORY_EXTRACT_SYSTEM"), PromptRef(_P_W_CLAUSE, "BID_DIRECTORY_EXTRACT_USER"), node_class="mechanical"),
    NodeDef("clause_extract", "writing", "writing.fixed_template_recognize", "固定模板识别",
            "对招标文件候选页一次性识别全部固定模板, 输出模板目录数组",
            "fixed_template.llm_recognizer.recognize_templates",
            "core/writer/fixed_template/llm_recognizer.py", "review", "chat_json", 0.1, 6000, "FixedTemplateCatalog",
            "technical", "advanced", 8,
            PromptRef(_P_W_VALIDATE, "FIXED_TEMPLATE_RECOGNIZE_SYSTEM"),
            PromptRef(_P_W_VALIDATE, "FIXED_TEMPLATE_RECOGNIZE_USER"), node_class="mechanical"),
    NodeDef("clause_classify", "writing", "writing.clause_classify", "约束分类",
            "对提取的约束条目进行分类聚合", "material_matcher.classify_clauses",
            "core/writer/prompts/clause_classify.py", "review", "chat_json", 0.1, 4000, "",
            "main", "advanced", 0,
            PromptRef(_P_W_CLS_CL, "CLAUSE_CLASSIFY_SYSTEM"), PromptRef(_P_W_CLS_CL, "CLAUSE_CLASSIFY_USER"), node_class="mechanical"),
    NodeDef("clause_classify", "writing", "writing.relevance_classify", "写作消费分级",
            "判定约束是否进入正文匹配/生成, 或折叠为参考/检查型红线", "writing_relevance.classify_writing_relevance",
            "core/writer/prompts/writing_relevance.py", "review", "chat_json", 0.1, 2000, "",
            "technical", "advanced", 1,
            PromptRef(_P_W_REL, "WRITING_RELEVANCE_SYSTEM"), PromptRef(_P_W_REL, "WRITING_RELEVANCE_USER"), node_class="mechanical"),
    NodeDef("industry_profile", "writing", "writing.industry_profile", "行业画像",
            "汇总约束生成行业画像引导内容风格", "",
            "core/writer/prompts/industry_profile.py", "review", "chat", 0.2, 4000, "",
            "technical", "advanced", 0,
            PromptRef(_P_W_IND, "INDUSTRY_PROFILE_SYSTEM"), PromptRef(_P_W_IND, "INDUSTRY_PROFILE_USER"), node_class="judgment"),
    NodeDef("outline_generate", "writing", "writing.skeleton_generate", "骨架生成",
            "LLM 生成投标文件目录骨架", "",
            "core/writer/prompts/outline_generate.py", "review", "chat_json", 0.1, 8000, "",
            "main", "advanced", 0,
            PromptRef(_P_W_OUTLINE, "OUTLINE_GENERATE_SYSTEM"), PromptRef(_P_W_OUTLINE, "OUTLINE_GENERATE_USER"), node_class="judgment"),
    NodeDef("outline_generate", "writing", "writing.section_fill", "章节填充",
            "向骨架章节填充详细子节点", "",
            "core/writer/prompts/outline_generate.py", "review", "chat_json", 0.1, 6000, "",
            "technical", "advanced", 1,
            PromptRef(_P_W_OUTLINE, "SECTION_FILL_SYSTEM"), PromptRef(_P_W_OUTLINE, "SECTION_FILL_USER"), node_class="judgment"),
    NodeDef("outline_generate", "writing", "writing.skeleton_enrich", "骨架充实",
            "充实骨架节点的标签和内容指引", "",
            "core/writer/prompts/outline_generate.py", "review", "chat_json", 0.1, 6000, "",
            "technical", "advanced", 2,
            PromptRef(_P_W_OUTLINE, "SKELETON_ENRICH_SYSTEM"), PromptRef(_P_W_OUTLINE, "SKELETON_ENRICH_USER"), node_class="judgment"),
    NodeDef("outline_generate", "writing", "writing.outline_scope_titles", "实质内容标题抽取",
            "按当前投标章节检索招标原文并抽取可挂目录的纲要标题", "",
            "core/writer/prompts/outline_scope.py", "review", "chat_json", 0.1, 2000, "",
            "technical", "advanced", 3,
            PromptRef(_P_W_OUTLINE_SCOPE, "SCOPE_TITLE_SYSTEM"), PromptRef(_P_W_OUTLINE_SCOPE, "SCOPE_TITLE_USER"), node_class="mechanical"),
    NodeDef("material_tag", "writing", "writing.material_tag", "素材打标",
            "LLM 标注素材的 domain/entities/applicable_sections", "",
            "core/writer/prompts/material_tag.py", "review", "chat_json", 0.1, 1000, "",
            "main", "advanced", 0,
            PromptRef(_P_W_TAG, "MATERIAL_TAG_SYSTEM"), PromptRef(_P_W_TAG, "MATERIAL_TAG_USER"), node_class="mechanical"),
    NodeDef("material_tag", "writing", "writing.image_classify", "图片分类",
            "VL 分类投标素材图片", "",
            "core/writer/prompts/image_classify.py", "review", "chat_json", 0.1, 1500, "",
            "technical", "advanced", 1,
            PromptRef(_P_W_IMG_CL, "IMAGE_CLASSIFY_SYSTEM"), PromptRef(_P_W_IMG_CL, "IMAGE_CLASSIFY_USER"), node_class="mechanical"),
    NodeDef("material_match", "writing", "writing.chapter_judge", "章节匹配裁决",
            "判断素材是否属于当前章节", "",
            "core/writer/prompts/material_match.py", "review", "chat_json", 0.1, 4000, "",
            "technical", "advanced", 0,
            PromptRef(_P_W_MATCH, "CHAPTER_JUDGE_SYSTEM"), PromptRef(_P_W_MATCH, "CHAPTER_JUDGE_USER"), node_class="mechanical"),
    NodeDef("material_match", "writing", "writing.chapter_profile", "章节画像",
            "生成章节内容画像用于匹配", "",
            "core/writer/prompts/chapter_profile.py", "review", "chat_json", 0.1, 8000, "",
            "technical", "advanced", 2,
            PromptRef(_P_W_CHAP, "CHAPTER_PROFILE_SYSTEM"), PromptRef(_P_W_CHAP, "CHAPTER_PROFILE_USER"), node_class="mechanical"),
    NodeDef("material_match", "writing", "writing.material_pair_judge", "素材句对裁决",
            "对素材-章节候选对做蕴含裁决(supports/partial/unrelated/conflict)", "",
            "core/writer/prompts/material_pair_judge.py", "review", "chat_with_validation", 0.0, 3000,
            "MaterialPairJudgeBatch",
            "technical", "advanced", 1,
            PromptRef(_P_W_PAIR_JUDGE, "MATERIAL_PAIR_JUDGE_SYSTEM"), PromptRef(_P_W_PAIR_JUDGE, "MATERIAL_PAIR_JUDGE_USER"), node_class="judgment"),
    NodeDef("content_generate", "writing", "writing.data_fill", "资料填充",
            "结构化数据轻量 AI 填充", "",
            "core/writer/prompts/section_compose.py", "review", "chat_json", 0.1, 2000, "",
            "main", "admin_only", 0,
            PromptRef(_P_W_COMPOSE, "DATA_FILL_SYSTEM"), PromptRef(_P_W_COMPOSE, "DATA_FILL_USER"), node_class="judgment"),
    NodeDef("content_generate", "writing", "writing.section_plan", "章节论证计划",
            "为自由论述章节生成评分目标、论点、措施、证据和验收标准计划", "",
            "core/writer/prompts/section_plan.py", "review", "chat_with_validation", 0.0, 3000, "SectionPlanDraft",
            "technical", "admin_only", 1,
            PromptRef(_P_W_PLAN, "SECTION_PLAN_SYSTEM"), PromptRef(_P_W_PLAN, "SECTION_PLAN_USER"), node_class="judgment"),
    NodeDef("content_generate", "writing", "writing.free_writing", "自由论述",
            "完整 AI 生成技术/服务/管理方案", "",
            "core/writer/prompts/section_compose.py", "review", "chat_json", 0.2, 4000, "",
            "main", "admin_only", 2,
            PromptRef(_P_W_COMPOSE, "FREE_WRITING_SYSTEM"), PromptRef(_P_W_COMPOSE, "FREE_WRITING_USER"), node_class="judgment"),
    NodeDef("content_generate", "writing", "writing.section_summary", "章节摘要",
            "用 1-2 句话概括章节核心内容", "",
            "core/writer/prompts/section_compose.py", "review", "chat", 0.1, 500, "",
            "technical", "admin_only", 3,
            PromptRef(_P_W_COMPOSE, "SUMMARIZE_SYSTEM"), PromptRef(_P_W_COMPOSE, "SUMMARIZE_USER"), node_class="mechanical"),
    NodeDef("content_generate", "writing", "writing.length_adjust", "章节长度校正",
            "W1: 生成后字数偏离建议篇幅动态区间时的一次定向扩写/压缩", "",
            "core/writer/prompts/section_compose.py", "review", "chat", 0.2, 4000, "",
            "technical", "admin_only", 4,
            PromptRef(_P_W_COMPOSE, "LENGTH_ADJUST_SYSTEM"), PromptRef(_P_W_COMPOSE, "LENGTH_ADJUST_USER"), node_class="mechanical"),
    NodeDef("quality_check", "writing", "writing.cross_ref_check", "交叉引用检查",
            "检查投标文件中的交叉引用是否正确", "",
            "core/writer/prompts/section_compose.py", "review", "chat_json", 0.1, 2000, "",
            "technical", "admin_only", 0,
            PromptRef(_P_W_COMPOSE, "CROSS_REF_CHECK_SYSTEM"), PromptRef(_P_W_COMPOSE, "CROSS_REF_CHECK_USER"), node_class="mechanical"),
]

# ───────────────────────────────────────────────
# 节点定义 -- Critic / Judge
# ───────────────────────────────────────────────

_JUDGE_NODES: list[NodeDef] = [
    NodeDef("review", "bid_review", "judge.review", "投标审查 Judge 复核",
            "复核投标审查结论是否合理", "judge.run_judge",
            "core/ai/critic/prompts/judge_templates.py", "review", "chat_json", 0.0, 500, "JudgeVerdict",
            "technical", "advanced", 10,
            PromptRef(_P_JUDGE, "JUDGE_REVIEW_SYSTEM"), PromptRef(_P_JUDGE, "JUDGE_REVIEW_USER"), node_class="judgment"),
    NodeDef("review", "bid_review", "judge.generic", "通用 Judge 复核",
            "未知 flow 的 Judge 兜底复核模板", "judge.run_judge",
            "core/ai/critic/prompts/judge_templates.py", "review", "chat_json", 0.0, 500, "JudgeVerdict",
            "technical", "advanced", 11,
            PromptRef(_P_JUDGE, "JUDGE_GENERIC_SYSTEM"), PromptRef(_P_JUDGE, "JUDGE_GENERIC_USER"), node_class="judgment"),
    NodeDef("judgement", "collusion", "judge.collusion", "串标审查 Judge 复核",
            "复核串标维度或文件对判定是否合理", "judge.run_judge",
            "core/ai/critic/prompts/judge_templates.py", "review", "chat_json", 0.0, 500, "JudgeVerdict",
            "technical", "advanced", 10,
            PromptRef(_P_JUDGE, "JUDGE_COLLUSION_SYSTEM"), PromptRef(_P_JUDGE, "JUDGE_COLLUSION_USER"), node_class="judgment"),
    NodeDef("quality_check", "writing", "judge.writer", "标书编写 Judge 复核",
            "复核生成章节是否忠实素材并符合约束", "judge.run_judge",
            "core/ai/critic/prompts/judge_templates.py", "review", "chat_json", 0.0, 800, "JudgeVerdict",
            "technical", "advanced", 10,
            PromptRef(_P_JUDGE, "JUDGE_WRITER_SYSTEM"), PromptRef(_P_JUDGE, "JUDGE_WRITER_USER"), node_class="judgment"),
]

ALL_NODES = _BID_REVIEW_NODES + _COLLUSION_NODES + _WRITING_NODES + _JUDGE_NODES


# ───────────────────────────────────────────────
# 已废弃节点 (DB 侧 soft-deactivate, 不再出现在 LLM Center UI / 运行时快照)
# ───────────────────────────────────────────────
# 这些节点曾经在 seed 登记, 但业务代码中已被等价的 *_decision 轻量模式或
# 其它新节点替代, 业务运行时不会调用。为保留 DB 历史版本方便审计/回滚,
# 不做物理删除, 仅把 LlmNode.is_active 置为 False, 并级联写一条 audit 日志。
DEPRECATED_NODE_KEYS: list[str] = [
    # 被 *_decision 轻量模式替代
    "bid_review.tender_merge",
    "bid_review.category_merge",
    "bid_review.secondary_merge",
    "writing.clause_dedup",
    "writing.fixed_template_validate",
    # 被 route_extract 替代的旧通用提取
    "collusion.general_extract",
    # 被分组对比 (pair_compare_*) 替代的旧全维度对比
    "collusion.full_compare",
    # 被直接 pair_judgement 链路替代的中间综合节点
    "collusion.pair_synthesis",
    # 代码中从未调用的功能节点
    "collusion.pricing_analysis",
    "writing.match_judge",
]

# ───────────────────────────────────────────────
# 核心边定义（主要数据流）
# ───────────────────────────────────────────────

EDGES: list[EdgeDef] = [
    # 投标审查
    EdgeDef("bid_review.tender_extract_page", "bid_review.tender_reflection", "data_flow", "分页结果 → 反思"),
    EdgeDef("bid_review.tender_extract_page", "bid_review.mandatory_atomic_extract", "data_flow", "分页提取 → 高风险原子条件召回"),
    EdgeDef("bid_review.mandatory_atomic_extract", "bid_review.bid_extract_match", "data_flow", "否决/资格原子条件 → 投标响应提取"),
    EdgeDef("bid_review.tender_reflection", "bid_review.category_merge_decision", "conditional", "质量不达标 → 分类合并决策"),
    EdgeDef("bid_review.tender_reflection", "bid_review.secondary_merge_decision", "conditional", "数量超阈值 → 二次合并决策"),
    EdgeDef("bid_review.tender_extract_page", "bid_review.bid_extract_match", "data_flow", "招标要求 → 投标响应提取"),
    EdgeDef("bid_review.bid_extract_match", "bid_review.review_item", "data_flow", "投标响应 → 审查"),
    EdgeDef("bid_review.bid_extract_match", "bid_review.review_no_response", "conditional", "无响应 → 未响应评估"),
    EdgeDef("bid_review.scoring_extract", "bid_review.scoring_repair", "conditional", "提取未完成 → 修补"),
    EdgeDef("bid_review.scoring_repair", "bid_review.scoring_full_candidate", "conditional", "修补未完成 → 完整候选提取"),
    EdgeDef("bid_review.scoring_full_candidate", "bid_review.scoring_pdf_rescue", "conditional", "救援尾部仍非绿 → PDF直读救援(第一梯队)"),
    EdgeDef("bid_review.scoring_pdf_rescue", "bid_review.scoring_vlm_rescue", "conditional", "PDF直读不支持/未转绿 → VLM视觉救援(第二梯队)"),
    EdgeDef("bid_review.scoring_extract", "bid_review.scheme_reader", "data_flow", "评分项产物 → 结构判读贴标（校验前）"),
    EdgeDef("bid_review.scheme_reader", "bid_review.scoring_repair", "conditional", "贴标对账后仍有纯缺口 → L2 修补"),
    # 串标审查
    EdgeDef("collusion.tender_format_extract", "collusion.route_extract", "data_flow", "白名单 → 五路提取"),
    EdgeDef("collusion.route_extract", "collusion.pair_compare_entity", "data_flow", "提取结果 → 分组对比"),
    EdgeDef("collusion.pair_compare_entity", "collusion.pair_judgement", "data_flow", "对比结果 → 判定"),
    EdgeDef("collusion.pair_judgement", "collusion.group_analysis", "data_flow", "判定 → 群组研判"),
    EdgeDef("collusion.group_analysis", "collusion.overall_synthesis", "data_flow", "群组 → 全局综合"),
    # 标书编写
    EdgeDef("writing.hard_clause", "writing.clause_dedup_decision", "data_flow", "约束 → 去重"),
    EdgeDef("writing.unified_clause", "writing.clause_dedup_decision", "data_flow", "单次过正文约束 → 去重"),
    EdgeDef("writing.clause_dedup_decision", "writing.bid_structure_analysis", "data_flow", "去重 → 结构分析"),
    EdgeDef("writing.bid_structure_analysis", "writing.skeleton_generate", "data_flow", "结构 → 骨架"),
    EdgeDef("writing.skeleton_generate", "writing.section_fill", "data_flow", "骨架 → 填充"),
    EdgeDef("writing.skeleton_enrich", "writing.outline_scope_titles", "data_flow", "骨架丰富 → 实质内容标题挂载"),
    EdgeDef("writing.section_fill", "writing.material_tag", "data_flow", "目录 → 素材打标"),
    EdgeDef("writing.material_tag", "writing.chapter_judge", "data_flow", "打标 → 匹配"),
    EdgeDef("writing.chapter_judge", "writing.material_pair_judge", "data_flow", "章节匹配 → 素材句对裁决"),
    EdgeDef("writing.material_pair_judge", "writing.data_fill", "data_flow", "素材绑定 → 内容生成"),
    EdgeDef("writing.chapter_judge", "writing.data_fill", "data_flow", "绑定 → 内容生成"),
    EdgeDef("writing.material_pair_judge", "writing.section_plan", "data_flow", "素材绑定 → 章节论证计划"),
    EdgeDef("writing.section_plan", "writing.free_writing", "data_flow", "论证计划 → 自由论述"),
    EdgeDef("writing.free_writing", "writing.length_adjust", "conditional", "字数偏离动态区间 → 长度校正"),
    EdgeDef("writing.data_fill", "writing.cross_ref_check", "data_flow", "内容 → 质量检查"),
    EdgeDef("writing.free_writing", "writing.cross_ref_check", "data_flow", "内容 → 质量检查"),
]


# ───────────────────────────────────────────────
# 参数定义 (node_key -> list of param dicts)
# ───────────────────────────────────────────────

@dataclass
class ParamDef:
    node_key: str
    param_key: str
    direction: str  # input / output / runtime
    data_type: str = "string"
    is_required: bool = True
    description: str = ""
    source_type: str = "manual"
    source_hint: str = ""

def _runtime_params(node_key: str) -> list[ParamDef]:
    """每个节点通用的运行时参数"""
    return [
        ParamDef(node_key, "temperature", "runtime", "float", False, "采样温度", "system"),
        ParamDef(node_key, "max_tokens", "runtime", "int", False, "最大输出 token", "system"),
    ]

PARAM_DEFS: list[ParamDef] = [
    # ── 投标审查 ──
    ParamDef("bid_review.tender_extract_page", "content", "input", "string", True, "招标文件单页内容", "system"),
    ParamDef("bid_review.tender_extract_page", "page_num", "input", "int", False, "页码", "system"),
    ParamDef("bid_review.tender_extract_page", "category_descriptions", "input", "string", False, "分类说明", "system"),
    ParamDef("bid_review.tender_extract_page", "requirements", "output", "json", True, "提取的招标要求数组"),
    ParamDef("bid_review.mandatory_atomic_extract", "content", "input", "string", True, "高风险作用域原文", "system"),
    ParamDef("bid_review.mandatory_atomic_extract", "hints", "input", "string", False, "须被覆盖的废标/★信号", "system"),
    ParamDef("bid_review.mandatory_atomic_extract", "items", "output", "json", True, "原子废标/无效投标条件数组"),
    ParamDef("bid_review.tender_reflection", "requirements", "input", "json", True, "待审核的要求列表", "upstream"),
    ParamDef("bid_review.tender_reflection", "quality_issues", "output", "json", True, "质量问题清单"),
    ParamDef("bid_review.bid_extract_match", "requirement_id", "input", "string", True, "招标要求 ID", "upstream"),
    ParamDef("bid_review.bid_extract_match", "category", "input", "string", True, "要求分类码", "upstream"),
    ParamDef("bid_review.bid_extract_match", "requirement_description", "input", "string", True, "要求描述", "upstream"),
    ParamDef("bid_review.bid_extract_match", "is_mandatory", "input", "bool", False, "是否为否决项(prompt 渲染为 '是'/'否')", "upstream"),
    ParamDef("bid_review.bid_extract_match", "bid_chunks", "input", "json", True, "检索到的投标文件片段", "system"),
    ParamDef("bid_review.bid_extract_match", "found", "output", "bool", True, "是否找到响应"),
    ParamDef("bid_review.bid_extract_match", "response_content", "output", "string", False, "投标响应正文"),
    ParamDef("bid_review.bid_extract_match", "source_pages", "output", "string", False, "响应来源页码"),
    ParamDef("bid_review.review_item", "requirement_id", "input", "string", True, "招标要求 ID", "upstream"),
    ParamDef("bid_review.review_item", "category", "input", "string", True, "要求分类码(英文枚举,写入 JSON category 字段)", "upstream"),
    ParamDef("bid_review.review_item", "category_label", "input", "string", True, "要求分类中文标签(展示行用)", "upstream"),
    ParamDef("bid_review.review_item", "requirement_description", "input", "string", True, "招标要求描述", "upstream"),
    ParamDef("bid_review.review_item", "response_content", "input", "string", True, "投标响应正文", "upstream"),
    ParamDef("bid_review.review_item", "is_mandatory", "input", "bool", False, "是否为否决项(prompt 渲染为 '是'/'否')", "upstream"),
    ParamDef("bid_review.review_item", "response_id", "input", "string", False, "投标响应 ID(无响应时渲染为 'null')", "upstream"),
    ParamDef("bid_review.review_item", "evidence_type", "input", "string", False, "证明材料类型: certificate/proposal/commitment/reference/other", "upstream"),
    ParamDef("bid_review.review_item", "source_pages", "input", "string", False, "响应来源页码(逗号分隔)", "upstream"),
    ParamDef("bid_review.review_item", "completeness", "input", "string", False, "响应完整性: full/partial/form_only/unknown", "upstream"),
    ParamDef("bid_review.review_item", "missing_elements", "input", "string", False, "缺失要素列表(预渲染文本, 顿号分隔)", "upstream"),
    ParamDef("bid_review.review_item", "linked_to", "input", "string", False, "关联到其它要求 ID(已淡化, 通常为 'null')", "upstream"),
    ParamDef("bid_review.review_item", "evidence_anchor", "input", "string", False, "证据锚点(合同号/审计号等)", "upstream"),
    ParamDef("bid_review.review_item", "relevance_score", "input", "string", False, "提取阶段相关性分数(0-100 或 '未知')", "upstream"),
    ParamDef("bid_review.review_item", "relevance_note", "input", "string", False, "提取阶段相关性说明", "upstream"),
    ParamDef("bid_review.review_item", "compliance_level", "output", "string", True, "符合程度: fully/partially/not"),
    ParamDef("bid_review.review_item", "score", "output", "int", True, "0-100 评分"),
    ParamDef("bid_review.review_item", "risk_level", "output", "string", True, "风险等级: high/medium/low/none"),
    ParamDef("bid_review.review_item", "comment", "output", "string", True, "详细审查意见"),
    ParamDef("bid_review.review_no_response", "requirement_id", "input", "string", True, "招标要求 ID", "upstream"),
    ParamDef("bid_review.review_no_response", "category", "input", "string", True, "要求分类码(英文枚举,写入 JSON category 字段)", "upstream"),
    ParamDef("bid_review.review_no_response", "category_label", "input", "string", True, "要求分类中文标签(展示行用)", "upstream"),
    ParamDef("bid_review.review_no_response", "requirement_description", "input", "string", True, "招标要求描述", "upstream"),
    ParamDef("bid_review.review_no_response", "is_mandatory", "input", "bool", False, "是否为否决项", "upstream"),
    ParamDef("bid_review.scoring_extract", "content", "input", "string", True, "评分表整表内容（可能跨页拼接）", "system"),
    ParamDef("bid_review.scoring_extract", "scoring_items", "output", "json", True, "提取的评分项数组（含满分/分档/扣分规则）"),
    ParamDef("bid_review.scoring_repair", "scoring_content", "input", "string", True, "评分表原文（预算截断后）", "system"),
    ParamDef("bid_review.scoring_repair", "context_text", "input", "string", False, "邻近评分说明文字", "system"),
    ParamDef("bid_review.scoring_repair", "existing_items", "input", "json", True, "已提取评分项 JSON", "upstream"),
    ParamDef("bid_review.scoring_repair", "diagnostics", "input", "json", True, "评分合计校验诊断 JSON", "upstream"),
    ParamDef("bid_review.scoring_repair", "repair_patches", "output", "json", True, "可锚定的评分项增量修补补丁"),
    ParamDef("bid_review.scoring_full_candidate", "target_scheme", "input", "string", True, "目标评分方案名称（scheme）", "upstream"),
    ParamDef("bid_review.scoring_full_candidate", "target_lot", "input", "string", True, "目标采购包/标段（lot）", "upstream"),
    ParamDef("bid_review.scoring_full_candidate", "scoring_basis", "input", "string", True, "评分基准（总分或分卷期望）", "upstream"),
    ParamDef("bid_review.scoring_full_candidate", "existing_items", "input", "json", False, "已提取评分项 JSON（可参考）", "upstream"),
    ParamDef("bid_review.scoring_full_candidate", "volume_defs", "input", "json", False, "卷定义列表（系统权威声明，不可新增）", "upstream"),
    ParamDef("bid_review.scoring_full_candidate", "diagnostics", "input", "json", False, "评分校验诊断 JSON", "upstream"),
    ParamDef("bid_review.scoring_full_candidate", "evidence_units", "input", "string", True, "证据单元渲染文本（含 chunk_id/page/kind/内容）", "system"),
    ParamDef("bid_review.scoring_full_candidate", "candidate_scope", "output", "json", True, "完整候选 FullCandidateScope JSON"),
    ParamDef("bid_review.scheme_reader", "signals_block", "input", "string", True, "评分结构信号快照渲染文本（声明句/标题样行/表头/合计行/行骨架，带页与行锚点）", "system"),
    ParamDef("bid_review.scheme_reader", "feedback_block", "input", "string", False, "上一轮判读的逐卷对账差异反馈（重判环第2轮起注入）", "upstream"),
    ParamDef("bid_review.scheme_reader", "scheme_map", "output", "json", True, "SchemeMap JSON（方案列表/卷基准/表行区间归属/锚点/置信度）"),
    ParamDef("bid_review.scoring_table_structure", "table_summaries", "input", "string", True, "装配前物理表脱敏摘要渲染文本（table_id/page/table_index、首尾行截断预览、全部分值信号ref=value）", "system"),
    ParamDef("bid_review.scoring_table_structure", "document_structure", "output", "json", True, "DocumentStructureCandidate JSON（物理table_id到logical_table_key/scheme_key/volume的分组分配）"),
    ParamDef("bid_review.scoring_table_candidate", "logical_table_evidence", "input", "string", True, "单个逻辑表组全部物理行渲染文本（row_id、非空cell_ref:text、该行分值信号ref=value）", "upstream"),
    ParamDef("bid_review.scoring_table_candidate", "table_candidate", "output", "json", True, "ScoringTableCandidate JSON（逐行role/item_key/单元格与分值信号引用、声明合计）"),
    ParamDef("bid_review.scoring_vlm_rescue", "page_hint", "input", "string", True, "图片序与PDF页码对应关系提示文本", "system"),
    ParamDef("bid_review.scoring_vlm_rescue", "image_count", "input", "string", True, "本批图片张数", "system"),
    ParamDef("bid_review.scoring_vlm_rescue", "page_start", "input", "string", True, "本批起始PDF页码", "system"),
    ParamDef("bid_review.scoring_vlm_rescue", "page_end", "input", "string", True, "本批结束PDF页码", "system"),
    ParamDef("bid_review.scoring_vlm_rescue", "scoring_items_jsonl", "output", "json", True, "JSON Lines：首行header（declared_total/volume_totals），其后逐行评分细项（含group_path层级路径）"),
    ParamDef("bid_review.scoring_pdf_rescue", "page_hint", "input", "string", True, "子PDF页序与原文档页码对应关系提示文本（含多标段/多方案页映射）", "system"),
    ParamDef("bid_review.scoring_pdf_rescue", "page_count", "input", "string", True, "子PDF总页数", "system"),
    ParamDef("bid_review.scoring_pdf_rescue", "page_start", "input", "string", True, "子PDF对应的原文档起始页码", "system"),
    ParamDef("bid_review.scoring_pdf_rescue", "page_end", "input", "string", True, "子PDF对应的原文档结束页码", "system"),
    ParamDef("bid_review.scoring_pdf_rescue", "scoring_items_jsonl", "output", "json", True, "JSON Lines：首行header（declared_total/volume_totals），其后逐行评分细项（含group_path层级路径）"),
    ParamDef("bid_review.relevance_classify", "requirements_json", "input", "string", True, "待判定的招标要求列表JSON（每条含 idx/category/is_mandatory/description）", "upstream"),
    ParamDef("bid_review.relevance_classify", "relevance_result", "output", "json", True, "逐条评审意义判定结果（idx/relevance/locus 证据载体/is_scoring/max_score）"),
    # ── 串标审查（主要节点） ──
    # 同源修复：input ParamDef 必须与 app.core.collusion.prompts 的 {占位符}
    # 完全对齐，否则 LLM Center validate_service 会抛 KeyError。
    # 模板共享占位符（doc_a_name 等）按节点独立声明，保持 PARAM_DEFS 现有声明性结构。

    # tender_format_extract: 招标格式提取（COLLUSION_TENDER_FORMAT_PROMPT）
    ParamDef("collusion.tender_format_extract", "tender_text", "input", "string", True, "招标文件原文", "system"),
    ParamDef("collusion.tender_format_extract", "format_requirements", "output", "json", True, "统一格式要求（白名单）"),

    # route_extract: 五路提取（COLLUSION_EXTRACT_ROUTE_PROMPT）
    ParamDef("collusion.route_extract", "doc_name", "input", "string", True, "投标文件名", "upstream"),
    ParamDef("collusion.route_extract", "doc_text", "input", "string", True, "投标文档文本(预渲染)", "upstream"),
    ParamDef("collusion.route_extract", "route_name", "input", "string", True, "提取路线名(entity/business/text 等)", "system"),
    ParamDef("collusion.route_extract", "focus_label", "input", "string", True, "聚焦字段标签", "system"),
    ParamDef("collusion.route_extract", "focus_instruction", "input", "string", True, "聚焦字段提取指令", "system"),
    ParamDef("collusion.route_extract", "target_fields", "input", "string", True, "目标字段 schema 列表", "system"),

    # pair_compare_entity: 企业信息对比（COLLUSION_ENTITY_PROMPT = _GROUPED_PROMPT_HEADER + ...）
    ParamDef("collusion.pair_compare_entity", "doc_a_id", "input", "string", True, "文档 A 的 doc_id", "upstream"),
    ParamDef("collusion.pair_compare_entity", "doc_a_name", "input", "string", True, "文档 A 文件名", "upstream"),
    ParamDef("collusion.pair_compare_entity", "doc_b_id", "input", "string", True, "文档 B 的 doc_id", "upstream"),
    ParamDef("collusion.pair_compare_entity", "doc_b_name", "input", "string", True, "文档 B 文件名", "upstream"),
    ParamDef("collusion.pair_compare_entity", "similarity_pct", "input", "string", True, "整体向量相似度(预渲染百分比数值)", "upstream"),
    ParamDef("collusion.pair_compare_entity", "coverage_pct", "input", "string", True, "相似度覆盖率(预渲染百分比数值)", "upstream"),
    ParamDef("collusion.pair_compare_entity", "max_continuous_span", "input", "string", True, "最长连续相似 span 长度", "upstream"),
    ParamDef("collusion.pair_compare_entity", "tender_context", "input", "string", False, "招标格式要求上下文(可选, 为空时渲染为空串)", "upstream"),
    ParamDef("collusion.pair_compare_entity", "structured_a", "input", "string", True, "文档 A 结构化数据 JSON", "upstream"),
    ParamDef("collusion.pair_compare_entity", "structured_b", "input", "string", True, "文档 B 结构化数据 JSON", "upstream"),
    ParamDef("collusion.pair_compare_entity", "context_a", "input", "string", True, "文档 A 自由文本上下文", "upstream"),
    ParamDef("collusion.pair_compare_entity", "context_b", "input", "string", True, "文档 B 自由文本上下文", "upstream"),
    ParamDef("collusion.pair_compare_entity", "comparison_result", "output", "json", True, "对比结果"),

    # pair_compare_text: 文本模式对比（COLLUSION_TEXT_PATTERN_PROMPT）
    # P1-2: 独立 industry_profile_context / kb_context 占位符,
    # 与 pair_compare_business 设计风格统一, 避免 LLM 看到的内容标签错位。
    ParamDef("collusion.pair_compare_text", "doc_a_name", "input", "string", True, "文档 A 文件名", "upstream"),
    ParamDef("collusion.pair_compare_text", "doc_b_name", "input", "string", True, "文档 B 文件名", "upstream"),
    ParamDef("collusion.pair_compare_text", "similarity_pct", "input", "string", True, "整体向量相似度(预渲染百分比数值)", "upstream"),
    ParamDef("collusion.pair_compare_text", "coverage_pct", "input", "string", True, "相似度覆盖率(预渲染百分比数值)", "upstream"),
    ParamDef("collusion.pair_compare_text", "max_continuous_span", "input", "string", True, "最长连续相似 span 长度", "upstream"),
    ParamDef("collusion.pair_compare_text", "tender_context", "input", "string", False, "招标格式要求上下文(可选)", "upstream"),
    ParamDef("collusion.pair_compare_text", "industry_profile_context", "input", "string", False, "行业画像上下文(可选, 与 pair_compare_business 一致)", "upstream"),
    ParamDef("collusion.pair_compare_text", "kb_context", "input", "string", False, "弱知识库白名单提示(可选)", "upstream"),
    ParamDef("collusion.pair_compare_text", "similar_chunks", "input", "string", True, "高相似度 chunk 对的预渲染列表", "upstream"),

    # pair_compare_business: 商务信息对比（COLLUSION_BUSINESS_PROMPT = _GROUPED_PROMPT_HEADER + ...）
    ParamDef("collusion.pair_compare_business", "doc_a_id", "input", "string", True, "文档 A 的 doc_id", "upstream"),
    ParamDef("collusion.pair_compare_business", "doc_a_name", "input", "string", True, "文档 A 文件名", "upstream"),
    ParamDef("collusion.pair_compare_business", "doc_b_id", "input", "string", True, "文档 B 的 doc_id", "upstream"),
    ParamDef("collusion.pair_compare_business", "doc_b_name", "input", "string", True, "文档 B 文件名", "upstream"),
    ParamDef("collusion.pair_compare_business", "similarity_pct", "input", "string", True, "整体向量相似度(预渲染百分比数值)", "upstream"),
    ParamDef("collusion.pair_compare_business", "coverage_pct", "input", "string", True, "相似度覆盖率(预渲染百分比数值)", "upstream"),
    ParamDef("collusion.pair_compare_business", "max_continuous_span", "input", "string", True, "最长连续相似 span 长度", "upstream"),
    ParamDef("collusion.pair_compare_business", "tender_context", "input", "string", False, "招标格式要求上下文(可选)", "upstream"),
    ParamDef("collusion.pair_compare_business", "industry_profile_context", "input", "string", False, "行业画像上下文(可选)", "upstream"),
    ParamDef("collusion.pair_compare_business", "structured_a", "input", "string", True, "文档 A 商务结构化数据 JSON", "upstream"),
    ParamDef("collusion.pair_compare_business", "structured_b", "input", "string", True, "文档 B 商务结构化数据 JSON", "upstream"),
    ParamDef("collusion.pair_compare_business", "context_a", "input", "string", True, "文档 A 商务自由文本上下文", "upstream"),
    ParamDef("collusion.pair_compare_business", "context_b", "input", "string", True, "文档 B 商务自由文本上下文", "upstream"),

    # single_dim_search: 单维深搜（COLLUSION_SINGLE_DIM_PROMPT）
    ParamDef("collusion.single_dim_search", "doc_a_id", "input", "string", True, "文档 A 的 doc_id", "upstream"),
    ParamDef("collusion.single_dim_search", "doc_a_name", "input", "string", True, "文档 A 文件名", "upstream"),
    ParamDef("collusion.single_dim_search", "doc_b_id", "input", "string", True, "文档 B 的 doc_id", "upstream"),
    ParamDef("collusion.single_dim_search", "doc_b_name", "input", "string", True, "文档 B 文件名", "upstream"),
    ParamDef("collusion.single_dim_search", "similarity_pct", "input", "string", True, "整体向量相似度(预渲染百分比数值)", "upstream"),
    ParamDef("collusion.single_dim_search", "tender_context", "input", "string", False, "招标格式要求上下文(可选)", "upstream"),
    ParamDef("collusion.single_dim_search", "dim_label", "input", "string", True, "维度标签", "system"),
    ParamDef("collusion.single_dim_search", "dim_instruction", "input", "string", True, "维度提取指令", "system"),
    ParamDef("collusion.single_dim_search", "context_a", "input", "string", True, "文档 A 该维度的上下文", "upstream"),
    ParamDef("collusion.single_dim_search", "context_b", "input", "string", True, "文档 B 该维度的上下文", "upstream"),

    # pair_judgement: 文件对判定（COLLUSION_PAIR_JUDGEMENT_PROMPT）
    ParamDef("collusion.pair_judgement", "doc_a_id", "input", "string", True, "文档 A 的 doc_id", "upstream"),
    ParamDef("collusion.pair_judgement", "doc_a_name", "input", "string", True, "文档 A 文件名", "upstream"),
    ParamDef("collusion.pair_judgement", "doc_b_id", "input", "string", True, "文档 B 的 doc_id", "upstream"),
    ParamDef("collusion.pair_judgement", "doc_b_name", "input", "string", True, "文档 B 文件名", "upstream"),
    ParamDef("collusion.pair_judgement", "similarity_pct", "input", "string", True, "整体向量相似度(预渲染百分比数值)", "upstream"),
    ParamDef("collusion.pair_judgement", "tender_context", "input", "string", False, "招标格式要求上下文(可选)", "upstream"),
    ParamDef("collusion.pair_judgement", "industry_profile_context", "input", "string", False, "行业画像上下文(可选)", "upstream"),
    ParamDef("collusion.pair_judgement", "pair_facts", "input", "string", True, "各维度对比材料预渲染文本", "upstream"),
    ParamDef("collusion.pair_judgement", "risk_level", "output", "string", True, "嫌疑等级"),
    ParamDef("collusion.pair_judgement", "findings", "output", "json", True, "判定发现"),

    # group_analysis: 群组研判（COLLUSION_GROUP_ANALYSIS_PROMPT）
    ParamDef("collusion.group_analysis", "member_list", "input", "string", True, "群组成员列表(预渲染)", "upstream"),
    ParamDef("collusion.group_analysis", "aggregated_evidence", "input", "string", True, "群组内对比证据聚合", "upstream"),
    ParamDef("collusion.group_analysis", "global_findings", "input", "string", True, "全局 findings 摘要", "upstream"),
    ParamDef("collusion.group_analysis", "group_findings", "output", "json", True, "群组研判结果"),

    # industry_signal: 行业信号提取（COLLUSION_INDUSTRY_SIGNAL_PROMPT）
    ParamDef("collusion.industry_signal", "project_context", "input", "string", True, "项目上下文摘要", "upstream"),

    # industry_profile_gen: 行业画像归纳（COLLUSION_INDUSTRY_PROFILE_PROMPT_V2）
    ParamDef("collusion.industry_profile_gen", "industry_signals", "input", "string", True, "行业信号汇总 JSON", "upstream"),
    # collusion.overall_synthesis 与 collusion.project_summary 运行时复用同一组 .format() 参数;
    # 占位符集合必须与 analyzer._render_overall_user_message / _render_user_message 一致, 否则
    # 会在 str.format 阶段抛 KeyError (历史上曾因残留 {bid_count} 触发)。
    ParamDef("collusion.overall_synthesis", "pair_judgements", "input", "string", True, "各对判定摘要(由 _build_pair_summaries 生成)", "upstream"),
    ParamDef("collusion.overall_synthesis", "groups_summary", "input", "string", True, "群组摘要 JSON (含 group_count/ungrouped_pairs/global_stats)", "upstream"),
    ParamDef("collusion.overall_synthesis", "coverage_summary", "input", "string", True, "覆盖率与质量信息 JSON", "upstream"),
    ParamDef("collusion.overall_synthesis", "profile_quality_summary", "input", "string", True, "行业画像质量信息 JSON", "upstream"),
    ParamDef("collusion.overall_synthesis", "global_findings_context", "input", "string", True, "全局 findings 精选文本（由 _build_global_findings_context 生成，灰度控制）", "upstream"),
    ParamDef("collusion.overall_synthesis", "overall_risk", "output", "string", True, "项目整体风险等级"),
    ParamDef("collusion.overall_synthesis", "summary", "output", "string", True, "综合结论摘要"),
    ParamDef("collusion.overall_synthesis", "suspected_groups", "output", "json", False, "疑似串通组合列表"),
    ParamDef("collusion.overall_synthesis", "collusion_network", "output", "string", False, "串通网络描述"),
    ParamDef("collusion.overall_synthesis", "bid_accompanying_pattern", "output", "string", False, "陪标模式分析"),
    ParamDef("collusion.overall_synthesis", "investigation_suggestions", "output", "json", False, "调查建议列表"),
    ParamDef("collusion.project_summary", "pair_judgements", "input", "string", True, "各对判定摘要(由 _build_pair_summaries 生成)", "upstream"),
    ParamDef("collusion.project_summary", "groups_summary", "input", "string", True, "群组摘要或 '(无嫌疑群组)'", "upstream"),
    ParamDef("collusion.project_summary", "coverage_summary", "input", "string", True, "覆盖率与质量信息 JSON", "upstream"),
    ParamDef("collusion.project_summary", "profile_quality_summary", "input", "string", True, "行业画像质量信息 JSON", "upstream"),
    ParamDef("collusion.project_summary", "global_findings_context", "input", "string", True, "全局 findings 精选文本（由 _build_global_findings_context 生成，灰度控制）", "upstream"),
    ParamDef("collusion.project_summary", "overall_risk", "output", "string", True, "项目整体风险等级"),
    ParamDef("collusion.project_summary", "summary", "output", "string", True, "综合结论摘要"),
    ParamDef("collusion.project_summary", "suspected_groups", "output", "json", False, "疑似串通组合列表"),
    ParamDef("collusion.project_summary", "collusion_network", "output", "string", False, "串通网络描述"),
    ParamDef("collusion.project_summary", "bid_accompanying_pattern", "output", "string", False, "陪标模式分析"),
    ParamDef("collusion.project_summary", "investigation_suggestions", "output", "json", False, "调查建议列表"),
    # ── 标书编写（主要节点） ──
    ParamDef("writing.hard_clause", "content", "input", "string", True, "招标文件分块内容", "system"),
    ParamDef("writing.hard_clause", "page_start", "input", "int", False, "起始页码", "system"),
    ParamDef("writing.hard_clause", "page_end", "input", "int", False, "结束页码", "system"),
    ParamDef("writing.hard_clause", "clauses", "output", "json", True, "提取的硬约束数组"),
    ParamDef("writing.hard_clause", "metadata", "output", "json", False, "硬约束结构化补充信息"),
    ParamDef("writing.structure_clause", "content", "input", "string", True, "招标文件分块内容", "system"),
    ParamDef("writing.structure_clause", "page_start", "input", "int", False, "起始页码", "system"),
    ParamDef("writing.structure_clause", "page_end", "input", "int", False, "结束页码", "system"),
    ParamDef("writing.structure_clause", "clauses", "output", "json", True, "提取的结构约束数组"),
    ParamDef("writing.structure_clause", "metadata", "output", "json", False, "材料/内容要求结构化补充信息"),
    ParamDef("writing.guide_clause", "content", "input", "string", True, "招标文件分块内容", "system"),
    ParamDef("writing.guide_clause", "page_start", "input", "int", False, "起始页码", "system"),
    ParamDef("writing.guide_clause", "page_end", "input", "int", False, "结束页码", "system"),
    ParamDef("writing.guide_clause", "clauses", "output", "json", True, "提取的生成引导数组"),
    ParamDef("writing.guide_clause", "metadata", "output", "json", False, "评分标准/写作引导结构化补充信息"),
    ParamDef("writing.unified_clause", "content", "input", "string", True, "招标文件分块内容", "system"),
    ParamDef("writing.unified_clause", "page_start", "input", "int", False, "起始页码", "system"),
    ParamDef("writing.unified_clause", "page_end", "input", "int", False, "结束页码", "system"),
    ParamDef("writing.unified_clause", "hard", "output", "json", True, "硬约束数组"),
    ParamDef("writing.unified_clause", "structure", "output", "json", True, "结构约束数组"),
    ParamDef("writing.unified_clause", "guide", "output", "json", True, "生成引导数组"),
    ParamDef("writing.render_config", "format_rules_text", "input", "string", True, "格式规定约束文本", "upstream"),
    ParamDef("writing.render_config", "render_profile", "output", "json", True, "字体/字号/边距/行距等渲染配置"),
    ParamDef("writing.render_config", "submission_requirements", "output", "json", False, "提交/装订/签章/暗标等格式风险"),
    ParamDef("writing.bid_structure_analysis", "directory_rules", "input", "string", False, "目录规则摘要", "upstream"),
    ParamDef("writing.bid_structure_analysis", "content_requirements", "input", "string", False, "内容要求摘要", "upstream"),
    ParamDef("writing.bid_structure_analysis", "material_requirements", "input", "string", False, "必备材料摘要", "upstream"),
    ParamDef("writing.bid_structure_analysis", "scoring_criteria", "input", "string", False, "评分标准摘要", "upstream"),
    ParamDef("writing.bid_structure_analysis", "hard_structure_signals", "input", "string", False, "固定模板/提交规则/格式规定中的分卷线索", "upstream"),
    ParamDef("writing.bid_structure_analysis", "industry_profile", "input", "string", False, "行业画像", "upstream"),
    ParamDef("writing.bid_structure_analysis", "structure_analysis", "output", "json", True, "投标文件分卷结构分析结果"),
    ParamDef("writing.bid_directory_extract", "content", "input", "string", True, "目录相关招标原文片段", "upstream"),
    ParamDef("writing.bid_directory_extract", "directory_spec", "output", "json", True, "招标文件指定目录结构"),
    ParamDef("writing.bid_directory_extract", "node_type_source", "output", "string", False, "目录节点类型建议来源"),
    ParamDef("writing.clause_classify", "clauses_text", "input", "string", True, "待分类约束列表", "upstream"),
    ParamDef("writing.clause_classify", "outline_candidates_text", "input", "string", False, "真实目录章节与分卷候选", "upstream"),
    ParamDef("writing.clause_classify", "scope_map", "output", "json", True, "约束作用域分类结果"),
    ParamDef("writing.relevance_classify", "clauses_text", "input", "string", True, "待写作消费分级的约束列表", "upstream"),
    ParamDef("writing.relevance_classify", "writing_relevance", "output", "json", True, "direct/reference 分级结果"),
    ParamDef("writing.industry_profile", "project_context", "input", "string", False, "项目背景约束摘要", "upstream"),
    ParamDef("writing.industry_profile", "scoring_summary", "input", "string", False, "评分标准摘要", "upstream"),
    ParamDef("writing.industry_profile", "content_requirements", "input", "string", False, "技术/内容要求摘要", "upstream"),
    ParamDef("writing.industry_profile", "material_requirements", "input", "string", False, "必备材料摘要", "upstream"),
    ParamDef("writing.industry_profile", "differentiation_hints", "input", "string", False, "差异化提示摘要", "upstream"),
    ParamDef("writing.industry_profile", "directory_rules", "input", "string", False, "目录/结构规则摘要", "upstream"),
    ParamDef("writing.industry_profile", "structured_signals", "input", "string", False, "结构化 metadata 摘要", "upstream"),
    ParamDef("writing.industry_profile", "profile_text", "output", "string", True, "行业画像正文"),
    ParamDef("writing.skeleton_generate", "directory_rules", "input", "string", False, "目录规则", "upstream"),
    ParamDef("writing.skeleton_generate", "content_requirements", "input", "string", False, "内容要求", "upstream"),
    ParamDef("writing.skeleton_generate", "material_requirements", "input", "string", False, "必备材料", "upstream"),
    ParamDef("writing.skeleton_generate", "scoring_criteria", "input", "string", False, "评分标准", "upstream"),
    ParamDef("writing.skeleton_generate", "industry_profile", "input", "string", False, "行业画像", "upstream"),
    ParamDef("writing.skeleton_generate", "outline_tree", "output", "json", True, "目录树结构"),
    ParamDef("writing.section_fill", "section_title", "input", "string", True, "当前分卷/章节标题", "upstream"),
    ParamDef("writing.section_fill", "section_category", "input", "string", True, "当前分卷类别", "upstream"),
    ParamDef("writing.section_fill", "section_description", "input", "string", False, "当前分卷定位描述", "derived"),
    ParamDef("writing.section_fill", "relevant_dir_rules", "input", "string", False, "当前分卷相关目录规则", "upstream"),
    ParamDef("writing.section_fill", "relevant_content_reqs", "input", "string", False, "当前分卷相关内容要求", "upstream"),
    ParamDef("writing.section_fill", "relevant_scoring", "input", "string", False, "当前分卷相关评分标准", "upstream"),
    ParamDef("writing.section_fill", "relevant_materials", "input", "string", False, "当前分卷相关必备材料", "upstream"),
    ParamDef("writing.section_fill", "industry_profile", "input", "string", False, "行业画像", "upstream"),
    ParamDef("writing.section_fill", "structure_mode", "input", "string", False, "投标文件结构模式", "upstream"),
    ParamDef("writing.section_fill", "section_policy", "input", "string", False, "结构模式驱动的分卷约束说明", "derived"),
    ParamDef("writing.section_fill", "outline_children", "output", "json", True, "当前分卷子目录树"),
    ParamDef("writing.skeleton_enrich", "node_title", "input", "string", True, "当前骨架章节标题", "upstream"),
    ParamDef("writing.skeleton_enrich", "node_order", "input", "int", True, "骨架章节原始序号", "upstream"),
    ParamDef("writing.skeleton_enrich", "volume_title", "input", "string", True, "所属分卷标题", "upstream"),
    ParamDef("writing.skeleton_enrich", "sibling_titles_text", "input", "string", False, "同卷其他骨架章节", "upstream"),
    ParamDef("writing.skeleton_enrich", "relevant_scoring", "input", "string", False, "相关评分标准", "upstream"),
    ParamDef("writing.skeleton_enrich", "relevant_content_reqs", "input", "string", False, "相关内容要求", "upstream"),
    ParamDef("writing.skeleton_enrich", "relevant_materials", "input", "string", False, "相关必备材料", "upstream"),
    ParamDef("writing.skeleton_enrich", "project_context", "input", "string", False, "项目工作内容/背景", "upstream"),
    ParamDef("writing.skeleton_enrich", "outline_children", "output", "json", True, "补充后的子目录数组"),
    ParamDef("writing.outline_scope_titles", "scope_text", "input", "string", True, "与当前投标章节相关的招标原文", "upstream"),
    ParamDef("writing.outline_scope_titles", "chapter_title", "input", "string", False, "当前投标目录章节标题", "upstream"),
    ParamDef("writing.outline_scope_titles", "scope_titles", "output", "json", True, "纲要级实质内容标题数组"),
    ParamDef("writing.material_tag", "title", "input", "string", True, "素材标题", "upstream"),
    ParamDef("writing.material_tag", "material_type", "input", "string", True, "素材类型", "upstream"),
    ParamDef("writing.material_tag", "content_preview", "input", "string", True, "素材内容摘要", "upstream"),
    ParamDef("writing.material_tag", "outline_tree", "input", "string", False, "当前标书目录树", "upstream"),
    ParamDef("writing.material_tag", "resolved_label", "input", "string", False, "标签决议器产出的最终业务类型", "upstream"),
    ParamDef("writing.material_tag", "tags", "output", "json", True, "打标结果"),
    ParamDef("writing.material_tag", "confidence", "output", "float", False, "打标置信度 0-1"),
    ParamDef("writing.material_tag", "evidence_source", "output", "string", False, "标签依据来源"),
    ParamDef("writing.material_tag", "tag_basis", "output", "string", False, "标签依据说明"),
    ParamDef("writing.image_classify", "material_title", "input", "string", True, "所属素材标题", "upstream"),
    ParamDef("writing.image_classify", "image_index", "input", "int", True, "图片序号", "system"),
    ParamDef("writing.image_classify", "width", "input", "int", False, "图片宽度", "system"),
    ParamDef("writing.image_classify", "height", "input", "int", False, "图片高度", "system"),
    ParamDef("writing.image_classify", "vl_content", "input", "string", True, "VL识别文本", "upstream"),
    ParamDef("writing.image_classify", "image_type", "output", "string", True, "图片粗类型"),
    ParamDef("writing.image_classify", "business_subtype", "output", "string", True, "图片业务子类"),
    ParamDef("writing.image_classify", "caption", "output", "string", True, "图片说明文字"),
    ParamDef("writing.image_classify", "structured_fields", "output", "json", False, "可结构化图片字段"),
    ParamDef("writing.chapter_judge", "chapter_title", "input", "string", True, "当前章节标题", "upstream"),
    ParamDef("writing.chapter_judge", "chapter_type", "input", "string", True, "当前章节类型", "upstream"),
    ParamDef("writing.chapter_judge", "chapter_path", "input", "string", True, "章节祖先路径", "upstream"),
    ParamDef("writing.chapter_judge", "semantic_role", "input", "string", False, "章节画像语义角色", "upstream"),
    ParamDef("writing.chapter_judge", "expects", "input", "string", False, "章节期望匹配内容", "upstream"),
    ParamDef("writing.chapter_judge", "rejects", "input", "string", False, "章节排除内容", "upstream"),
    ParamDef("writing.chapter_judge", "candidates_text", "input", "string", True, "候选约束列表", "upstream"),
    ParamDef("writing.chapter_judge", "bindings", "output", "json", True, "章节-约束裁决数组"),
    ParamDef("writing.data_fill", "node_title", "input", "string", True, "章节标题", "upstream"),
    ParamDef("writing.data_fill", "chapter_number", "input", "string", False, "章节编号", "derived"),
    ParamDef("writing.data_fill", "target_word_count", "input", "int", False, "建议字数", "upstream"),
    ParamDef("writing.data_fill", "sub_numbering_hint", "input", "string", False, "子标题编号规范", "derived"),
    ParamDef("writing.data_fill", "industry_profile_excerpt", "input", "string", False, "行业画像摘录（术语表+写作风格）", "upstream"),
    ParamDef("writing.data_fill", "chapter_profile_text", "input", "string", False, "章节画像文本", "upstream"),
    ParamDef("writing.data_fill", "clauses_text", "input", "string", False, "关联约束", "upstream"),
    ParamDef("writing.data_fill", "materials_text", "input", "string", False, "可用素材上下文", "upstream"),
    ParamDef("writing.data_fill", "material_usage_rules", "input", "string", False, "素材消费约束", "derived"),
    ParamDef("writing.data_fill", "content", "output", "string", True, "填充后的内容"),
    ParamDef("writing.section_plan", "node_context_json", "input", "json", True, "章节上下文(JSON)", "system"),
    ParamDef("writing.section_plan", "chapter_profile_text", "input", "string", True, "章节画像文本", "upstream"),
    ParamDef("writing.section_plan", "industry_profile_text", "input", "string", True, "行业画像文本", "upstream"),
    ParamDef("writing.section_plan", "constraints_json", "input", "json", True, "关联约束(JSON)", "upstream"),
    ParamDef("writing.section_plan", "scoring_points_json", "input", "json", True, "评分点(JSON)", "upstream"),
    ParamDef("writing.section_plan", "evidence_catalog_json", "input", "json", True, "证据目录(JSON)", "upstream"),
    ParamDef("writing.section_plan", "section_plan", "output", "json", True, "SectionPlanDraft"),
    ParamDef("writing.free_writing", "node_title", "input", "string", True, "章节标题", "upstream"),
    ParamDef("writing.free_writing", "chapter_number", "input", "string", False, "章节编号", "derived"),
    ParamDef("writing.free_writing", "target_word_count", "input", "int", False, "建议字数", "upstream"),
    ParamDef("writing.free_writing", "sub_numbering_hint", "input", "string", False, "子标题编号规范", "derived"),
    ParamDef("writing.free_writing", "chapter_profile_text", "input", "string", False, "章节画像文本", "upstream"),
    ParamDef("writing.free_writing", "clauses_text", "input", "string", False, "招标约束", "upstream"),
    ParamDef("writing.free_writing", "materials_text", "input", "string", False, "可用素材上下文", "upstream"),
    ParamDef("writing.free_writing", "material_usage_rules", "input", "string", False, "素材消费约束", "derived"),
    ParamDef("writing.free_writing", "section_plan_text", "input", "string", False, "章节论证计划", "upstream"),
    ParamDef("writing.free_writing", "previous_summaries_text", "input", "string", False, "前文摘要", "upstream"),
    ParamDef("writing.free_writing", "industry_profile_excerpt", "input", "string", False, "行业画像摘录", "upstream"),
    ParamDef("writing.free_writing", "content", "output", "string", True, "生成的章节内容"),
    ParamDef("writing.section_summary", "node_title", "input", "string", True, "章节标题", "upstream"),
    ParamDef("writing.section_summary", "content_preview", "input", "string", True, "章节内容预览", "upstream"),
    ParamDef("writing.section_summary", "summary", "output", "string", True, "章节摘要"),
    ParamDef("writing.length_adjust", "node_title", "input", "string", True, "章节标题", "upstream"),
    ParamDef("writing.length_adjust", "content", "input", "string", True, "原章节内容(HTML)", "upstream"),
    ParamDef("writing.length_adjust", "current_word_count", "input", "int", True, "当前纯文本字数", "derived"),
    ParamDef("writing.length_adjust", "target_word_count", "input", "int", True, "建议字数", "upstream"),
    ParamDef("writing.length_adjust", "direction", "input", "string", True, "调整方向(扩写/压缩)", "derived"),
    ParamDef("writing.length_adjust", "content_out", "output", "string", True, "调整后的章节内容(HTML)"),
    ParamDef("writing.cross_ref_check", "outline_text", "input", "string", True, "全文章节目录", "upstream"),
    ParamDef("writing.cross_ref_check", "reference_fragments", "input", "string", True, "待检查引用记录", "upstream"),
    ParamDef("writing.cross_ref_check", "issues", "output", "json", True, "交叉引用检查结果"),
    ParamDef("writing.chapter_profile", "tree_text", "input", "string", True, "完整目录树（含层级缩进）", "upstream"),
    ParamDef("writing.chapter_profile", "leaf_list", "input", "string", True, "需要画像的叶子章节列表", "upstream"),
    ParamDef("writing.chapter_profile", "industry_profile_excerpt", "input", "string", False, "行业画像摘录 (P2.3 上下文增强)", "upstream"),
    ParamDef("writing.chapter_profile", "clause_domain_summary", "input", "string", False, "本卷约束按 sub_type 聚合摘要 (P2.3 上下文增强)", "upstream"),
    ParamDef("writing.chapter_profile", "clause_examples_summary", "input", "string", False, "具体高价值约束示例摘要", "upstream"),
    ParamDef("writing.chapter_profile", "profiles", "output", "json", True, "叶子章节画像数组"),
    ParamDef("writing.material_pair_judge", "pairs_json", "input", "string", True, "候选素材-章节对(JSON)", "upstream"),
    ParamDef("writing.material_pair_judge", "decisions", "output", "json", True, "逐对素材-章节蕴含裁决数组"),
    # ── 固定模板 LLM-first 识别 ──
    ParamDef("writing.fixed_template_recognize", "chunks_text", "input", "string", True, "固定模板候选页文本", "upstream"),
    ParamDef("writing.fixed_template_recognize", "page_range_hint", "input", "string", False, "候选页范围提示", "derived"),
    ParamDef("writing.fixed_template_recognize", "volume_type_hint", "input", "string", False, "分卷提示(technical/commercial)", "derived"),
    ParamDef("writing.fixed_template_recognize", "templates", "output", "json", True, "固定模板目录数组"),
]

for _judge_nk in ("judge.review", "judge.generic", "judge.collusion", "judge.writer"):
    PARAM_DEFS.extend([
        ParamDef(_judge_nk, "original_output", "input", "json", True, "待复核的原始 AI 输出", "upstream"),
        ParamDef(_judge_nk, "context_summary", "input", "string", True, "复核上下文摘要", "upstream"),
        ParamDef(_judge_nk, "evidence_text", "input", "string", False, "补充证据文本", "upstream"),
        ParamDef(_judge_nk, "verdict", "output", "string", True, "复核结论: agree/disagree/need_review"),
        ParamDef(_judge_nk, "confidence", "output", "float", True, "复核置信度 0-1"),
        ParamDef(_judge_nk, "reason", "output", "string", True, "复核理由"),
    ])

# 为所有节点自动添加运行时参数
_ALL_NODE_KEYS = {nd.node_key for nd in ALL_NODES}
for _nk in sorted(_ALL_NODE_KEYS):
    PARAM_DEFS.extend(_runtime_params(_nk))


# ═══════════════════════════════════════════════
# 从代码模块加载 Prompt 文本
# ═══════════════════════════════════════════════

def _resolve_prompt(ref: Optional[PromptRef]) -> str:
    """从代码模块获取 Prompt 常量文本"""
    if ref is None:
        return ""
    try:
        mod = importlib.import_module(ref.module)
        val = getattr(mod, ref.constant, None)
        if val is None:
            logger.warning(f"Prompt 常量不存在: {ref.module}.{ref.constant}")
            return ""
        return str(val)
    except Exception as e:
        logger.warning(f"加载 Prompt 失败: {ref.module}.{ref.constant} -> {e}")
        return ""


# ═══════════════════════════════════════════════
# 幂等 Seed 逻辑
# ═══════════════════════════════════════════════

def sync_existing_node_runtime(existing: Any, nd: NodeDef) -> list[str]:
    """同步存量节点运行时字段: node_class 对齐; default_max_tokens 只升不降。

    不做: 覆盖 Prompt / temperature / 人工已调高的预算。
    """
    changed: list[str] = []
    desired_class = nd.node_class
    if desired_class in ("mechanical", "judgment"):
        current_class = getattr(existing, "node_class", None)
        if current_class != desired_class:
            existing.node_class = desired_class
            changed.append("node_class")
    desired_tokens = int(nd.default_max_tokens or 0)
    current_tokens = int(getattr(existing, "default_max_tokens", 0) or 0)
    if desired_tokens > current_tokens:
        existing.default_max_tokens = desired_tokens
        changed.append("default_max_tokens")
    return changed


async def _sync_code_versions(
    db: AsyncSession, node_id: int, nd: NodeDef, stats: dict,
) -> None:
    """比对代码 Prompt 与 DB 中 source=code 的最新版本，差异时创建新版本（不激活）"""
    from app.models.llm_template_version import LlmTemplateVersion

    for role, ref in [("system", nd.system_prompt), ("user", nd.user_prompt)]:
        code_text = _resolve_prompt(ref)
        if not code_text:
            continue

        latest_code_ver = (await db.execute(
            select(LlmTemplateVersion)
            .where(
                LlmTemplateVersion.node_id == node_id,
                LlmTemplateVersion.template_role == role,
                LlmTemplateVersion.source == "code",
            )
            .order_by(LlmTemplateVersion.version.desc())
            .limit(1)
        )).scalar_one_or_none()

        if latest_code_ver and latest_code_ver.content.strip() == code_text.strip():
            continue

        max_ver = (await db.execute(
            select(func.max(LlmTemplateVersion.version)).where(
                LlmTemplateVersion.node_id == node_id,
                LlmTemplateVersion.template_role == role,
            )
        )).scalar() or 0

        db.add(LlmTemplateVersion(
            node_id=node_id, template_role=role,
            version=max_ver + 1, content=code_text,
            is_active=False, source="code",
            status="draft", change_note="代码变更自动检测",
        ))
        stats["versions"] = stats.get("versions", 0) + 1
        stats.setdefault("code_updates", []).append(f"{nd.node_key}/{role}")
        logger.info(f"[Seed] 代码变更检测: {nd.node_key}/{role} -> 创建 v{max_ver + 1} (未激活)")


async def seed_llm_registry(db: AsyncSession) -> dict:
    """幂等填充 LLM Center 注册表

    Returns:
        统计: {"flows": n, "stages": n, "nodes": n, "versions": n, "edges": n}
    """
    from app.models.llm_flow import LlmFlow, LlmStage
    from app.models.llm_node import LlmNode
    from app.models.llm_template_version import LlmTemplateVersion
    from app.models.llm_node_edge import LlmNodeEdge

    stats = {"flows": 0, "stages": 0, "nodes": 0, "versions": 0, "edges": 0}

    # 1. Flows
    flow_map: dict[str, int] = {}
    for fd in FLOWS:
        existing = (await db.execute(
            select(LlmFlow).where(LlmFlow.flow_key == fd.flow_key)
        )).scalar_one_or_none()
        if existing:
            flow_map[fd.flow_key] = existing.id
        else:
            flow = LlmFlow(
                flow_key=fd.flow_key, name=fd.name, description=fd.description,
                route_prefix=fd.route_prefix, display_order=fd.display_order,
            )
            db.add(flow)
            await db.flush()
            flow_map[fd.flow_key] = flow.id
            stats["flows"] += 1
            logger.info(f"[Seed] Flow 新增: {fd.flow_key}")

    # 2. Stages
    stage_map: dict[str, int] = {}  # "flow_key.stage_key" -> id
    for sd in STAGES:
        flow_id = flow_map.get(sd.flow_key)
        if not flow_id:
            continue
        composite_key = f"{sd.flow_key}.{sd.stage_key}"
        existing = (await db.execute(
            select(LlmStage).where(LlmStage.flow_id == flow_id, LlmStage.stage_key == sd.stage_key)
        )).scalar_one_or_none()
        if existing:
            stage_map[composite_key] = existing.id
        else:
            stage = LlmStage(
                flow_id=flow_id, stage_key=sd.stage_key, name=sd.name,
                description=sd.description, display_order=sd.display_order,
            )
            db.add(stage)
            await db.flush()
            stage_map[composite_key] = stage.id
            stats["stages"] += 1
            logger.info(f"[Seed] Stage 新增: {composite_key}")

    # 3. Nodes + v1 Prompt versions
    node_id_map: dict[str, int] = {}  # node_key -> id
    for nd in ALL_NODES:
        composite_stage = f"{nd.flow_key}.{nd.stage_key}"
        stage_id = stage_map.get(composite_stage)
        if not stage_id:
            logger.warning(f"[Seed] Stage 未找到: {composite_stage}, 跳过节点 {nd.node_key}")
            continue

        existing = (await db.execute(
            select(LlmNode).where(LlmNode.node_key == nd.node_key)
        )).scalar_one_or_none()

        if nd.node_class not in ("mechanical", "judgment"):
            raise ValueError(
                f"[Seed] NodeDef {nd.node_key} 未声明合法 node_class"
            )

        if existing:
            node_id_map[nd.node_key] = existing.id
            runtime_changed = sync_existing_node_runtime(existing, nd)
            if runtime_changed:
                stats["nodes_runtime_updated"] = stats.get("nodes_runtime_updated", 0) + 1
                logger.info(
                    f"[Seed] Node 运行时同步: {nd.node_key} fields={runtime_changed}"
                )
            await _sync_code_versions(db, existing.id, nd, stats)
            continue

        node = LlmNode(
            stage_id=stage_id, node_key=nd.node_key, name=nd.name,
            description=nd.description, builder_key=nd.builder_key,
            source_file=nd.source_file, execution_channel=nd.execution_channel,
            call_method=nd.call_method, default_temperature=nd.default_temperature,
            default_max_tokens=nd.default_max_tokens,
            output_schema_name=nd.output_schema_name or None,
            visibility=nd.visibility, editable_level=nd.editable_level,
            display_order=nd.display_order,
            node_class=nd.node_class,
        )
        db.add(node)
        await db.flush()
        node_id_map[nd.node_key] = node.id
        stats["nodes"] += 1
        logger.info(f"[Seed] Node 新增: {nd.node_key}")

        # v1 Prompt: system
        sys_text = _resolve_prompt(nd.system_prompt)
        if sys_text:
            db.add(LlmTemplateVersion(
                node_id=node.id, template_role="system", version=1,
                content=sys_text, is_active=True, source="code",
                status="published", change_note="代码同步 v1",
            ))
            stats["versions"] += 1

        # v1 Prompt: user
        user_text = _resolve_prompt(nd.user_prompt)
        if user_text:
            db.add(LlmTemplateVersion(
                node_id=node.id, template_role="user", version=1,
                content=user_text, is_active=True, source="code",
                status="published", change_note="代码同步 v1",
            ))
            stats["versions"] += 1

    await db.flush()

    # 4. Edges
    for ed in EDGES:
        from_id = node_id_map.get(ed.from_node_key)
        to_id = node_id_map.get(ed.to_node_key)
        if not from_id or not to_id:
            continue
        existing = (await db.execute(
            select(LlmNodeEdge).where(
                LlmNodeEdge.from_node_id == from_id,
                LlmNodeEdge.to_node_id == to_id,
            )
        )).scalar_one_or_none()
        if not existing:
            db.add(LlmNodeEdge(
                from_node_id=from_id, to_node_id=to_id,
                edge_type=ed.edge_type, description=ed.description,
            ))
            stats["edges"] += 1

    # 5. Param Defs -- 按代码注册源同步
    #
    # 策略:
    #   - 代码声明即契约: PARAM_DEFS 为权威列表, seed 每次执行都会把 DB 向代码对齐
    #   - 自动覆盖字段 (代码即真相): data_type / is_required / description /
    #     source_type / source_hint / status
    #   - 人工维护字段不覆盖: default_value / example_value / ui_hint /
    #     display_order (运营或 UI 层面行为, 禁止 seed 回灌)
    #   - 软降级: 代码已删除但 DB 仍存在的 (node, direction, param_key) -> status=deprecated
    #     仅对"代码对该 (node, direction) 至少声明了一个参数"的组合生效,
    #     避免误伤尚未迁入 seed 的历史节点。
    from app.models.llm_param_def import LlmParamDef
    stats["params"] = 0
    stats["params_updated"] = 0
    stats["params_deprecated"] = 0

    _AUTO_SYNC_FIELDS: tuple[str, ...] = (
        "data_type", "is_required", "description",
        "source_type", "source_hint", "status",
    )

    # 预先统计代码侧 (node_key, direction) -> {param_key}, 供软降级判断
    code_param_keys_by_node_dir: dict[tuple[str, str], set[str]] = {}
    for pd in PARAM_DEFS:
        code_param_keys_by_node_dir.setdefault(
            (pd.node_key, pd.direction), set()
        ).add(pd.param_key)

    for pd in PARAM_DEFS:
        nid = node_id_map.get(pd.node_key)
        if not nid:
            continue
        existing = (await db.execute(
            select(LlmParamDef).where(
                LlmParamDef.node_id == nid,
                LlmParamDef.direction == pd.direction,
                LlmParamDef.param_key == pd.param_key,
            )
        )).scalar_one_or_none()

        desired: dict[str, object] = {
            "data_type": pd.data_type,
            "is_required": pd.is_required,
            "description": pd.description,
            "source_type": pd.source_type,
            "source_hint": pd.source_hint or None,
            "status": "active",
        }

        if existing is None:
            db.add(LlmParamDef(
                node_id=nid, param_key=pd.param_key, direction=pd.direction,
                **desired,
            ))
            stats["params"] += 1
            continue

        changed_fields: list[str] = []
        for fname in _AUTO_SYNC_FIELDS:
            if getattr(existing, fname) != desired[fname]:
                setattr(existing, fname, desired[fname])
                changed_fields.append(fname)
        if changed_fields:
            stats["params_updated"] += 1
            logger.info(
                f"[Seed] Param 更新: {pd.node_key}/{pd.direction}/{pd.param_key} "
                f"fields={changed_fields}"
            )

    # 5.b 软降级: 代码中已删除的 (node_key, direction) 组合下的冗余参数
    for (node_key, direction), code_keys in code_param_keys_by_node_dir.items():
        nid = node_id_map.get(node_key)
        if not nid:
            continue
        orphans = (await db.execute(
            select(LlmParamDef).where(
                LlmParamDef.node_id == nid,
                LlmParamDef.direction == direction,
                LlmParamDef.status == "active",
                LlmParamDef.param_key.notin_(code_keys),
            )
        )).scalars().all()
        for row in orphans:
            row.status = "deprecated"
            stats["params_deprecated"] += 1
            logger.info(
                f"[Seed] Param 软降级: {node_key}/{direction}/{row.param_key} "
                f"(代码已移除)"
            )

    # 6. 已废弃节点 soft-deactivate (保留 DB 数据, 仅置 is_active=False)
    stats["deprecated_deactivated"] = 0
    for dep_key in DEPRECATED_NODE_KEYS:
        dep_node = (await db.execute(
            select(LlmNode).where(LlmNode.node_key == dep_key)
        )).scalar_one_or_none()
        if dep_node and dep_node.is_active:
            dep_node.is_active = False
            stats["deprecated_deactivated"] += 1
            logger.info(f"[Seed] 已废弃节点 soft-deactivate: {dep_key}")

    await db.commit()
    logger.info(f"[Seed] 完成: {stats}")
    return stats


# ═══════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════

async def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    from app.core.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        stats = await seed_llm_registry(db)
        print(f"Seed 完成: {stats}")


if __name__ == "__main__":
    asyncio.run(main())
