"""
职责: 判定评分项的被评分对象（报价金额/下浮率 / 技术方案质量）是否在证据中出现。
不做: 不调用 LLM/DB、不计算真实评标价、不生成最终审查结论。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

_QUOTE_HINTS = (
    "投标总价",
    "投标报价",
    "报价一览",
    "开标一览",
    "含税总价",
    "报价金额",
)
_RESTATEMENT_HINTS = (
    "价格分计算",
    "评标办法",
    "评分标准",
    "计算公式",
    "评标基准价",
    "价格部分得分",
    "报价得分计算公式",
)
_MONEY_RE = re.compile(
    r"(?:人民币|RMB|￥|¥)\s*[\d,，.]+(?:万)?元?"
    r"|[\d,，.]+\s*(?:万元|亿元)"
    r"|(?:投标总价|投标报价|含税总价|报价金额)[^\d]{0,16}[\d,，.]+"
)
_COMPARATIVE_HINTS = (
    "最低评标价",
    "评标基准价",
    "基准价",
    "算术平均",
    "平均价",
    "所有有效报价",
    "其他投标人",
    "所有投标人",
    "偏离度",
)
_RATE_RE = re.compile(
    r"(?:下浮(?:比例|率)?|降点(?:率)?|折扣(?:率)?|优惠率)"
    r"(?:\s*(?:为|是|：|:))?\s*"
    r"(\d+(?:\.\d+)?)\s*%"
)
_RATE_REQ_HINTS = (
    "下浮",
    "降点",
    "折扣率",
    "优惠率",
    "下浮比例",
    "下浮率",
    "综合下浮",
)
_AMOUNT_REQ_HINTS = (
    "投标总价",
    "含税总价",
    "报价金额",
    "总报价",
)
RequiredQuoteForms = Literal["amount", "rate", "both", "either"]


@dataclass(frozen=True)
class QuoteForms:
    """
    职责: 记录响应文本中已检出的报价形态。
    不做: 不判断是否满足招标要求、不打分。
    """

    has_amount: bool
    has_rate: bool
_QUALITY_HINTS = (
    "新意",
    "创新",
    "差异",
    "针对性",
    "具体措施",
    "措施",
    "不足",
    "扣分",
    "未体现",
    "分档",
    "可操作",
    "深度",
    "亮点",
    "创新点",
)
# 主观分档：出现任一则整条按 graded（混有硬指标时仍走 压档）。
# 不用单字「优/差」——「优于要求」是硬指标满足，不是优/良/中档。
_GRADED_HINTS = (
    "新意",
    "创新点",
    "差异性",
    "差异化",
    "酌情",
    "按档",
    "分档",
    "方案完整性",
    "优良中",
    "优秀得",
    "良好得",
)
_BINARY_PATTERNS = (
    re.compile(r"否则\s*0"),
    re.compile(r"否则不得分"),
    re.compile(r"否则不得"),
    re.compile(r"不满足.{0,12}0\s*分"),
    re.compile(r"(?:满足|达标|符合)[^。；;]{0,24}得\s*\d"),
    re.compile(r"得\s*\d+(?:\.\d+)?\s*分.{0,16}否则"),
)
_EST_CLAIM_RE = re.compile(r"预估得分[：:]\s*\d+(?:\.\d+)?分")
_LABELED_VOLUMES = frozenset({
    "technical", "commercial", "qualification", "service", "composite",
})


def build_scoring_object_evidence(pair: Any) -> dict[str, Any]:
    """从匹配对抽出评分对象门禁用的只读证据字段。"""
    req = getattr(pair, "requirement", None)
    if req is None:
        return {}
    return {
        "requirement_kind": getattr(req, "requirement_kind", "normal"),
        "is_key_review": bool(getattr(req, "is_key_review", 0)),
        "requirement_text": getattr(req, "description", None) or "",
        "scoring_metadata": getattr(req, "scoring_metadata", None) or {},
        "score_weight": getattr(req, "score_weight", None),
    }


def is_scoring_requirement(evidence: dict[str, Any] | None) -> bool:
    ev = evidence or {}
    if str(ev.get("requirement_kind") or "normal") == "scoring_item":
        return True
    return bool(ev.get("is_key_review"))


def _volume(evidence: dict[str, Any] | None) -> str:
    meta = (evidence or {}).get("scoring_metadata") or {}
    return str(meta.get("volume") or "").strip().lower()


def is_price_scoring_item(evidence: dict[str, Any] | None) -> bool:
    if not is_scoring_requirement(evidence):
        return False
    vol = _volume(evidence)
    if vol == "price":
        return True
    if vol in _LABELED_VOLUMES:
        return False
    text = (evidence or {}).get("requirement_text") or ""
    return any(k in text for k in ("投标报价", "价格分", "报价得分", "评标价", "投标总价"))


def _standard_corpus(source: dict[str, Any] | None) -> str:
    ev = source or {}
    meta = ev.get("scoring_metadata") or {}
    chunks = [
        str(ev.get("requirement_text") or ""),
        str(meta.get("authoritative_description") or ""),
    ]
    for key in ("score_rules", "deduction_rules"):
        for item in meta.get(key) or []:
            chunks.append(str(item))
    return "\n".join(chunks)


def classify_scoring_standard_kind(source: dict[str, Any] | None) -> str:
    """判定评分标准是二分硬指标、主观分档还是未知。

    职责: 从评分规则/要求原文识别 binary / graded / unknown。
    不做: 不打分、不改审查结论、不推断卷别。
    """
    text = _standard_corpus(source)
    if not text.strip():
        return "unknown"
    if any(h in text for h in _GRADED_HINTS):
        return "graded"
    if any(p.search(text) for p in _BINARY_PATTERNS):
        return "binary"
    return "unknown"


def assessments_confirm_full_marks(
    assessments: Any,
    max_score: float | None,
) -> bool:
    """逐档是否全部达标且可量化分值合计达到满分。

    职责: 用 rule_assessments 判断是否已闭合满分，供存在性压档豁免。
    不做: 不改 assessments、不估算主观档。
    """
    if not assessments or max_score is None:
        return False
    try:
        ceiling = float(max_score)
    except (TypeError, ValueError):
        return False
    if ceiling <= 0:
        return False

    points_sum = 0.0
    has_numeric = False
    for raw in assessments:
        if hasattr(raw, "model_dump"):
            item = raw.model_dump()
        elif isinstance(raw, dict):
            item = raw
        else:
            return False
        status = str(item.get("status") or "").strip().lower()
        if status != "met":
            return False
        pts = item.get("points")
        if pts is None:
            continue
        try:
            points_sum += float(pts)
        except (TypeError, ValueError):
            return False
        has_numeric = True
    if not has_numeric:
        return False
    return points_sum + 0.05 >= ceiling


def is_technical_scoring_item(evidence: dict[str, Any] | None) -> bool:
    if not is_scoring_requirement(evidence):
        return False
    if is_price_scoring_item(evidence):
        return False
    vol = _volume(evidence)
    if vol == "technical":
        return True
    if vol in {"price"} | _LABELED_VOLUMES:
        return False
    cat = str((evidence or {}).get("category") or "").strip().lower()
    return cat in {"tech", "technical"}


def detect_quote_forms(text: str | None) -> QuoteForms:
    """
    职责: 从文本检出金额报价与下浮/降点/折扣率。
    不做: 不对照招标要求、不把公式里的百分号当成下浮。
    """
    raw = text or ""
    if not raw.strip():
        return QuoteForms(has_amount=False, has_rate=False)
    return QuoteForms(
        has_amount=_has_amount_quote(raw),
        has_rate=bool(_RATE_RE.search(raw)),
    )


def _has_amount_quote(text: str) -> bool:
    if not _MONEY_RE.search(text):
        return False
    if any(h in text for h in _QUOTE_HINTS):
        return True
    if any(h in text for h in _RESTATEMENT_HINTS):
        return False
    return True


def infer_required_quote_forms(requirement_text: str | None) -> RequiredQuoteForms:
    """
    职责: 从招标要求推断需要金额、下浮，还是二者/任一。
    不做: 不读取投标响应、不把「有效投标报价」当成必须填金额。
    """
    text = requirement_text or ""
    want_rate = any(h in text for h in _RATE_REQ_HINTS)
    want_amount = any(h in text for h in _AMOUNT_REQ_HINTS)
    if want_rate and want_amount:
        return "both"
    if want_rate:
        return "rate"
    if want_amount:
        return "amount"
    return "either"


def quote_forms_satisfy(
    required: RequiredQuoteForms,
    provided: QuoteForms,
) -> bool:
    """
    职责: 判断已检出形态是否覆盖招标要求；both 缺一侧仍视为已报价。
    不做: 不改符合度、不估算比较分。
    """
    if required == "amount":
        return provided.has_amount
    if required == "rate":
        return provided.has_rate
    return provided.has_amount or provided.has_rate


def has_bid_quote(
    response_text: str | None,
    requirement_text: str | None = None,
) -> bool:
    """
    职责: 判断投标响应是否给出招标要求的报价形态。
    不做: 不调用 LLM、不计算评标价。
    """
    provided = detect_quote_forms(response_text)
    if requirement_text is None:
        return provided.has_amount or provided.has_rate
    return quote_forms_satisfy(
        infer_required_quote_forms(requirement_text),
        provided,
    )


def is_comparative_price_formula(requirement_text: str | None) -> bool:
    text = requirement_text or ""
    return any(h in text for h in _COMPARATIVE_HINTS)


def resolve_max_score(evidence: dict[str, Any] | None) -> float | None:
    ev = evidence or {}
    meta = ev.get("scoring_metadata") or {}
    raw = meta.get("max_score")
    if raw is None:
        raw = ev.get("score_weight")
    try:
        if raw is None:
            return None
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None


def is_presence_only_full_marks(
    comment: str | None,
    estimated_score: float | None,
    max_score: float | None,
) -> bool:
    if estimated_score is None or max_score is None:
        return False
    try:
        if float(estimated_score) + 0.05 < float(max_score):
            return False
    except (TypeError, ValueError):
        return False
    text = comment or ""
    return not any(h in text for h in _QUALITY_HINTS)


def strip_estimated_score_claim(comment: str | None, replacement: str = "") -> str:
    return _EST_CLAIM_RE.sub(replacement, comment or "")


def replace_estimated_score_claim(comment: str | None, score: float) -> str:
    new = f"预估得分：{score:g}分"
    text = comment or ""
    if _EST_CLAIM_RE.search(text):
        return _EST_CLAIM_RE.sub(new, text, count=1)
    return text.rstrip() + new
