"""标书编写 -- 评分项核计报告构建（纯转换）

职责: 把项目内"有分值"的有效评分项适配为满分校验入参，产出约束确认页核计横幅 payload。
不做: 不查询 DB / 不鉴权（ClauseService 负责）、不做校验算法（score_totals 职责）。

口径(与审查侧差异): 审查侧 scoring_item 来自表头驱动权威提取，无分值项是真·符合性重点项，
      计入 missing 有意义; 写标侧 scoring_criterion 在 LLM 回退时会混入"评标办法总述/格式/
      保证金/报价规则"等无分值告知类(分类过宽), 若计入 missing 会误报"N 项分值缺失"。
      故写标核计**只以有分值评分项(score_weight>0)为准**——合计≈100/按卷达标即通过，
      不足/超出才提示; 无分值项一律不计入分母(它们由 P1 展示层降级为参考组)。
"""

from __future__ import annotations

from typing import Any, Optional

from app.core.extractor.scoring.operational_assessment import (
    attach_suspected_assessment_payload,
    is_suspected_assessment_meta,
)
from app.core.extractor.scoring.score_totals import (
    ScoringTotalsEntry,
    build_totals_report_payload,
)
from app.core.writer.scoring_constants import SCORING_AUTHORITY_SOURCE
from app.models.writing_clause import WritingClause

_SCORING_SUB_TYPE = "scoring_criterion"


def _scoring_source_counts(clauses: list[WritingClause]) -> tuple[int, int]:
    """统计评分项来源: 权威评分表 vs LLM 回退/人工旧数据。"""
    authority = 0
    llm = 0
    for clause in clauses:
        meta = clause.metadata_json if isinstance(clause.metadata_json, dict) else {}
        if meta.get("source") == SCORING_AUTHORITY_SOURCE:
            authority += 1
        else:
            llm += 1
    return authority, llm


def _effective_weight(clause: WritingClause) -> float:
    """评分项有效满分: 非数字或 <=0 视为无有效分值返回 0。"""
    try:
        val = float(getattr(clause, "score_weight", None) or 0)
    except (TypeError, ValueError):
        return 0.0
    return val if val > 0 else 0.0


def _no_valued_scoring_payload(
    scoring_clause_count: int,
    *,
    authority_count: int,
    llm_count: int,
) -> dict[str, Any]:
    """有评分说明条目但全部无分值时的报告: 提示"未识别到有效评分项"而非"N 项分值缺失"。"""
    note = (
        "未识别到带分值的有效评分项，请核对招标文件评分表是否被正确提取"
        f"（当前 {scoring_clause_count} 条评分说明均无分值）"
    )
    return {
        "needs_review": True,
        "note": note,
        "by_volume": {},
        "volume_checks": {},
        "weight_sum_ok": True,
        "item_count": 0,
        "missing_score_count": 0,
        "diagnostics": [note],
        "authority_count": authority_count,
        "llm_count": llm_count,
    }


def build_scoring_report_payload(
    clauses: list[WritingClause],
) -> Optional[dict[str, Any]]:
    """从"有分值"的评分项构建"按卷满分校验"报告 payload。

    - 项目无任何 scoring_criterion 条目 → None（确认页不渲染横幅）。
    - 有条目但全部无分值 → 提示"未识别到有效评分项"（item_count=0，供前端区分文案）。
    - 有有分值项 → 仅以有分值项核计合计（无分值告知类不计入 missing，杜绝误报）。
    """
    scoring_clauses = [c for c in clauses if c.sub_type == _SCORING_SUB_TYPE]
    if not scoring_clauses:
        return None
    authority_count, llm_count = _scoring_source_counts(scoring_clauses)

    valued = [c for c in scoring_clauses if _effective_weight(c) > 0]
    if not valued:
        return _no_valued_scoring_payload(
            len(scoring_clauses),
            authority_count=authority_count,
            llm_count=llm_count,
        )

    entries: list[ScoringTotalsEntry] = []
    suspected_count = 0
    suspected_score = 0.0
    for clause in valued:
        meta = clause.metadata_json if isinstance(clause.metadata_json, dict) else {}
        if is_suspected_assessment_meta(meta):
            suspected_count += 1
            suspected_score += _effective_weight(clause)
            continue
        entries.append(
            ScoringTotalsEntry(
                score_weight=_effective_weight(clause),
                volume=meta.get("volume"),
                volume_label=meta.get("volume_label"),
                lot=meta.get("lot"),
                scheme=meta.get("scheme"),
                volume_weight=meta.get("volume_weight"),
                volume_max=meta.get("volume_max"),
                source_page=getattr(clause, "source_page", None),
            )
        )
    if not entries:
        payload = _no_valued_scoring_payload(
            len(scoring_clauses),
            authority_count=authority_count,
            llm_count=llm_count,
        )
        return attach_suspected_assessment_payload(
            payload, count=suspected_count, score=suspected_score
        )
    payload = build_totals_report_payload(entries)
    if payload is None:
        return attach_suspected_assessment_payload(
            _no_valued_scoring_payload(
                len(scoring_clauses),
                authority_count=authority_count,
                llm_count=llm_count,
            ),
            count=suspected_count,
            score=suspected_score,
        )
    payload["authority_count"] = authority_count
    payload["llm_count"] = llm_count
    return attach_suspected_assessment_payload(
        payload, count=suspected_count, score=suspected_score
    )
