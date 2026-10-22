"""串标分析器 findings/attention 域纯函数

Task 8 Step 4：从 analyzer.py 迁出 finding 提取、规范化、attention 提升、跨字段共享、
一致性检查、风险推导、pair 合同收敛、HOLLOW 证据正则 等纯函数与常量。
主 analyzer.py 顶部 re-export 保持外部 import 路径零变化。

收纳范围（按原文顺序）：
- _has_positive_dimension_score / _has_dimension_payload
- _finalize_pair_contract（pair 合同收敛核心）
- _EVIDENCE_LEVEL_ORDER 常量
- _FLAT_FINDING_RECOVERED_FLAG 常量
- _recover_flattened_findings
- _extract_structured_findings
- _maybe_promote_to_attention / _collect_attention_from_dim
- _normalize_compare_result / _build_evidences_for_pair
- _extract_page_number / _normalize_evidence_text / _compact_evidence_quote
- _has_verifiable_evidence_refs
- _cross_dimension_evidence_share / _normalize_dim_evidence_levels
- _consistency_check / _derive_overall_risk_from_dims
- _HOLLOW_EVIDENCE_PATTERNS 正则常量

设计原则：
- 全部为纯函数 + 常量，无 self 依赖
- 通过 ClassVar/常量从 analyzer_parts.constants 引入 _DIM_KEYS/_DIM_MAP/_CERTAINTY_MIN
- _extract_structured_findings 函数体内 lazy import assess_finding_polarity 保留原行为
"""

from __future__ import annotations

import re as _re

# 补全 import 修复
from typing import Any, Optional

from loguru import logger

from app.core.collusion.analyzer_parts.constants import (
    _CERTAINTY_MIN,
    _DIM_KEYS,
    _DIM_MAP,
)
from app.schemas.collusion import (
    DIMENSION_EVIDENCE_LEVEL_CONTRACT,
    EVIDENCE_LEVEL_CORRECTED_FLAG,
    build_pair_id,
    coerce_evidence_level_for_dim,
    is_flattened_field_string,
    sanitize_quote,
)


def _has_positive_dimension_score(compare_result: dict) -> bool:
    return any(
        isinstance(compare_result.get(k), dict)
        and (compare_result[k].get("score") or 0) > 0
        for k in _DIM_KEYS
    )


def _has_dimension_payload(compare_result: dict) -> bool:
    return any(isinstance(compare_result.get(k), dict) for k in _DIM_KEYS)


def _finalize_pair_contract(
    compare_result: dict,
    *,
    analysis_mode: str,
    similarity_score: float,
    high_threshold: float,
    extraction_degraded: bool = False,
) -> dict:
    """统一收敛单对结果的风险/状态合同。

    核心原则：
    1. 风险等级只表示业务风险，不再兼任“待复核/失败/跳过”语义。
    2. 无法直接形成结论时，通过 decision_state / analysis_state 表达，而非伪装成风险等级。
    """
    result = dict(compare_result or {})
    quality_flags = [str(x) for x in (result.get("quality_flags") or []) if str(x).strip()]

    def _append_flag(flag: str) -> None:
        if flag not in quality_flags:
            quality_flags.append(flag)

    if analysis_mode == "skipped":
        _append_flag("智能筛对跳过，未进入深度分析")
        result["analysis_state"] = "skipped"
        result["decision_state"] = "skipped"
        result["risk_level"] = None
        result["overall_risk"] = "inconclusive"
        result["review_state"] = "unreviewed"
        result["quality_flags"] = quality_flags
        result.setdefault(
            "risk_explanation",
            "该对文件未进入深度分析流程，当前仅能作为待复核记录，不能等同于无风险。",
        )
        return result

    is_hard_failed = (
        analysis_mode == "failed"
        or str(result.get("failure_state", "")).lower() == "failed"
    )
    if is_hard_failed:
        _append_flag("分析失败，当前仅保留占位结果")
        result["analysis_state"] = "failed"
        result["decision_state"] = "failed"
        result["risk_level"] = None
        result["overall_risk"] = "inconclusive"
        result["review_state"] = "failed"
        result["quality_flags"] = quality_flags
        result.setdefault(
            "risk_explanation",
            "该对文件分析过程中发生异常，当前结果仅作待复核占位。",
        )
        return result

    analysis_state = "partial" if extraction_degraded or "degraded" in analysis_mode else "completed"
    positive_dims = _has_positive_dimension_score(result)
    has_dim_payload = _has_dimension_payload(result)
    risk_level_raw = str(result.get("overall_risk") or "inconclusive").lower()

    if extraction_degraded or "degraded" in analysis_mode:
        _append_flag("部分分析链路已降级，结果可信度受限")

    if not has_dim_payload and analysis_mode != "algorithm_only":
        _append_flag("维度分析数据不足，无法形成稳定结论")
        result["analysis_state"] = analysis_state
        result["decision_state"] = "needs_review"
        result["risk_level"] = None
        result["overall_risk"] = "inconclusive"
        result["review_state"] = "needs_review"
        result["quality_flags"] = quality_flags
        result.setdefault("risk_explanation", "维度分析数据不足，当前需人工复核。")
        return result

    if not positive_dims:
        _append_flag("已完成异常证据核查，未发现有效异常信号")
        if analysis_state == "partial":
            _append_flag("当前结论基于降级分析路径，仍建议人工复核")
            result["analysis_state"] = analysis_state
            result["decision_state"] = "needs_review"
            result["risk_level"] = None
            result["overall_risk"] = "inconclusive"
            result["review_state"] = "needs_review"
            result["quality_flags"] = quality_flags
            result["risk_explanation"] = (
                "当前分析链路存在降级或数据缺口，虽未发现异常证据，但暂不直接判定为无风险。"
            )
            return result

        result["analysis_state"] = analysis_state
        result["decision_state"] = "decidable"
        result["risk_level"] = "none"
        result["overall_risk"] = "none"
        result["review_state"] = "completed"
        result["quality_flags"] = quality_flags
        if similarity_score >= high_threshold and analysis_mode != "algorithm_only":
            _append_flag("高相似度已完成核查，当前重合主要表现为通用/模板化内容")
            result["risk_explanation"] = (
                f"向量相似度 {similarity_score:.0%} 较高，但已完成维度核查且未发现异常证据，当前判定为无风险。"
            )
        else:
            result.setdefault("risk_explanation", "已完成当前分析范围内的核查，未发现串标嫌疑。")
        return result

    # 对齐：inconclusive 是合法的"无法判定/信息不足"语义（Prompt 与
    # CollusionRiskLevel 均显式允许该值），不得与 LLM 幻觉/乱码枚举混为一谈。
    # 历史缺陷：下方 invalid_risk_enum 校验集 {high,medium,low,none} 漏掉了
    # inconclusive，导致 LLM 合法返回的"无法判定"被误判为"AI 未返回有效风险
    # 等级"，再经 i18n 显示成"返回值='无法判定'"，文案自相矛盾。此处单列
    # inconclusive，输出中性、可理解的"信息不足→待复核"文案。
    if risk_level_raw == "inconclusive":
        _append_flag("当前有效证据不足以判定串标风险，已转人工复核，建议结合原文进一步核对。")
        result["analysis_state"] = analysis_state
        result["decision_state"] = "needs_review"
        result["risk_level"] = None
        result["overall_risk"] = "inconclusive"
        result["review_state"] = "needs_review"
        result["quality_flags"] = quality_flags
        result["degradation_reason"] = {
            "type": "inconclusive_insufficient_evidence",
            "raw_value": "inconclusive",
            "fallback": "needs_review",
        }
        result.setdefault(
            "risk_explanation",
            "基于当前证据无法判定串标风险（有效证据不足），建议结合原文人工复核。",
        )
        return result

    # P0-4: 真·非标准风险枚举（空值 / 乱码 / LLM 幻觉出
    # {high,medium,low,none,inconclusive} 之外的值 / Gate patch 异常值 / 手工改坏）
    # 才走"无效返回值"路径，收敛为 inconclusive + needs_review，并追加 quality_flag。
    # 文案面向用户：明确告知"AI 未返回有效风险等级"+ raw 值，便于结合
    # narrative_builder 拼装的事实段判读；同时持久化 degradation_reason
    # 字段供下游 narrative_builder 与前端事实段消费。
    if risk_level_raw not in {"high", "medium", "low", "none"}:
        _raw_display = str(risk_level_raw) if risk_level_raw is not None else "空"
        _append_flag(
            f"AI 对本对未返回有效风险等级（返回值={_raw_display!r}），已自动转人工复核，建议结合证据原文判读。"
        )
        result["analysis_state"] = analysis_state
        result["decision_state"] = "needs_review"
        result["risk_level"] = None
        result["overall_risk"] = "inconclusive"
        result["review_state"] = "needs_review"
        result["quality_flags"] = quality_flags
        result["degradation_reason"] = {
            "type": "invalid_risk_enum",
            "raw_value": str(risk_level_raw) if risk_level_raw is not None else "",
            "fallback": "needs_review",
        }
        result.setdefault(
            "risk_explanation",
            f"风险等级字段值 '{risk_level_raw}' 不在允许枚举 {{high,medium,low,none,inconclusive}} 中，已自动降级为待复核。",
        )
        return result

    risk_level = risk_level_raw
    result["analysis_state"] = analysis_state
    result["decision_state"] = "decidable"
    result["risk_level"] = risk_level
    result["overall_risk"] = risk_level
    result["review_state"] = "completed"
    result["quality_flags"] = quality_flags
    return result


_EVIDENCE_LEVEL_ORDER = {"first": 0, "second": 1, "third": 2, "unknown": 3}


_FLAT_FINDING_RECOVERED_FLAG = "flat_finding_recovered"


def _recover_flattened_findings(raw_findings: list) -> tuple[list, bool]:
    """合并 LLM 输出中被拍平为 'key: value' 字符串列表的 finding 组。

 防御 修复后的"未清理尾巴"：当 LLM 偶发把单个 finding 的字段拆成
 `["summary: ...", "detail: ...", "quote_a: ..."]` 这样的字符串列表时，
 schema 层若漏拒，则在此处兜底合并回单条结构化 finding。

 扫描规则：
 - 顺序遍历 raw_findings；遇到 `is_flattened_field_string(...)` 命中的字符串，
 解析其 `key`/`value` 写入当前累积 dict；
 - 同 key 重复出现视为下一条 finding 起点，先 flush 上一条；
 - 遇到非拍平字符串或 dict/其他类型时，先 flush 累积 dict，再原样保留；
 - `page_a` / `page_b` 尝试转 int，`similarity` 尝试转 float，失败保留字符串。

 Returns:
 (合并后的 list, 是否触发恢复)
 """
    if not isinstance(raw_findings, list) or not raw_findings:
        return list(raw_findings or []), False

    output: list = []
    current: dict | None = None
    recovered_any = False

    def _flush() -> None:
        nonlocal current
        if current:
            output.append(current)
        current = None

    for item in raw_findings:
        if is_flattened_field_string(item):
            recovered_any = True
            text = item.strip()
            sep_idx = -1
            for sep in (":", "："):
                idx = text.find(sep)
                if idx >= 0 and (sep_idx < 0 or idx < sep_idx):
                    sep_idx = idx
            if sep_idx < 0:
                continue
            key = text[:sep_idx].strip().lower()
            value: Any = text[sep_idx + 1:].strip()
            if key in {"page_a", "page_b"} and isinstance(value, str):
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    pass
            elif key == "similarity" and isinstance(value, str):
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    pass
            if current is None:
                current = {}
            elif key in current:
                _flush()
                current = {}
            current[key] = value
        else:
            _flush()
            output.append(item)

    _flush()
    return output, recovered_any


def _extract_structured_findings(compare_result: dict, doc_a_id: int, doc_b_id: int) -> list[dict]:
    """从 LLM 对比结果的各维度中提取结构化证据列表

    遍历 d1-d8 各维度，将有效的 findings 加上维度归属和证据级别，
    生成统一格式的证据列表，供 API 和前端直接使用。
    """
    pair_ref = compare_result.get("pair_ref") or {}
    doc_a_name = pair_ref.get("doc_a_name")
    doc_b_name = pair_ref.get("doc_b_name")
    pair_id = compare_result.get("pair_id") or build_pair_id(doc_a_id, doc_b_id)

    result: list[dict] = []
    attention_items: list[dict] = []

    for dim_key, (dim_code, dim_label) in _DIM_MAP.items():
        dim_data = compare_result.get(dim_key)
        if not isinstance(dim_data, dict):
            continue

        score = dim_data.get("score", 0)
        certainty = dim_data.get("certainty", 0)
        if score <= 0 or certainty < _CERTAINTY_MIN:
            _collect_attention_from_dim(
                dim_data, dim_code, attention_items,
            )
            continue

        evidence_level = dim_data.get("evidence_level", "third")
        raw_findings = dim_data.get("findings") or []
        findings, flat_recovered = _recover_flattened_findings(raw_findings)
        if flat_recovered:
            dim_flags = dim_data.setdefault("_quality_flags", [])
            if _FLAT_FINDING_RECOVERED_FLAG not in dim_flags:
                dim_flags.append(_FLAT_FINDING_RECOVERED_FLAG)
            pair_flags = compare_result.setdefault("quality_flags", [])
            if _FLAT_FINDING_RECOVERED_FLAG not in pair_flags:
                pair_flags.append(_FLAT_FINDING_RECOVERED_FLAG)
            logger.warning(
                f"[FlatFindingRecover] dim={dim_code} pair={pair_id} "
                f"检测到 LLM 输出拍平 finding，已合并为 {len(findings)} 条"
            )

        for f in findings:
            if isinstance(f, str):
                if not f.strip():
                    continue
                f = {"summary": f}
            if not isinstance(f, dict):
                continue

            # ── 步骤3: 空心证据拦截 ──
            effective_certainty = certainty
            qa = sanitize_quote(f.get("quote_a")) or ""
            qb = sanitize_quote(f.get("quote_b")) or ""
            if qa and _HOLLOW_EVIDENCE_PATTERNS.search(qa):
                effective_certainty = min(effective_certainty, 0.1)
            if qb and _HOLLOW_EVIDENCE_PATTERNS.search(qb):
                effective_certainty = min(effective_certainty, 0.1)
            if effective_certainty < _CERTAINTY_MIN:
                logger.debug(
                    f"[EvidenceGate] 空心证据被拦截: dim={dim_code}, "
                    f"quote_a='{qa[:40]}', quote_b='{qb[:40]}'"
                )
                continue

            # ── 步骤3.5: 反向定性陈述拦截──
            # LLM 即便有 prompt 红线，仍可能把"未发现/不构成串标"等反向陈述
            # 写入 findings。此类 finding 不应作为正式证据展示，转入关注项。
            from app.core.collusion.finding_polarity import (
                assess_finding_polarity,
                assess_finding_speculative,
            )
            is_negative, neg_pattern = assess_finding_polarity(f)
            if is_negative:
                f.setdefault("rejection_reason", f"反向定性陈述: {neg_pattern}")
                f["_negative_finding"] = True
                logger.debug(
                    f"[EvidenceGate] 反向陈述被拦截转关注项: dim={dim_code}, "
                    f"pattern='{neg_pattern}', summary='{str(f.get('summary') or '')[:40]}'"
                )
                _maybe_promote_to_attention(f, dim_code, attention_items)
                continue

            # ── 步骤3.6: 推测语境拦截（, C2）──
            # LLM 输出 "假设/疑似/可能/例如" 等推测词时不得作为正式证据。
            # 命中后转入 attention_items.speculative_finding，前端单独样式展示。
            is_speculative, spec_pattern = assess_finding_speculative(f)
            if is_speculative:
                f.setdefault("rejection_reason", f"推测性观察(非实证): {spec_pattern}")
                f["_speculative_finding"] = True
                logger.debug(
                    f"[EvidenceGate] 推测语境被拦截转关注项: dim={dim_code}, "
                    f"pattern='{spec_pattern}', summary='{str(f.get('summary') or '')[:40]}'"
                )
                _maybe_promote_to_attention(f, dim_code, attention_items)
                continue

            if f.get("verified") is False:
                _maybe_promote_to_attention(f, dim_code, attention_items)
                continue

            if f.get("_missing_evidence_ref"):
                _maybe_promote_to_attention(f, dim_code, attention_items)
                pair_flags = compare_result.setdefault("quality_flags", [])
                missing_flag = f"{dim_code} 存在缺少原文引用的派生分析项，已从正式证据降级"
                if missing_flag not in pair_flags:
                    pair_flags.append(missing_flag)
                continue

            evidences = _build_evidences_for_pair(
                f=f,
                doc_a_id=doc_a_id,
                doc_b_id=doc_b_id,
                doc_a_name=doc_a_name,
                doc_b_name=doc_b_name,
            )
            if not _has_verifiable_evidence_refs(evidences):
                f["_missing_evidence_ref"] = True
                f.setdefault(
                    "rejection_reason",
                    "该 finding 缺少可复核原文引用，未作为正式证据展示",
                )
                _maybe_promote_to_attention(f, dim_code, attention_items)
                pair_flags = compare_result.setdefault("quality_flags", [])
                missing_flag = f"{dim_code} 存在缺少原文引用的派生分析项，已从正式证据降级"
                if missing_flag not in pair_flags:
                    pair_flags.append(missing_flag)
                continue

            entry = {
                "pair_id": pair_id,
                "dimension": dim_code,
                "dimension_label": dim_label,
                "level": evidence_level,
                "description": f.get("summary") or f.get("detail", ""),
                "detail": f.get("detail"),
                "evidences": evidences,
                "quote_a": sanitize_quote(f.get("quote_a")),
                "page_a": str(f["page_a"]) if f.get("page_a") is not None else None,
                "quote_b": sanitize_quote(f.get("quote_b")),
                "page_b": str(f["page_b"]) if f.get("page_b") is not None else None,
                "similarity": f.get("similarity"),
                "certainty": effective_certainty,
                "verified": f.get("verified"),
            }
            # 透传跨字段串联引擎填充的扩展字段
            for ext_key in ("risk_pattern", "source_field_a", "source_field_b", "legal_reference"):
                if f.get(ext_key) is not None:
                    entry[ext_key] = f.get(ext_key)
            result.append(entry)

    compare_result["_attention_items"] = attention_items
    return result


def _maybe_promote_to_attention(
    f: dict, dim_code: str, attention_items: list[dict],
) -> None:
    """将有提醒价值的被降级 finding 提升为关注项"""
    if f.get("_negative_finding"):
        attention_items.append({
            "category": "negative_finding",
            "dimension": dim_code,
            "summary": f.get("summary") or f"{dim_code} 维度未发现串标线索",
            "detail": f.get("detail"),
            "source_reason": f.get("rejection_reason") or "LLM 已分析但确认未发现串标证据",
        })
        return

    # C2: 推测性观察单独分类，前端用更明显的样式提示用户"非实证"
    if f.get("_speculative_finding"):
        attention_items.append({
            "category": "speculative_finding",
            "dimension": dim_code,
            "summary": f.get("summary") or f"{dim_code} 维度仅有推测性观察",
            "detail": f.get("detail"),
            "source_reason": (
                f.get("rejection_reason")
                or "包含推测性表述（假设/疑似/例如等），缺乏可复核实证，仅供参考"
            ),
        })
        return

    if f.get("business_relationship"):
        attention_items.append({
            "category": "business_relationship",
            "dimension": dim_code,
            "summary": f.get("summary") or "检测到业务往来关系",
            "detail": (
                f.get("detail") or
                "两家投标企业存在甲乙方合作关系, "
                "本身不构成串标证据, 但说明双方存在联络渠道, 建议关注是否有其他关联信号。"
            ),
            "source_reason": f.get("rejection_reason") or "业务往来不构成串标证据但值得关注",
        })
        return

    if f.get("_missing_evidence_ref"):
        attention_items.append({
            "category": "unverified_quote",
            "dimension": dim_code,
            "summary": f.get("summary") or "分析项缺少可复核原文引用",
            "detail": f.get("detail"),
            "source_reason": f.get("rejection_reason") or "缺少可复核原文引用，未作为正式证据展示",
        })
        return

    if f.get("verification_warning") and f.get("verified") is False:
        quote_a = sanitize_quote(f.get("quote_a")) or ""
        quote_b = sanitize_quote(f.get("quote_b")) or ""
        has_substance = (len(quote_a) > 15 or len(quote_b) > 15) and not _HOLLOW_EVIDENCE_PATTERNS.search(quote_a + quote_b)
        if has_substance:
            attention_items.append({
                "category": "unverified_quote",
                "dimension": dim_code,
                "summary": f.get("summary") or "引文有实质内容但页码无法验证",
                "detail": f.get("detail"),
                "source_reason": f.get("rejection_reason") or "引文有实质内容但页码或位置无法在原始文档中验证",
            })


def _collect_attention_from_dim(
    dim_data: dict, dim_code: str, attention_items: list[dict],
) -> None:
    """从被门控归零的维度中收集有价值的关注项"""
    if not dim_data.get("gate_rejected"):
        return
    for f in dim_data.get("findings") or []:
        if not isinstance(f, dict):
            continue
        _maybe_promote_to_attention(f, dim_code, attention_items)


def _normalize_compare_result(
    compare_result: dict,
    doc_a_id: int,
    doc_b_id: int,
    doc_a_name: str,
    doc_b_name: str,
) -> dict:
    """统一补齐 pair_id/pair_ref 与 findings 结构，避免位置型错配"""
    result = dict(compare_result or {})
    pair_id = build_pair_id(doc_a_id, doc_b_id)
    result["pair_id"] = pair_id
    result["pair_ref"] = {
        "pair_id": pair_id,
        "doc_a_id": doc_a_id,
        "doc_b_id": doc_b_id,
        "doc_a_name": doc_a_name,
        "doc_b_name": doc_b_name,
    }

    for dim_key in _DIM_KEYS:
        dim = result.get(dim_key)
        if not isinstance(dim, dict):
            continue
        normalized_findings = []
        for finding in dim.get("findings") or []:
            if isinstance(finding, str):
                finding = {"summary": finding, "detail": finding}
            if not isinstance(finding, dict):
                continue
            finding["pair_id"] = pair_id
            if not finding.get("evidences"):
                finding["evidences"] = _build_evidences_for_pair(
                    f=finding,
                    doc_a_id=doc_a_id,
                    doc_b_id=doc_b_id,
                    doc_a_name=doc_a_name,
                    doc_b_name=doc_b_name,
                )
            normalized_findings.append(finding)
        dim["findings"] = normalized_findings

    return result


def _build_evidences_for_pair(
    f: dict,
    doc_a_id: int,
    doc_b_id: int,
    doc_a_name: Optional[str],
    doc_b_name: Optional[str],
) -> list[dict]:
    """将旧的 quote_a/quote_b 格式映射为结构化 evidence 列表"""
    evidences = []
    quote_a = sanitize_quote(f.get("quote_a"))
    page_a = f.get("page_a")
    quote_b = sanitize_quote(f.get("quote_b"))
    page_b = f.get("page_b")

    if quote_a or page_a:
        evidences.append(
            {
                "doc_id": doc_a_id,
                "doc_name": doc_a_name,
                "quote": quote_a,
                "location": str(page_a) if page_a is not None else None,
            }
        )
    if quote_b or page_b:
        evidences.append(
            {
                "doc_id": doc_b_id,
                "doc_name": doc_b_name,
                "quote": quote_b,
                "location": str(page_b) if page_b is not None else None,
            }
        )
    return evidences


def _extract_page_number(page_ref: object) -> int | None:
    if page_ref is None:
        return None
    match = _re.search(r"第?\s*(\d+)\s*页?|P\s*(\d+)", str(page_ref), _re.IGNORECASE)
    if not match:
        return None
    raw = match.group(1) or match.group(2)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _normalize_evidence_text(text: object) -> str:
    if text is None:
        return ""
    return _re.sub(r"\s+", "", str(text))


def _compact_evidence_quote(text: str | None, max_chars: int = 260) -> str | None:
    if not text:
        return None
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    compact = " ".join(lines)
    if not compact:
        return None
    return compact[:max_chars] + ("..." if len(compact) > max_chars else "")


def _has_verifiable_evidence_refs(evidences: object) -> bool:
    if not isinstance(evidences, list):
        return False
    for ev in evidences:
        if not isinstance(ev, dict):
            continue
        if ev.get("doc_id") is None:
            continue
        if sanitize_quote(ev.get("quote")):
            return True
    return False


def _normalize_dim_evidence_levels(compare_result: dict) -> dict:
    """: 按维度业务契约（DIMENSION_EVIDENCE_LEVEL_CONTRACT）强制矫正 evidence_level。

 维度-级别契约:
 - d1_personnel / d2_company → "first"
 - d3_text / d4_price / d5_qualification → 限制在 {"second","third"} 内,
 非法值覆写为 "second"
 - d6_format / d7_abnormal / d8_metadata → "third"

 被矫正的维度会在 dim["_quality_flags"] 追加 EVIDENCE_LEVEL_CORRECTED_FLAG
 标记，便于审计追踪。dim 为 None 或非 dict 时跳过。
 """
    for dim_key in DIMENSION_EVIDENCE_LEVEL_CONTRACT:
        dim = compare_result.get(dim_key)
        if not isinstance(dim, dict):
            continue
        current = dim.get("evidence_level")
        target, corrected = coerce_evidence_level_for_dim(dim_key, current)
        if not corrected:
            continue
        dim["evidence_level"] = target
        flags = dim.setdefault("_quality_flags", [])
        if EVIDENCE_LEVEL_CORRECTED_FLAG not in flags:
            flags.append(EVIDENCE_LEVEL_CORRECTED_FLAG)
        logger.warning(
            f"[EvidenceLevelNormalize] {dim_key}: '{current}' → '{target}'"
        )
    return compare_result


def _consistency_check(compare_result: dict, similarity_score: float) -> dict:
    """一致性检查：标记矛盾信号 + 基于维度得分推导 overall_risk"""
    # 风险判定与证据落库前先按维度契约矫正 evidence_level
    compare_result = _normalize_dim_evidence_levels(compare_result)

    flags: list[str] = []

    d1 = compare_result.get("d1_personnel")
    d2 = compare_result.get("d2_company")
    d3 = compare_result.get("d3_text")

    d1_score = d1.get("score", 0) if isinstance(d1, dict) else 0
    d1_cert = d1.get("certainty", 0) if isinstance(d1, dict) else 0
    d2_score = d2.get("score", 0) if isinstance(d2, dict) else 0
    d3_score = d3.get("score", 0) if isinstance(d3, dict) else 0
    d3_cert = d3.get("certainty", 0) if isinstance(d3, dict) else 0

    if similarity_score >= 0.7 and d3_score > 0 and d3_cert < _CERTAINTY_MIN:
        flags.append(
            "向量相似度高且D3有重叠但确定性低，建议核查是否为模板导致"
        )
    elif similarity_score >= 0.7 and d3_score == 0:
        flags.append(
            f"向量相似度{similarity_score:.0%}但D3文本雷同得分为0，可能存在分析遗漏"
        )

    if d1_score > 0.5 and d1_cert >= _CERTAINTY_MIN and d2_score == 0:
        flags.append(
            f"D1人员重叠得分{d1_score:.2f}但D2企业关联得分为0，建议核查企业关系"
        )

    compare_result["consistency_flags"] = flags
    compare_result = _derive_overall_risk_from_dims(compare_result)
    return compare_result


def _derive_overall_risk_from_dims(compare_result: dict) -> dict:
    """三级证据体系推导 overall_risk（certainty门控 + evidence_level感知版）

 certainty < _CERTAINTY_MIN 的维度不参与风险判定。
 D3/D4/D5 若 LLM 返回 evidence_level="third"（行业通用重叠），
 则降级为三级参考，不作为二级证据候选。: D1/D2 弱命中（score>0 但未达 first_hits 强阈值）按"弱一级=类二级强佐证"
 参与 MEDIUM/LOW 判定，避免被静默丢弃。但弱一级不参与 HIGH 凑数，遵循
 "宁漏报三级，不错报一级"原则。

 HIGH: 任一 D1/D2 score>=0.5 + certainty>=0.7（强一级直接定性）
 OR >=2 项 D3/D4/D5(evidence_level!=third) score>0.3 + certainty>=0.5
 MEDIUM: 单项 D3/D4/D5(evidence_level!=third) 或 D1/D2 弱一级
 score>0.3 + certainty>=0.4
 OR >=2 三级 + 1 二级/弱一级(均 certainty>=0.3)
 LOW: >=2 三级 score>0 + certainty>=0.3
 OR 单项二级/弱一级 score>0 + certainty>=0.3 但 <0.4
 NONE: 所有维度 score==0 或 certainty 全低于门控
 INCONCLUSIVE: 所有维度均无数据(全 None)
 """
    first_level = ("d1_personnel", "d2_company")
    second_level = ("d3_text", "d4_price", "d5_qualification")
    third_level = ("d6_format", "d7_abnormal", "d8_metadata")

    def _get_sc(key: str) -> tuple[float | None, float]:
        dim = compare_result.get(key)
        if not isinstance(dim, dict):
            return None, 0.0
        s = dim.get("score")
        return s, dim.get("certainty", 0.0)

    def _get_evidence_level(key: str) -> str:
        dim = compare_result.get(key)
        if not isinstance(dim, dict):
            return "unknown"
        return dim.get("evidence_level", "unknown")

    def _is_effective_second(key: str) -> bool:
        """D3/D4/D5 只有 evidence_level 不为 third 时才视为有效二级证据"""
        return _get_evidence_level(key) != "third"

    # INCONCLUSIVE: 无任何维度数据(LLM全部失败)
    has_any = any(_get_sc(k)[0] is not None for k in _DIM_KEYS)
    if not has_any:
        compare_result["overall_risk"] = "inconclusive"
        compare_result["risk_explanation"] = "所有维度均无分析数据，无法判定风险"
        return compare_result

    # D3/D4/D5 中 evidence_level="third" 的维度降级为三级参考
    demoted_to_third = [
        k for k in second_level
        if (_get_sc(k)[0] or 0) > 0 and _get_sc(k)[1] >= _CERTAINTY_MIN
        and not _is_effective_second(k)
    ]

    # 各级别命中统计(全部加入certainty门控 + evidence_level感知)
    first_hits = [
        k for k in first_level
        if (_get_sc(k)[0] or 0) >= 0.5 and _get_sc(k)[1] >= 0.7
    ]
    # D1/D2 score>0 但未达 first_hits 强阈值的弱一级证据
    first_weak = [
        k for k in first_level
        if (_get_sc(k)[0] or 0) > 0 and _get_sc(k)[1] >= _CERTAINTY_MIN
        and k not in first_hits
    ]
    first_weak_medium = [
        k for k in first_weak
        if (_get_sc(k)[0] or 0) > 0.3 and _get_sc(k)[1] >= 0.4
    ]

    second_high = [
        k for k in second_level
        if (_get_sc(k)[0] or 0) > 0.3 and _get_sc(k)[1] >= 0.5
        and _is_effective_second(k)
    ]
    second_medium = [
        k for k in second_level
        if (_get_sc(k)[0] or 0) > 0.3 and _get_sc(k)[1] >= 0.4
        and _is_effective_second(k)
    ]
    second_any = [
        k for k in second_level
        if (_get_sc(k)[0] or 0) > 0 and _get_sc(k)[1] >= _CERTAINTY_MIN
        and _is_effective_second(k)
    ]
    third_active = [
        k for k in third_level
        if (_get_sc(k)[0] or 0) > 0 and _get_sc(k)[1] >= _CERTAINTY_MIN
    ] + demoted_to_third

    def _dim_labels(keys: list[str]) -> str:
        return "、".join(_DIM_MAP.get(k, ("", k))[1] for k in keys)

    explanations: list[str] = []

    # ── HIGH ──
    if first_hits:
        compare_result["overall_risk"] = "high"
        explanations.append(f"发现直接定性证据: {_dim_labels(first_hits)}")
        compare_result["risk_explanation"] = "; ".join(explanations)
        return compare_result

    if len(second_high) >= 2:
        compare_result["overall_risk"] = "high"
        explanations.append(f"多项强力佐证交叉验证: {_dim_labels(second_high)}")
        compare_result["risk_explanation"] = "; ".join(explanations)
        return compare_result

    # ── MEDIUM ──
    # 二级强佐证 与 D1/D2 弱一级 等价进入 MEDIUM 候选
    medium_candidates = list(second_medium) + list(first_weak_medium)
    if medium_candidates:
        compare_result["overall_risk"] = "medium"
        if first_weak_medium and not second_medium:
            explanations.append(
                f"发现弱一级佐证(确定性不足以直接定性): "
                f"{_dim_labels(first_weak_medium)}"
            )
        elif first_weak_medium:
            explanations.append(
                f"发现强力佐证及弱一级佐证: "
                f"{_dim_labels(second_medium + first_weak_medium)}"
            )
        else:
            explanations.append(f"发现强力佐证: {_dim_labels(second_medium)}")
        compare_result["risk_explanation"] = "; ".join(explanations)
        return compare_result

    if len(third_active) >= 2 and (len(second_any) + len(first_weak)) >= 1:
        compare_result["overall_risk"] = "medium"
        explanations.append(
            f"多项辅助参考与佐证叠加: "
            f"{_dim_labels(third_active + second_any + first_weak)}"
        )
        compare_result["risk_explanation"] = "; ".join(explanations)
        return compare_result

    # ── LOW ──
    if len(third_active) >= 2:
        compare_result["overall_risk"] = "low"
        explanations.append(f"发现多项辅助参考: {_dim_labels(third_active)}")
        compare_result["risk_explanation"] = "; ".join(explanations)
        return compare_result

    # 二级弱命中 与 D1/D2 弱一级 等价进入 LOW 候选
    low_candidates = list(second_any) + list(first_weak)
    if low_candidates:
        compare_result["overall_risk"] = "low"
        if first_weak and not second_any:
            explanations.append(
                f"发现弱一级证据但确定性不足: {_dim_labels(first_weak)}"
            )
        elif first_weak:
            explanations.append(
                f"发现佐证及弱一级证据但确定性不足: "
                f"{_dim_labels(second_any + first_weak)}"
            )
        else:
            explanations.append(
                f"发现佐证但确定性不足: {_dim_labels(second_any)}"
            )
        compare_result["risk_explanation"] = "; ".join(explanations)
        return compare_result

    if third_active:
        compare_result["overall_risk"] = "low"
        explanations.append(f"仅发现辅助参考: {_dim_labels(third_active)}")
        compare_result["risk_explanation"] = "; ".join(explanations)
        return compare_result

    # ── NONE ──
    compare_result["overall_risk"] = "none"
    compare_result["risk_explanation"] = "所有维度均未发现串标嫌疑"
    return compare_result


_HOLLOW_EVIDENCE_PATTERNS = _re.compile(
    r"的具体描述|缺少的.*描述|相同的.*模式|具体的.*参数"
    r"|相关原文摘录|技术参数的详细描述|核心内容|格式完全相同$"
    r"|相同的错别字和非标准表述$|相同的内容遗漏模式$"
)
