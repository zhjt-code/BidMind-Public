"""内部枚举值到用户态中文文案的统一映射真源。

历史教训：
- 中文化清洗在前端 `sanitizeProgressText` / 后端 router / 各页面 mapping 中
 各自维护，新增枚举值即时复发。
- 进度文案有清洗，但结果文案 `narrative_summary` / `synthesis_summary` /
 `risk_explanation` / `quality_flags` 等 LLM 自由文本字段直通到 UI。
- 黑名单式正则滞后于 LLM 新变体（`is_mandatory=true`、`relevance_score<30`、
 `technical页34`）；清洗后必须用泄漏检测器告警，使未知形态可观测。

本模块提供：
- `DISPLAY_TOKEN_MAP`: 单一映射真源（风险等级 / 决策态 / 证据级 /
 置信度 / 维度代号 / 全局发现类型 / 一致性状态 / 可信度档位 / 审查类别）
- `translate_token(token)`: 单 token 查询
- `sanitize_user_facing_text(text)`: 文本级闸门，按词边界替换内部枚举词、
 清除调试参数、中文化赋值/比较表达式，并在残留时 WARNING

接入约束：
- 所有面向用户的字符串字段（含 LLM 自由文本）在落库/router 序列化前必须过此函数
- 该函数为幂等：对已是中文的文本调用 N 次结果不变
- 不修改结构化字段（如枚举值、分数等），仅清洗供人阅读的文本
"""

from __future__ import annotations

import re
from typing import Final

from loguru import logger

from app.core.i18n.leak_detector import detect_internal_leaks


# ---------------------------------------------------------------------------
# 单一映射真源：内部枚举值 -> 用户态中文文案
# ---------------------------------------------------------------------------

DISPLAY_TOKEN_MAP: Final[dict[str, str]] = {
    # 风险等级（collusion / review 共用）
    "high": "高风险",
    "medium": "中风险",
    "low": "低风险",
    "none": "无风险",
    "inconclusive": "无法判定",
    # 决策态（collusion）
    "clear_no_risk": "未发现串标嫌疑",
    "suspicious_found": "发现疑似串标",
    "partial_review_needed": "需人工复核",
    "analysis_failed": "分析失败",
    "decidable": "可裁决",
    "needs_review": "需人工复核",
    "skipped": "未深度分析",
    "failed": "分析失败",
    "unreviewed": "未深度分析",
    # 证据级（collusion）
    "first": "一级证据",
    "second": "二级证据",
    "third": "三级证据",
    # 置信度档位（collusion / writer 共用）
    "certain": "确定",
    "probable": "高度可能",
    "possible": "可能",
    "unknown": "未知",
    # 维度代号（仅在自由文本中需要中文化时使用；结构化 Badge 已有专用映射）
    "d1_personnel": "D1 人员重叠",
    "d2_company": "D2 企业关联",
    "d3_text": "D3 文本雷同",
    "d4_price": "D4 报价异常",
    "d5_qualification": "D5 资质业绩",
    "d6_format": "D6 文档格式",
    "d7_abnormal": "D7 异常一致",
    "d8_metadata": "D8 文档元数据",
    # 全局发现类型（collusion）
    "personnel_overlap": "人员重叠",
    "company_relation": "企业关联",
    "pricing_pattern": "报价异常",
    "metadata_cluster": "元数据聚类",
    "cross_field_correlation": "跨字段串联",
    # 风险口径一致性（collusion）
    "diverged": "存在分歧",
    "consistent": "一致",
    # 行业画像置信度（collusion 行业画像）
    "high_confidence": "高可信",
    "medium_confidence": "中等可信",
    "low_confidence": "低可信",
    # 行业画像 status 四态（collusion 行业画像， 拆分语义）
    "llm_failed": "AI 调用失败",
    "insufficient_signals": "材料信号不足",
    "llm_returned_unknown": "AI 无法归纳",
    # status="success" 不进词典（默认不显示给用户，避免重复信息）
    # 投标审查符合度（review）
    "fully": "完全符合",
    "partially": "部分符合",
    "not": "不符合",
    # 审查类别（RequirementCategory.value，）
    "qualification": "资质要求",
    "technical": "技术要求",
    "commercial": "商务要求",
    "performance": "业绩要求",
    "staffing": "人员要求",
    "format": "格式要求",
    # 写标章节路由（writer）
    "fixed_template": "固定模板",
    "data_fill": "资料填充",
    "free_writing": "自由论述",
    # 写标 schema 字段名（：一致性检查/覆盖告警偶发拼进用户文案）
    "source_clause_ids": "来源约束",
    "source_material_ids": "来源素材",
}


# 关键字按长度倒序排列，长 token 优先匹配（如 "low_confidence" 不被 "low" 抢匹配）
_SORTED_TOKENS: Final[list[str]] = sorted(
    DISPLAY_TOKEN_MAP.keys(), key=lambda s: -len(s)
)


def translate_token(token: str | None) -> str:
    """单 token 查询；未命中返回原值。"""
    if not token:
        return ""
    return DISPLAY_TOKEN_MAP.get(str(token).strip().lower(), str(token))


# ---------------------------------------------------------------------------
# 调试参数清洗规则（与前端 sanitizeProgressText 保持一致语义）
# ---------------------------------------------------------------------------

_DEBUG_PARAM_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:pair(?:_id)?|group_index"
    r"|doc(?:ument)?(?:_[ab])?_id"
    r"|project_id|node_id|task_id)"
    r"\s*[:=]\s*[\w:\-]+\b",
    flags=re.IGNORECASE,
)

# 形如 `597:598` 的内部 pair 标识
_PAIR_NUM_RE: Final[re.Pattern[str]] = re.compile(r"\b\d+\s*:\s*\d+\b")

# 多余空白与紧贴标点的归一化
_SPACE_NEAR_PUNCT_RE: Final[re.Pattern[str]] = re.compile(r"\s+([，。；：、,;:])")
_LEAD_PUNCT_RE: Final[re.Pattern[str]] = re.compile(r"^[\s·,:;，；\-]+|[\s·,:;，；\-]+$")
_MULTI_SPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s{2,}")


def _build_token_pattern(token: str) -> re.Pattern[str]:
    """为 token 生成"边界匹配"正则。

    - 纯 ASCII token 用 `\b` 边界（避免误伤 'lower'/'noneoftheabove' 等）
    - 含下划线的 snake_case token 视同 ASCII，前后必须是非字母数字
    - 含中文的 token 不做边界（不会误伤）
    """
    escaped = re.escape(token)
    if re.fullmatch(r"[A-Za-z0-9_]+", token):
        # ASCII：左右非字母数字下划线
        return re.compile(rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])", re.IGNORECASE)
    return re.compile(escaped)


_COMPILED_TOKEN_PATTERNS: Final[list[tuple[re.Pattern[str], str]]] = [
    (_build_token_pattern(token), DISPLAY_TOKEN_MAP[token]) for token in _SORTED_TOKENS
]


# ---------------------------------------------------------------------------
# LLM 自由文本英文污染清洗
# ---------------------------------------------------------------------------

# "Golden Rule6" / "Golden Rule 6" → 移除（用户无需看到内部规则编号）
_GOLDEN_RULE_RE: Final[re.Pattern[str]] = re.compile(
    r"[Gg]olden\s*[Rr]ules?\s*\d*", re.IGNORECASE
)

# 审查失败兜底的机器标记：落库 comment 仍保留供 is_failure_result 识别，
# 用户态闸门必须剥掉，否则每次 GET /results 都会 WARNING 刷屏。
_FAILURE_COMMENT_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"\[?\s*LLM_REVIEW_FAILURE\s*\]?",
    re.IGNORECASE,
)

# 账本闸门/prompt 诊断串（机制名 + 要求主键）不得进入用户态
_LEDGER_OVERLAY_RE: Final[re.Pattern[str]] = re.compile(
    r"项目证据账本已登记.{0,2000}?本条未发现结论与账本冲突，待复核"
)
_LEDGER_NAME_RE: Final[re.Pattern[str]] = re.compile(r"项目证据账本")
_REQ_ID_REF_RE: Final[re.Pattern[str]] = re.compile(
    r"来自要求#\d+(?:、#\d+)*"
)
_LEDGER_CONFLICT_TAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"本条未发现结论与账本冲突，待复核"
)

# 证据锚点 "pages:5-10-11" → "第5、10、11页"
_PAGES_ANCHOR_RE: Final[re.Pattern[str]] = re.compile(
    r"\bpages\s*[:：]\s*(\d[\d\s,，、\-]*)", re.IGNORECASE
)

# is_mandatory=true/false → 否决项表述
_IS_MANDATORY_RE: Final[re.Pattern[str]] = re.compile(
    r"\bis_mandatory\s*[=:：]\s*(true|false|True|False|1|0|yes|no)\b",
    re.IGNORECASE,
)

# relevance_score 赋值/比较 → 相关性评分表述（比较符全覆盖）
_RELEVANCE_SCORE_EXPR_RE: Final[re.Pattern[str]] = re.compile(
    r"\brelevance_score\s*([=<>≤≥:：]+)\s*(\d+)",
    re.IGNORECASE,
)

# score 赋值/比较 → 评分表述（避免误伤 relevance_score：后者已先处理）
_SCORE_EXPR_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_])score\s*([=<>≤≥:：]+)\s*(\d+)",
    re.IGNORECASE,
)

# snake_case=映射表已知值 → 整式替换为中文
_SNAKE_KNOWN_VALUE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b[a-z][a-z0-9]*_[a-z0-9_]+\s*[=<>≤≥:：]+\s*([A-Za-z][A-Za-z0-9_]*)\b",
    re.IGNORECASE,
)

# 未知 snake_case 赋值/比较（RHS 仍为纯 ASCII token）通杀移除
_UNKNOWN_SNAKE_EXPR_RE: Final[re.Pattern[str]] = re.compile(
    r"\b[a-z][a-z0-9]*_[a-z0-9_]+\s*[=<>≤≥:：]+\s*[A-Za-z0-9_\"']+\b",
    re.IGNORECASE,
)

_CMP_LABELS: Final[dict[str, str]] = {
    "<": "低于",
    "<=": "不高于",
    "≤": "不高于",
    ">": "高于",
    ">=": "不低于",
    "≥": "不低于",
    "=": "",
    "==": "",
    ":": "",
    "：": "",
}


def _pages_anchor_to_cn(match: re.Match[str]) -> str:
    nums = [n for n in re.split(r"[\s,，、\-]+", match.group(1).strip()) if n]
    return "第" + "、".join(nums) + "页" if nums else ""


def _is_mandatory_to_cn(match: re.Match[str]) -> str:
    val = match.group(1).lower()
    if val in {"true", "1", "yes"}:
        return "属于否决项"
    return "非否决项"


def _score_expr_to_cn(prefix: str, match: re.Match[str]) -> str:
    op = match.group(1).strip()
    num = match.group(2)
    label = _CMP_LABELS.get(op, "")
    if label:
        return f"{prefix}{label}{num}"
    return f"{prefix}{num}"


def _snake_known_value_to_cn(match: re.Match[str]) -> str:
    value = match.group(1)
    mapped = DISPLAY_TOKEN_MAP.get(value.lower())
    return mapped if mapped is not None else match.group(0)


def sanitize_user_facing_text(text: str | None) -> str:
    """用户态文案出口闸门。

    将 LLM 自由文本中的英文枚举词、内部参数、赋值/比较表达式替换为中文，
    并归一化空白。清洗后若泄漏检测器仍命中，打 WARNING 便于观测新形态。
    该函数为幂等（已是中文的文本不会变化）。

    Args:
        text: 原始文本（可能含 inconclusive/is_mandatory=true/technical页 等）

    Returns:
        清洗后文本；输入为 None / 空串时返回 ''
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return ""

    cleaned = text

    # 1) 清除调试参数（pair_id=12 / doc_id:34 等）
    cleaned = _DEBUG_PARAM_RE.sub(" ", cleaned)
    # 2) 清除 `597:598` 形式的内部 pair 编号
    cleaned = _PAIR_NUM_RE.sub(" ", cleaned)
    # 3) 确定性字段表达式中文化（先长字段名，再短字段名）
    cleaned = _PAGES_ANCHOR_RE.sub(_pages_anchor_to_cn, cleaned)
    cleaned = _IS_MANDATORY_RE.sub(_is_mandatory_to_cn, cleaned)
    cleaned = _RELEVANCE_SCORE_EXPR_RE.sub(
        lambda m: _score_expr_to_cn("相关性评分", m), cleaned
    )
    cleaned = _SCORE_EXPR_RE.sub(lambda m: _score_expr_to_cn("评分", m), cleaned)
    cleaned = _GOLDEN_RULE_RE.sub("", cleaned)
    cleaned = _FAILURE_COMMENT_MARKER_RE.sub("", cleaned)
    cleaned = _LEDGER_OVERLAY_RE.sub("", cleaned)
    cleaned = _REQ_ID_REF_RE.sub("", cleaned)
    cleaned = _LEDGER_NAME_RE.sub("", cleaned)
    cleaned = _LEDGER_CONFLICT_TAIL_RE.sub("", cleaned)
    # 4) snake_case=已知枚举值 → 整式替换为中文（避免留下 field= 残片）
    cleaned = _SNAKE_KNOWN_VALUE_RE.sub(_snake_known_value_to_cn, cleaned)
    # 5) 按映射表逐 token 替换（含审查类别枚举）
    for pattern, replacement in _COMPILED_TOKEN_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    # 6) 未知 snake_case 赋值/比较（RHS 仍为 ASCII）通杀移除；须在 token 替换之后
    cleaned = _UNKNOWN_SNAKE_EXPR_RE.sub(" ", cleaned)
    # 7) 归一化空白
    cleaned = _SPACE_NEAR_PUNCT_RE.sub(r"\1", cleaned)
    cleaned = _MULTI_SPACE_RE.sub(" ", cleaned)
    cleaned = _LEAD_PUNCT_RE.sub("", cleaned)
    cleaned = cleaned.strip()

    # 8) 泄漏可观测：清洗后仍有残留则 WARNING（不阻断返回）
    residual = detect_internal_leaks(cleaned)
    if residual:
        logger.warning(
            "[i18n] 用户态文案清洗后仍有内部术语残留 hits={} sample={}",
            residual[:8],
            cleaned[:120],
        )

    return cleaned


def sanitize_user_facing_list(items: list[str] | None) -> list[str]:
    """对字符串列表逐项做用户态清洗，过滤空串。"""
    if not items:
        return []
    cleaned: list[str] = []
    for item in items:
        result = sanitize_user_facing_text(item)
        if result:
            cleaned.append(result)
    return cleaned
