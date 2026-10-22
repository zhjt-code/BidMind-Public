"""串标行业画像上下文构建器。

职责: 为串标行业画像构建结构化材料信号上下文并判定信号充分性。
不做: LLM 调用、风险等级裁决、结果页展示、PDF 渲染、数据库读写。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class IndustryProfileContextInput:
    """串标行业画像上下文输入。"""

    tender_text: str | None = None
    bid_extractions: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class IndustrySignalAdequacy:
    """行业信号充分性判定结果。"""

    is_sufficient: bool
    confidence: str
    reason: str


_TENDER_SECTION_HINTS = (
    "项目概况", "工程概况", "采购内容", "采购范围", "招标范围", "建设内容",
    "服务内容", "技术要求", "技术规格", "技术标准", "资格要求", "资质要求",
    "评分标准", "评审标准", "项目名称", "标的名称",
)

_INDUSTRY_TERMS = (
    "工程", "施工", "采购", "服务", "系统", "平台", "设备", "软件", "数据",
    "信息化", "集成", "接口", "运维", "改造", "医院", "医疗", "市政", "道路",
    "桥梁", "园区", "物业", "安防", "消防", "电力", "石油", "油气", "管线",
    "防爆", "环保", "水利", "矿山", "检测", "监理", "咨询", "等保", "资质",
    "工艺", "标准", "专业", "HIS", "EMR", "PACS", "LIS",
)

_LOW_VALUE_TENDER_TERMS = (
    "投标人须知", "招标公告", "通用合同条款", "评分办法", "评标办法",
)

_STRONG_SIGNAL_TYPES = {
    "domain", "title", "qualification", "equipment", "process", "standard", "term",
}


def build_industry_signal_context(input_data: IndustryProfileContextInput) -> str:
    """构建材料驱动的行业信号抽取上下文。"""
    parts: list[str] = []

    tender_lines = _collect_tender_signal_lines(input_data.tender_text or "")
    if tender_lines:
        parts.append("### 招标文件行业相关片段\n" + "\n".join(tender_lines))

    bid_lines = _collect_bid_signal_lines(input_data.bid_extractions or [])
    if bid_lines:
        parts.append("### 投标侧补充信号\n" + "\n".join(bid_lines))

    if not parts:
        return "（材料中未提取到足够行业信号，请返回低置信度或 unknown，不得猜测行业）"
    return "\n\n".join(parts)


def assess_industry_signal_adequacy(signals: list[Any] | None) -> IndustrySignalAdequacy:
    """按信号强度而非单纯数量判定是否足以进入画像归纳。"""
    normalized = [_normalize_signal(signal) for signal in (signals or [])]
    normalized = [signal for signal in normalized if signal["value"]]
    if not normalized:
        return IndustrySignalAdequacy(False, "unknown", "no_signals")

    strong_count = sum(1 for signal in normalized if _is_strong_signal(signal))
    if len(normalized) >= 3 and strong_count >= 1:
        return IndustrySignalAdequacy(True, "high", "multiple_signals")
    if len(normalized) >= 2:
        confidence = "medium" if strong_count else "low"
        return IndustrySignalAdequacy(True, confidence, "multiple_signals")
    if strong_count == 1:
        return IndustrySignalAdequacy(True, "medium", "single_strong_signal")
    return IndustrySignalAdequacy(False, "low", "weak_signals")


def extract_deterministic_industry_signals(
    context: str,
    *,
    max_signals: int = 8,
) -> list[dict[str, Any]]:
    """从结构化上下文中确定性抽取行业信号，兜底 LLM 空输出。"""
    signals: list[dict[str, Any]] = []
    for line in (context or "").splitlines():
        source, value = _parse_context_line(line)
        if not value:
            continue
        score = _score_signal_text(value)
        if score <= 0:
            continue
        signals.append({
            "signal_type": _infer_signal_type(source, value),
            "value": value,
            "source": source or "context",
            "source_refs": [source or "结构化上下文"],
            "confidence": 0.9 if score >= 4 else 0.75,
        })

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for signal in sorted(signals, key=lambda item: float(item.get("confidence") or 0), reverse=True):
        key = str(signal.get("value") or "")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(signal)
        if len(deduped) >= max_signals:
            break
    return deduped


def build_profile_generation_payload(
    *,
    signal_payload: dict[str, Any],
    context: str,
    adequacy: IndustrySignalAdequacy,
) -> dict[str, Any]:
    """构造画像归纳输入，保留 signals 与原始结构化上下文摘要。"""
    payload = dict(signal_payload or {})
    payload["adequacy"] = {
        "is_sufficient": adequacy.is_sufficient,
        "confidence": adequacy.confidence,
        "reason": adequacy.reason,
    }
    payload["source_context_excerpt"] = _compact_text(context, max_chars=6000)
    payload["_instruction"] = (
        "请同时依据 signals 与 source_context_excerpt 归纳行业；"
        "source_context_excerpt 是已提取的招标/投标结构化材料，不是外部先验。"
    )
    return payload


def build_deterministic_profile_from_signals(
    signals: list[Any],
    *,
    adequacy: IndustrySignalAdequacy,
) -> dict[str, Any] | None:
    """强信号充分但画像 LLM 返回 unknown 时，构造低风险兜底画像。"""
    normalized = [_normalize_signal(signal) for signal in (signals or [])]
    strong = [signal for signal in normalized if _is_strong_signal(signal)]
    if not strong:
        return None

    industry_type = _infer_industry_type_from_signal_text(
        " ".join(str(signal.get("value") or "") for signal in strong)
    )
    if not industry_type:
        return None

    has_qualification = any(str(signal.get("signal_type")) == "qualification" for signal in normalized)
    has_process = any(str(signal.get("signal_type")) in {"process", "standard", "equipment"} for signal in normalized)
    standardized_domains: list[dict[str, str]] = []
    creative_domains: list[dict[str, str]] = []
    common_qualification_types: list[dict[str, str]] = []
    if has_qualification:
        standardized_domains.append({
            "domain": "行业准入资质与认证材料",
            "reason": "多份材料中的资质/准入证书属于行业基础合规材料，相似时应先按低区分度内容处理。",
        })
        common_qualification_types.append({
            "name": "行业准入资质/认证证书",
            "reason": "该类型由结构化资质信号直接支撑。",
        })
    if has_process:
        creative_domains.append({
            "domain": "具体技术路线、实验平台与工艺参数",
            "reason": "技术方案中的具体研发路线、平台搭建和参数分析更能体现投标方差异。",
        })

    return {
        "industry_type": industry_type,
        "confidence": adequacy.confidence,
        "summary": "画像 LLM 未归纳出明确行业，系统根据结构化强信号生成保守行业画像。",
        "standardized_domains": standardized_domains,
        "creative_domains": creative_domains,
        "common_qualification_types": common_qualification_types,
        "common_pricing_patterns": [],
        "source_mode": "deterministic_fallback",
    }


def _collect_tender_signal_lines(tender_text: str, *, max_items: int = 16) -> list[str]:
    """从招标全文中定向抽取行业相关片段，避免线性前缀截断。"""
    units = _split_text_units(tender_text)
    scored: list[tuple[int, int, str]] = []
    for index, unit in enumerate(units):
        score = _score_tender_unit(unit)
        if score <= 0:
            continue
        scored.append((score, index, _compact_text(unit, max_chars=260)))

    scored.sort(key=lambda item: (-item[0], item[1]))
    selected = sorted(scored[:max_items], key=lambda item: item[1])
    return [
        f"- 来源=tender:行业片段{idx + 1} | 内容={text}"
        for idx, (_score, _index, text) in enumerate(selected)
    ]


def _collect_bid_signal_lines(
    bid_extractions: list[dict[str, Any]],
    *,
    max_items_per_group: int = 20,
) -> list[str]:
    """从投标结构化提取结果中构造补充行业信号。"""
    technical: list[str] = []
    qualifications: list[str] = []
    projects: list[str] = []
    companies: list[str] = []
    prices: list[str] = []
    subcontractors: list[str] = []
    roles: Counter[str] = Counter()

    for ext in bid_extractions:
        if not isinstance(ext, dict):
            continue
        technical.extend(_string_items(ext.get("technical_highlights")))
        qualifications.extend(_string_items(ext.get("qualifications")))
        projects.extend(_string_items(ext.get("past_projects")))
        company = ext.get("company_info")
        if isinstance(company, dict):
            company_name = str(company.get("company_name") or "").strip()
            if company_name:
                companies.append(company_name)

        price_info = ext.get("price_info")
        if isinstance(price_info, dict):
            raw_price = str(price_info.get("total_price") or "").strip()
            if raw_price:
                label = f"{companies[-1]}: {raw_price}" if companies else raw_price
                prices.append(label)

        subcontractors.extend(_string_items(ext.get("subcontractors")))
        personnel = ext.get("personnel_list") or []
        if isinstance(personnel, list):
            for person in personnel:
                if not isinstance(person, dict):
                    continue
                role = str(person.get("role") or "").strip()
                if role:
                    roles[role] += 1

    lines: list[str] = []
    lines.extend(_format_bid_group("company_info", companies, max_items=max_items_per_group))
    lines.extend(_format_bid_group("technical_highlights", technical, max_items=max_items_per_group))
    lines.extend(_format_bid_group("qualifications", qualifications, max_items=max_items_per_group))
    lines.extend(_format_bid_group("past_projects", projects, max_items=12))
    lines.extend(_format_bid_group("price_info", prices, max_items=8))
    lines.extend(_format_bid_group("subcontractors", subcontractors, max_items=12))
    if roles:
        role_items = [f"{role}: {count}次" for role, count in roles.most_common(10)]
        lines.extend(_format_bid_group("personnel_roles", role_items, max_items=10))
    return lines


def _split_text_units(text: str) -> list[str]:
    normalized = re.sub(r"[ \t\r\f\v]+", " ", text or "").strip()
    if not normalized:
        return []
    units = re.split(r"(?<=[。！？；;])\s*|\n+", normalized)
    return [unit.strip() for unit in units if unit and unit.strip()]


def _score_tender_unit(unit: str) -> int:
    if not unit.strip():
        return 0
    score = 0
    if any(hint in unit for hint in _TENDER_SECTION_HINTS):
        score += 3
    if any(term in unit for term in _INDUSTRY_TERMS):
        score += 2
    if re.search(r"[A-Z]{2,}[A-Za-z0-9-]*", unit):
        score += 2
    if any(term in unit for term in _LOW_VALUE_TENDER_TERMS):
        score -= 2
    if len(unit) > 500:
        score -= 1
    return score


def _normalize_signal(signal: Any) -> dict[str, Any]:
    if isinstance(signal, dict):
        value = (
            signal.get("value")
            or signal.get("evidence")
            or signal.get("text")
            or signal.get("name")
            or ""
        )
        signal_type = signal.get("signal_type") or signal.get("category") or ""
        confidence = signal.get("confidence", 0.5)
    else:
        value = str(signal or "")
        signal_type = "term"
        confidence = 0.5
    return {
        "value": str(value or "").strip(),
        "signal_type": str(signal_type or "").strip().lower(),
        "confidence": _coerce_confidence(confidence),
    }


def _is_strong_signal(signal: dict[str, Any]) -> bool:
    value = str(signal.get("value") or "")
    signal_type = str(signal.get("signal_type") or "")
    confidence = float(signal.get("confidence") or 0.0)
    if len(value) < 4:
        return False
    if confidence >= 0.85 and signal_type in _STRONG_SIGNAL_TYPES:
        return True
    if signal_type in {"domain", "title", "qualification"} and len(value) >= 8:
        return True
    return bool(re.search(r"[A-Z]{2,}[A-Za-z0-9-]*", value)) and confidence >= 0.7


def _parse_context_line(line: str) -> tuple[str, str]:
    text = (line or "").strip()
    if not text.startswith("- "):
        return "", ""
    source = ""
    value = text[2:]
    if "|" in value:
        source_part, value_part = value.split("|", 1)
        source = source_part.replace("来源=", "").strip()
        value = value_part
    if "内容=" in value:
        value = value.split("内容=", 1)[1]
    return source, value.strip()


def _score_signal_text(text: str) -> int:
    if not text.strip():
        return 0
    score = 0
    if any(term in text for term in _INDUSTRY_TERMS):
        score += 2
    if re.search(r"[A-Z]{2,}[A-Za-z0-9-]*", text):
        score += 2
    if any(hint in text for hint in ("资质", "准入", "科研服务", "技术", "工艺", "管线", "压裂")):
        score += 2
    if len(text) >= 12:
        score += 1
    if any(noise in text for noise in ("公司", "大学", "联系电话", "银行账号")) and score < 4:
        score -= 2
    return score


def _infer_signal_type(source: str, value: str) -> str:
    if "qualification" in source or "资质" in value or "准入" in value:
        return "qualification"
    if "technical" in source:
        return "process"
    if "price" in source:
        return "pricing"
    if "tender" in source:
        return "domain"
    if re.search(r"[A-Z]{2,}[A-Za-z0-9-]*", value):
        return "term"
    return "domain"


def _infer_industry_type_from_signal_text(text: str) -> str:
    if any(term in text for term in ("油气", "石油", "天然气", "压裂", "管线钢")):
        return "石油天然气-科研技术服务"
    if any(term in text for term in ("医院", "HIS", "EMR", "PACS", "LIS")):
        return "医疗信息化-系统集成"
    if any(term in text for term in ("市政", "道路", "桥梁", "公用工程")):
        return "市政工程-施工服务"
    if any(term in text for term in ("物业", "园区", "保洁", "安保")):
        return "物业服务-园区运营"
    if any(term in text for term in ("电力", "变电", "输电", "配电")):
        return "电力工程-技术服务"
    return ""


def _coerce_confidence(value: Any) -> float:
    try:
        if isinstance(value, str):
            return {
                "high": 0.9,
                "medium": 0.7,
                "low": 0.4,
                "unknown": 0.0,
            }.get(value.lower(), float(value))
        return float(value)
    except (TypeError, ValueError):
        return 0.5


def _format_bid_group(source: str, items: list[str], *, max_items: int) -> list[str]:
    unique = _dedupe_keep_order(items)
    return [
        f"- 来源=bid_extraction:{source} | 内容={_compact_text(item, max_chars=220)}"
        for item in unique[:max_items]
    ]


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _compact_text(text: str, *, max_chars: int) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars].rstrip() + "..."
