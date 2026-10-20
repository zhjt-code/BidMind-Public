"""串标审查 View 拼装与出口闸门服务（R2-6 拆分自 collusion router）

承载结果视图层的拼装逻辑与对外文本字段的统一清洗（A2 出口闸门 / 防御）。

核心职责：
- FindingItem 列表拼装（兼容新旧两种数据格式）
- LLM 自由文本字段递归 sanitize（pair_id 替换 / 空括号清洗 / 嵌套结构遍历）
- pair_id ↔ 文件对名称双向映射构造
- 旧版 evidences 字段兼容转换

设计原则：
- **纯函数模块**：无类、无全局状态；
- **依赖单向**：本模块不依赖 collusion_status_service / collusion_summary_service；
- **出口闸门归一**：所有 LLM 自由文本字段必须经本模块 sanitize 后才能进入响应体。

参考：
- v2 §八 R2-6 卡片 §6.3 service 公共接口设计
- / A2 阶段：LLM inconclusive / unknown 英文枚举裸出修复
"""

from __future__ import annotations

import re

from app.core.collusion.identity_display import replace_document_identity_tokens
from app.core.i18n import sanitize_user_facing_text
from app.models.collusion_result import CollusionResult
from app.schemas.collusion import (
    CollusionResults,
    FindingItem,
    build_pair_id,
)


# ---------------------------------------------------------------------------
# Regex / 常量
# ---------------------------------------------------------------------------

PAIR_REF_PARAM_RE = re.compile(
    r"\bpair(?:_id)?\s*[:=]\s*(\d+\s*:\s*\d+)\b",
    flags=re.IGNORECASE,
)
EMPTY_PARENS_RE = re.compile(r"[（(]\s*[）)]")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def strip_pdf_suffix(filename: str) -> str:
    """去除 .pdf 后缀（不区分大小写）。"""
    return re.sub(r"\.pdf$", "", str(filename or ""), flags=re.IGNORECASE)


def normalize_pair_ref(pair_ref: str) -> str:
    """归一化 pair_ref：'34:12' / '12:34' / 'pair_id=12:34' 等都统一为 sorted 'min:max'。

    无效输入兜底为去空格的原值。
    """
    parts = [p.strip() for p in str(pair_ref or "").split(":", 1)]
    if len(parts) != 2:
        return str(pair_ref or "").replace(" ", "")
    try:
        return build_pair_id(int(parts[0]), int(parts[1]))
    except (TypeError, ValueError):
        return str(pair_ref or "").replace(" ", "")


def build_pair_ref_labels(results: CollusionResults) -> dict[str, str]:
    """从 results.pair_results 构造 pair_id → '文件名×文件名' 映射。"""
    labels: dict[str, str] = {}
    for pair in results.pair_results or []:
        pair_id = pair.pair_id or build_pair_id(pair.doc_a_id, pair.doc_b_id)
        labels[pair_id] = f"{strip_pdf_suffix(pair.doc_a_name)}×{strip_pdf_suffix(pair.doc_b_name)}"
    return labels


def build_doc_ref_labels(results: CollusionResults) -> dict[int, str]:
    """从 results.pair_results 构造 doc_id → 上传文件名（去 .pdf）映射。"""
    labels: dict[int, str] = {}
    for pair in results.pair_results or []:
        if pair.doc_a_id is not None:
            labels[int(pair.doc_a_id)] = strip_pdf_suffix(pair.doc_a_name)
        if pair.doc_b_id is not None:
            labels[int(pair.doc_b_id)] = strip_pdf_suffix(pair.doc_b_name)
    return labels


def replace_collusion_pair_refs(
    text: str | None,
    pair_labels: dict[str, str],
    doc_labels: dict[int, str] | None = None,
    pair_ordinal_count: int = 0,
) -> str:
    """将 LLM 文案中的内部 pair / 文档身份标识转换为可读文件名。"""
    if not text:
        return ""
    resolved = str(text)

    def _replace_param(match: re.Match[str]) -> str:
        return pair_labels.get(normalize_pair_ref(match.group(1)), "")

    resolved = PAIR_REF_PARAM_RE.sub(_replace_param, resolved)
    for pair_id, label in pair_labels.items():
        resolved = re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(pair_id)}(?![A-Za-z0-9_])",
            label,
            resolved,
        )
    if doc_labels:
        resolved = replace_document_identity_tokens(
            resolved,
            doc_labels,
            pair_ordinal_count=pair_ordinal_count,
        )
    return resolved


def build_evidences_from_legacy(
    quote_a: str | None,
    page_a: str | int | None,
    quote_b: str | None,
    page_b: str | int | None,
    doc_a_id: int,
    doc_b_id: int,
    doc_a_name: str,
    doc_b_name: str,
) -> list[dict]:
    """旧版 finding 的 quote_a/page_a/quote_b/page_b 兼容转换为 evidences 列表。"""
    evidences: list[dict] = []
    if quote_a or page_a:
        evidences.append({
            "doc_id": doc_a_id,
            "doc_name": doc_a_name,
            "quote": quote_a,
            "location": str(page_a) if page_a is not None else None,
        })
    if quote_b or page_b:
        evidences.append({
            "doc_id": doc_b_id,
            "doc_name": doc_b_name,
            "quote": quote_b,
            "location": str(page_b) if page_b is not None else None,
        })
    return evidences


def step_status(pct: int, overall: str) -> str:
    """单步进度状态判定：100→completed / >0→running / else→waiting。"""
    if pct >= 100:
        return "completed"
    if pct > 0:
        return "running"
    return "waiting"


# ---------------------------------------------------------------------------
# FindingItem 拼装（128 行 — 兼容新格式与旧 llm_analysis dimension findings 两套数据源）
# ---------------------------------------------------------------------------


def build_finding_items(
    r: CollusionResult,
    doc_a_id: int,
    doc_b_id: int,
    doc_a_name: str,
    doc_b_name: str,
    pair_id: str,
) -> list:
    """从 CollusionResult 构建结构化 FindingItem 列表

    优先使用 findings 字段（新格式：结构化列表），
    若 findings 为空或旧格式，则回退到 llm_analysis 中各维度的 findings。
    """
    items: list[FindingItem] = []

    # 维度key → (dimension代码, 默认证据级别)
    _DIM_MAP = {
        "d1_personnel":    ("D1", "first"),
        "d2_company":      ("D2", "first"),
        "d3_text":         ("D3", "second"),
        "d4_price":        ("D4", "second"),
        "d5_qualification": ("D5", "second"),
        "d6_format":       ("D6", "third"),
        "d7_abnormal":     ("D7", "third"),
        "d8_metadata":     ("D8", "third"),
    }

    raw_findings = r.findings or []

    has_structured = any(
        isinstance(f, dict) and ("quote_a" in f or "page_a" in f or "detail" in f)
        for f in raw_findings
    )

    if has_structured:
        from app.core.collusion.analyzer_parts.dimension_signals import (
            is_hollow_dimension_signal,
        )
        for f in raw_findings:
            if isinstance(f, dict):
                # 历史空壳维度信号无可核对路径，不进入展示列表
                if is_hollow_dimension_signal(f):
                    continue
                _pa = f.get("page_a")
                _pb = f.get("page_b")
                pa_str = str(_pa) if _pa is not None else None
                pb_str = str(_pb) if _pb is not None else None
                items.append(FindingItem(
                    pair_id=f.get("pair_id") or pair_id,
                    dimension=f.get("dimension", "unknown"),
                    level=f.get("level", "third"),
                    description=f.get("description") or f.get("summary", ""),
                    detail=f.get("detail"),
                    evidences=f.get("evidences") or build_evidences_from_legacy(
                        quote_a=f.get("quote_a"),
                        page_a=_pa,
                        quote_b=f.get("quote_b"),
                        page_b=_pb,
                        doc_a_id=doc_a_id,
                        doc_b_id=doc_b_id,
                        doc_a_name=doc_a_name,
                        doc_b_name=doc_b_name,
                    ),
                    quote_a=f.get("quote_a"),
                    page_a=pa_str,
                    quote_b=f.get("quote_b"),
                    page_b=pb_str,
                    similarity=f.get("similarity"),
                    certainty=f.get("certainty"),
                    is_dimension_signal=bool(f.get("is_dimension_signal")),
                ))
            elif isinstance(f, str):
                items.append(FindingItem(
                    dimension="unknown", level="third", description=f
                ))
    else:
        # 回退：从 llm_analysis 各维度提取
        llm = r.llm_analysis or {}
        for dim_key, (dim_code, default_level) in _DIM_MAP.items():
            dim_data = llm.get(dim_key)
            if not isinstance(dim_data, dict):
                continue
            score = dim_data.get("score", 0)
            if score <= 0:
                continue
            evidence_level = dim_data.get("evidence_level", default_level)
            certainty = dim_data.get("certainty")
            for f in (dim_data.get("findings") or []):
                if isinstance(f, dict):
                    _pa = f.get("page_a")
                    _pb = f.get("page_b")
                    pa_str = str(_pa) if _pa is not None else None
                    pb_str = str(_pb) if _pb is not None else None
                    items.append(FindingItem(
                        pair_id=pair_id,
                        dimension=dim_code,
                        level=evidence_level,
                        description=f.get("summary") or f.get("detail", ""),
                        detail=f.get("detail"),
                        evidences=f.get("evidences") or build_evidences_from_legacy(
                            quote_a=f.get("quote_a"),
                            page_a=_pa,
                            quote_b=f.get("quote_b"),
                            page_b=_pb,
                            doc_a_id=doc_a_id,
                            doc_b_id=doc_b_id,
                            doc_a_name=doc_a_name,
                            doc_b_name=doc_b_name,
                        ),
                        quote_a=f.get("quote_a"),
                        page_a=pa_str,
                        quote_b=f.get("quote_b"),
                        page_b=pb_str,
                        similarity=f.get("similarity"),
                        certainty=certainty,
                    ))
                elif isinstance(f, str) and f.strip():
                    items.append(FindingItem(
                        pair_id=pair_id,
                        dimension=dim_code,
                        level=evidence_level,
                        description=f,
                        certainty=certainty,
                    ))

        # 若维度提取也为空，使用旧的 findings 字段
        if not items:
            for f in raw_findings:
                if isinstance(f, str):
                    items.append(FindingItem(
                        pair_id=pair_id,
                        dimension="unknown", level="third", description=f
                    ))

    return items


# ---------------------------------------------------------------------------
# 出口闸门（A2 阶段 / 防御）
# ---------------------------------------------------------------------------


def sanitize_collusion_results(results: CollusionResults) -> CollusionResults:
    """串标结果出口闸门 -- 对所有 LLM 自由文本字段做用户态清洗（A2 阶段）。

 覆盖 同源问题：narrative_summary / synthesis_summary 等字段
 历史上从 LLM 直通到 UI，导致 inconclusive / unknown 等英文枚举裸出。
 本函数仅修改人可阅读的字符串字段，不动结构化字段（枚举、分数等）。
 幂等：对已是中文的文本调用 N 次结果不变。
 """
    pair_labels = build_pair_ref_labels(results)
    doc_labels = build_doc_ref_labels(results)
    pair_ordinal_count = len(results.pair_results or [])

    def sx(text: str | None) -> str:
        cleaned = sanitize_user_facing_text(
            replace_collusion_pair_refs(
                text,
                pair_labels,
                doc_labels,
                pair_ordinal_count,
            )
        )
        return EMPTY_PARENS_RE.sub("", cleaned).strip()

    def sx_list(items: list[str] | None) -> list[str]:
        cleaned_items = [sx(s) for s in (items or [])]
        return [s for s in cleaned_items if s]

    results.synthesis_summary = sx(results.synthesis_summary)
    results.narrative_summary = sx(results.narrative_summary)
    results.investigation_suggestions = sx_list(results.investigation_suggestions)
    results.data_quality_flags = sx_list(results.data_quality_flags)
    results.profile_quality_flags = sx_list(results.profile_quality_flags)
    results.factual_explanation = sx(results.factual_explanation)
    results.factual_explanation_items = sx_list(results.factual_explanation_items)
    if results.risk_consistency_note:
        results.risk_consistency_note = sx(results.risk_consistency_note)
    results.extraction_warnings = sx_list(results.extraction_warnings)

    for pair in results.pair_results or []:
        if pair.risk_explanation:
            pair.risk_explanation = sx(pair.risk_explanation)
        pair.quality_flags = sx_list(pair.quality_flags)
        pair.consistency_flags = sx_list(pair.consistency_flags)
        for finding in pair.findings or []:
            if finding.description:
                finding.description = sx(finding.description)
            if finding.detail:
                finding.detail = sx(finding.detail)
            for ev in finding.evidences or []:
                if ev.quote:
                    ev.quote = sx(ev.quote)
        for ai in pair.attention_items or []:
            if ai.summary:
                ai.summary = sx(ai.summary)
            if ai.detail:
                ai.detail = sx(ai.detail)
            if ai.source_reason:
                ai.source_reason = sx(ai.source_reason)

    for sg in results.suspected_groups or []:
        if sg.reason:
            sg.reason = sx(sg.reason)

    for grp in results.groups or []:
        if grp.collusion_strategy:
            grp.collusion_strategy = sx(grp.collusion_strategy)
        if grp.key_evidence_summary:
            grp.key_evidence_summary = sx(grp.key_evidence_summary)
        if grp.network_description:
            grp.network_description = sx(grp.network_description)
        grp.investigation_focus = sx_list(grp.investigation_focus)
        for finding in grp.findings or []:
            if finding.description:
                finding.description = sx(finding.description)
            if finding.detail:
                finding.detail = sx(finding.detail)

    for gf in results.global_findings or []:
        if gf.description:
            gf.description = sx(gf.description)

    return results
