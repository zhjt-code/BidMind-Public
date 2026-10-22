"""串标分析器 -- 维度信号合成（T1 治理 + 短板补齐）

职责: 为"有维度得分(score>0)但未形成幸存正式证据"且已产出可核对实质内容
 的维度，合成可展示的参考级"维度信号项"；复用 finding 的 summary/detail
 与单边页码定位，保证条目有实际信息量、交用户审定。
不做: 不修改任何维度得分；不重新判定 pair/group 风险等级；不复活反向定性/
 推测性 finding 为信号正文(它们归属关注项)；不伪造原文引用；不为
 "仅有分数、无可核对内容"的维度合成空壳占位项。

设计背景:
 维度"评分通道"(直接读 score 写维度字段 → 矩阵/雷达)与"证据展示通道"
 (_extract_structured_findings 多重门槛)相互独立。维度有分但缺"成对可复核
 引文"时正式证据被剥离。早期信号项仅拼通用模板("得分X但无原文引用")，
 逻辑自相矛盾且无信息量；后改为优先复用维度已有的 summary/detail
 与单边定位。 进一步取消无实质内容时的空壳回退——分数已由维度
 得分条与重合片段承载，空壳"参考信息"不提供可操作核对路径。

约束:
 - 信号项 level 固定为 "unknown"（前端渲染为"参考信息"，遵循 Golden Rule 4
 「宁漏报三级，不错报一级」，不抬升为 first/second/third）。
 - 信号项必带 detail，确保 collusion_view_service.build_finding_items 的
 has_structured 判定为真，避免回退路径忽略 r.findings 中的信号项。
 - 反向定性 / 推测性 / 业务往来类 finding 不作为信号正文（归属 attention_items）。
 - 无实质文本且无定位证据时不合成信号。
"""

from __future__ import annotations

from typing import Any

from app.core.collusion.analyzer_parts.constants import _DIM_MAP
from app.core.collusion.analyzer_parts.findings import (
    _HOLLOW_EVIDENCE_PATTERNS,
    _build_evidences_for_pair,
)
from app.schemas.collusion import sanitize_quote

_SIGNAL_LEVEL = "unknown"
_MIN_SUBSTANCE_CHARS = 4
# 历史空壳信号正文固定含此标记，供 view/报告层过滤已落库数据
_HOLLOW_SIGNAL_MARKER = "结构化/算法分析检出信号"


def is_hollow_dimension_signal(finding: Any) -> bool:
    """识别无可操作核对路径的空壳维度信号项。

    兼容 dict 与带属性的 FindingItem/ORM 投影；仅匹配历史空壳模板，
    不误伤带实质内容的维度信号项。
    """
    if isinstance(finding, dict):
        is_signal = bool(finding.get("is_dimension_signal"))
        desc = str(finding.get("description") or "")
    else:
        is_signal = bool(getattr(finding, "is_dimension_signal", False))
        desc = str(getattr(finding, "description", None) or "")
    if not is_signal:
        return False
    return _HOLLOW_SIGNAL_MARKER in desc


def _covered_dimensions(structured_findings: list[dict]) -> set[str]:
    """收集已有正式证据条目覆盖的维度代号（大写 D1-D8）。"""
    covered: set[str] = set()
    for f in structured_findings or []:
        if isinstance(f, dict):
            dim = f.get("dimension")
            if dim:
                covered.add(str(dim).upper())
    return covered


def _is_excluded_finding(f: dict) -> bool:
    """反向/推测/业务往来/闸门拒绝/撞车引文不应作为信号正文。

    不排除 _missing_evidence_ref：缺成对引文正是信号项的来源
    （正式证据已剥离，仍可用 summary/页码供核对）。
    """
    return bool(
        f.get("_negative_finding")
        or f.get("_speculative_finding")
        or f.get("business_relationship")
        or f.get("verified") is False
        or f.get("_quote_collision")
    )


def _substantive_text(value: object) -> str:
    """提取非空、非空心的分析文本。"""
    if not value:
        return ""
    text = str(value).strip()
    if len(text) < _MIN_SUBSTANCE_CHARS:
        return ""
    if _HOLLOW_EVIDENCE_PATTERNS.search(text):
        return ""
    return text


def _collect_dimension_substance(
    dim_data: dict,
    doc_a_id: int,
    doc_b_id: int,
    doc_a_name: object,
    doc_b_name: object,
) -> tuple[str, str, list[dict]]:
    """从维度的被降级 finding 中提取可展示的实质内容。

    Returns:
        (summary_text, detail_text, evidences) -- 任一为空表示该维度无具体依据。
    """
    summaries: list[str] = []
    details: list[str] = []
    evidences: list[dict] = []

    for f in dim_data.get("findings") or []:
        if not isinstance(f, dict) or _is_excluded_finding(f):
            continue
        summary = _substantive_text(f.get("summary"))
        detail = _substantive_text(f.get("detail"))
        if summary and summary not in summaries:
            summaries.append(summary)
        if detail and detail not in details:
            details.append(detail)
        # 单边页码/引文定位也尽量带出，供用户跳转核对（best-effort，可能仅有页码）
        for ev in _build_evidences_for_pair(
            f=f, doc_a_id=doc_a_id, doc_b_id=doc_b_id,
            doc_a_name=doc_a_name, doc_b_name=doc_b_name,
        ):
            if ev.get("quote"):
                ev = dict(ev)
                ev["quote"] = sanitize_quote(ev.get("quote"))
            evidences.append(ev)

    summary_text = "；".join(summaries[:2])
    detail_text = "\n".join(details[:2])
    return summary_text, detail_text, evidences


_VERIFY_NOTE = (
    "（该维度得分来自结构化字段/算法分析，未绑定可逐句复核的成对原文引用；"
    "是否成立请结合上方综合研判与原文人工核对。）"
)


def synthesize_dimension_signal_findings(
    compare_result: dict,
    structured_findings: list[dict],
    doc_a_id: int,
    doc_b_id: int,
) -> list[dict]:
    """为有得分、无正式证据、但有可核对实质内容的维度合成参考级信号项。

 仅在能复用 summary/detail 或定位证据时产出；确无实质内容时不合成
 （分数已由维度得分条/重合片段承载，空壳占位无核对路径，见）。
 """
    if not isinstance(compare_result, dict):
        return []

    covered = _covered_dimensions(structured_findings)
    pair_id = compare_result.get("pair_id")
    pair_ref = compare_result.get("pair_ref") or {}
    doc_a_name = pair_ref.get("doc_a_name")
    doc_b_name = pair_ref.get("doc_b_name")
    signals: list[dict] = []

    for dim_key, (dim_code, dim_label) in _DIM_MAP.items():
        if dim_code in covered:
            continue
        dim_data = compare_result.get(dim_key)
        if not isinstance(dim_data, dict):
            continue
        score = dim_data.get("score") or 0
        if score <= 0:
            continue

        certainty = dim_data.get("certainty") or 0.0
        summary_text, detail_text, evidences = _collect_dimension_substance(
            dim_data, doc_a_id, doc_b_id, doc_a_name, doc_b_name,
        )

        # 无可核对实质内容时不合成空壳信号
        if not summary_text and not detail_text and not evidences:
            continue

        score_part = f"得分 {int(score * 100)}%"
        cert_part = f"、确定性 {int(certainty * 100)}%" if certainty else ""
        if summary_text or detail_text:
            description = f"{dim_label}：{summary_text or detail_text}"
            detail = (
                f"{detail_text or summary_text}\n\n"
                f"本维度{score_part}{cert_part}。{_VERIFY_NOTE}"
            )
        else:
            # 仅有页码/定位：仍可跳转核对，避免空标题
            description = f"{dim_label}：检出维度信号，已提供原文定位供核对"
            detail = f"本维度{score_part}{cert_part}。{_VERIFY_NOTE}"

        signals.append({
            "pair_id": pair_id,
            "dimension": dim_code,
            "level": _SIGNAL_LEVEL,
            "description": description,
            "detail": detail,
            "evidences": evidences,
            "certainty": certainty if certainty else None,
            "is_dimension_signal": True,
        })

    return signals
