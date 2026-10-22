"""评分项换轴重构 P0-3 探针 v2：双源账本（显式声明+组标签推导）覆盖率补测。

职责: 在 v1 探针（显式声明捕获）基础上补测推导账本层——用提取项自带的原文组标签
      （volume_label, 一等物理证据产物）聚合出隐式组基准，实测"双源组级基准覆盖率"、
      "R1双源召回"与"显式捕获误报率"，输出报告供闸门1回炉复议。
不做: 不修改生产代码、不做枚举卷映射、不调用 LLM/DB、
      不读取冻结验证集(sample_corpus_holdout_round2 与 holdout_round2_truth_sealed.md)。
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

_BACKEND_DIR = Path(__file__).parent.parent
_PROJECT_ROOT = _BACKEND_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from scripts.run_scoring_ledger_probe import (  # noqa: E402
    DOWNGRADED_HOLDOUT_KEYS,
    HOLDOUT_CORPUS_DIR,
    LEDGER_TOLERANCE,
    TRAINING_CORPUS_DIR,
    TRAINING_KEYS,
    TRAINING_TRUTH_PATH,
    _ensure_probe_env,
    _load_fixture,
    capture_declaration_ledger,
    normalize_group_label,
    parse_composition_references,
)

REPORT_PATH = _PROJECT_ROOT / "docs" / "reports" / "scoring_ledger_probe_p0_v2.md"

DEFAULT_TOTAL = 100.0


# ── 纯函数区 ─────────────────────────────────────────────────────────────────


def derive_group_sums(item_group_pairs: list[tuple[str, float]]) -> dict[str, float]:
    """推导账本层: 按归一化组标签聚合提取项分值; 空标签项不参与分组（单列观测）。"""
    sums: dict[str, float] = defaultdict(float)
    for label, score in item_group_pairs:
        norm = normalize_group_label(label)
        if norm:
            sums[norm] += float(score or 0.0)
    return dict(sums)


def label_coverage_ratio(item_group_pairs: list[tuple[str, float]]) -> float:
    """提取项分值质量中带非空组标签的占比（推导层可用性的上游前提）。"""
    total = sum(float(s or 0.0) for _l, s in item_group_pairs)
    if total <= 0:
        return 0.0
    labeled = sum(
        float(s or 0.0) for l, s in item_group_pairs if normalize_group_label(l)
    )
    return labeled / total


def classify_basis_level(
    explicit_pairs: list[dict],
    total_candidates: list[float],
    derived_sums: dict[str, float],
    tolerance: float = LEDGER_TOLERANCE,
) -> str:
    """双源基准分级（四级降级链的探针版）:

    L1_explicit_group  显式组对>=2 且 Σ(或折算Σ)≈显式总分候选或默认100;
    L2_derived_group   推导组>=2 且 Σ推导≈显式总分候选或默认100（低置信）;
    L3_doc_total       仅文档级显式总分候选可用;
    L4_none            全无 → REVIEW_REQUIRED。
    """
    candidates = list(total_candidates) + [DEFAULT_TOTAL]

    if len(explicit_pairs) >= 2:
        raw_sum = sum(float(p["score"]) for p in explicit_pairs)
        folded_sum = sum(
            float(p["score"]) * float(p.get("weight") or 1.0) for p in explicit_pairs
        )
        if any(
            abs(total - candidate) <= tolerance
            for total in (raw_sum, folded_sum)
            for candidate in candidates
        ):
            return "L1_explicit_group"

    if len(derived_sums) >= 2:
        derived_total = sum(derived_sums.values())
        if any(abs(derived_total - candidate) <= tolerance for candidate in candidates):
            return "L2_derived_group"

    if total_candidates:
        return "L3_doc_total"
    return "L4_none"


def match_reference_dual(
    ref_pairs: list[dict],
    explicit_pairs: list[dict],
    derived_sums: dict[str, float],
    tolerance: float = LEDGER_TOLERANCE,
) -> tuple[int, int]:
    """R1 双源召回: 参照组对被显式捕获或推导组命中（标签归一相等+分值差<=容差）。"""
    explicit_index: dict[str, set[float]] = defaultdict(set)
    for pair in explicit_pairs:
        norm = normalize_group_label(pair["label"])
        if norm:
            explicit_index[norm].add(float(pair["score"]))
            if pair.get("weight"):
                explicit_index[norm].add(float(pair["score"]) * float(pair["weight"]))
    hit = 0
    for ref in ref_pairs:
        norm = normalize_group_label(ref["label"])
        ref_values = {float(ref["score"])}
        if ref.get("alt_score"):
            ref_values.add(float(ref["alt_score"]))
        explicit_hit = any(
            abs(candidate - value) <= tolerance
            for candidate in explicit_index.get(norm, set())
            for value in ref_values
        )
        derived_hit = norm in derived_sums and any(
            abs(derived_sums[norm] - value) <= tolerance for value in ref_values
        )
        if explicit_hit or derived_hit:
            hit += 1
    return hit, len(ref_pairs)


def count_explicit_false_pairs(
    ref_pairs: list[dict], explicit_pairs: list[dict], tolerance: float = LEDGER_TOLERANCE
) -> tuple[int, list[str]]:
    """显式捕获误报（保守口径）: 捕获组对的归一标签不在参照标签集内。

    注意: 参照注解可能本身不完整, 该口径是误报上限而非精确值, 明细供人工复核。
    """
    ref_labels = {normalize_group_label(r["label"]) for r in ref_pairs}
    false_details: list[str] = []
    for pair in explicit_pairs:
        norm = normalize_group_label(pair["label"])
        if norm and norm not in ref_labels:
            false_details.append(f"{pair['label']}={pair['score']:g}({pair['source']})")
    return len(false_details), false_details


# ── 主流程 ───────────────────────────────────────────────────────────────────


def probe_dual_source_ledger() -> dict:
    """运行双源账本覆盖率补测并返回汇总结构。"""
    _ensure_probe_env()
    from scripts.audit_scoring_legacy_baseline import run_deterministic_extraction_for_sample

    references = parse_composition_references(TRAINING_TRUTH_PATH.read_text(encoding="utf-8"))
    sample_specs = [(k, TRAINING_CORPUS_DIR) for k in TRAINING_KEYS]
    sample_specs += [(k, HOLDOUT_CORPUS_DIR) for k in DOWNGRADED_HOLDOUT_KEYS]

    rows: list[dict] = []
    for key, corpus_dir in sample_specs:
        fixture = _load_fixture(key, corpus_dir)
        ledger = (
            capture_declaration_ledger(fixture)
            if fixture
            else {"strong_pairs": [], "all_pairs": [], "total_candidates": []}
        )
        extracted = run_deterministic_extraction_for_sample(key, corpus_dir=corpus_dir)
        group_pairs = [(l, s) for l, s in extracted.get("item_group_pairs", [])]
        derived = derive_group_sums(group_pairs)
        coverage = label_coverage_ratio(group_pairs)
        level = classify_basis_level(
            ledger["strong_pairs"], ledger["total_candidates"], derived
        )

        ref = references.get(key, {"pairs": [], "annotated": False})
        dual_hit, ref_count = (
            match_reference_dual(ref["pairs"], ledger["all_pairs"], derived)
            if ref.get("annotated")
            else (0, 0)
        )
        false_count, false_details = (
            count_explicit_false_pairs(ref["pairs"], ledger["strong_pairs"])
            if ref.get("annotated")
            else (0, [])
        )

        rows.append({
            "key": key,
            "annotated": bool(ref.get("annotated")),
            "label_coverage": coverage,
            "derived_groups": len(derived),
            "derived_sum": round(sum(derived.values()), 4),
            "basis_level": level,
            "dual_hit": dual_hit,
            "ref_count": ref_count,
            "explicit_strong": len(ledger["strong_pairs"]),
            "false_count": false_count,
            "false_details": false_details,
        })

    annotated = [r for r in rows if r["annotated"]]
    ref_total = sum(r["ref_count"] for r in annotated)
    dual_hit_total = sum(r["dual_hit"] for r in annotated)
    explicit_total = sum(r["explicit_strong"] for r in annotated)
    false_total = sum(r["false_count"] for r in annotated)
    group_levels = ("L1_explicit_group", "L2_derived_group")
    return {
        "rows": rows,
        "dual_recall": dual_hit_total / ref_total if ref_total else 0.0,
        "dual_hit_total": dual_hit_total,
        "ref_total": ref_total,
        "group_basis_rate": sum(1 for r in rows if r["basis_level"] in group_levels) / len(rows),
        "basis_rate": sum(1 for r in rows if r["basis_level"] != "L4_none") / len(rows),
        "false_rate": false_total / explicit_total if explicit_total else 0.0,
        "false_total": false_total,
        "explicit_total": explicit_total,
        "avg_label_coverage": sum(r["label_coverage"] for r in rows) / len(rows),
    }


def format_dual_report(summary: dict, run_date: str = "") -> str:
    """渲染 v2 补测报告。"""
    from datetime import date as _date

    level_cn = {
        "L1_explicit_group": "L1显式组级",
        "L2_derived_group": "L2推导组级(低置信)",
        "L3_doc_total": "L3文档级总分",
        "L4_none": "L4无基准(REVIEW_REQUIRED)",
    }
    lines = [
        "# 评分项换轴重构 P0-3 探针 v2（双源账本覆盖率补测）",
        "",
        f"**运行日期**: {run_date or _date.today()}",
        "**补测动机**: v1 显式声明捕获 R1=56%/79% 未达 90% 阈值，触发账本轴回炉复议；",
        "v2 按修订设计（显式声明 + 组标签推导双源 + 默认百分制低置信兜底）重测地基覆盖。",
        "**推导层来源**: 提取项自带原文组标签 volume_label（一等物理证据，换轴后保留字段）。",
        "",
        "## 汇总指标",
        "",
        "| 指标 | 实测值 | 参考阈值 | 判定 |",
        "|---|---:|---:|---|",
        f"| R1 双源召回（显式∪推导，注解样本） | {summary['dual_recall']:.2%} "
        f"({summary['dual_hit_total']}/{summary['ref_total']}) | >=90% | "
        f"{'达标' if summary['dual_recall'] >= 0.90 else '不达标'} |",
        f"| 组级基准覆盖率（L1∪L2，33样本） | {summary['group_basis_rate']:.2%} | 观测 | - |",
        f"| 基准可用率（L1∪L2∪L3，33样本） | {summary['basis_rate']:.2%} | >=90% | "
        f"{'达标' if summary['basis_rate'] >= 0.90 else '不达标'} |",
        f"| 显式捕获误报上限（注解样本，保守口径） | {summary['false_rate']:.2%} "
        f"({summary['false_total']}/{summary['explicit_total']}) | <=15% | "
        f"{'达标' if summary['false_rate'] <= 0.15 else '不达标'} |",
        f"| 提取项组标签平均覆盖率（分值质量占比） | {summary['avg_label_coverage']:.2%} | 观测 | - |",
        "",
        "## 逐样本明细",
        "",
        "| sample | 注解 | 标签覆盖 | 推导组数 | Σ推导 | 基准层级 | 双源命中/参照 | 显式误报明细 |",
        "|---|---|---:|---:|---:|---|---|---|",
    ]
    for r in summary["rows"]:
        dual = f"{r['dual_hit']}/{r['ref_count']}" if r["annotated"] else "-"
        false_desc = "; ".join(r["false_details"][:4]) or "-"
        lines.append(
            f"| {r['key']} | {'Y' if r['annotated'] else 'N'} | {r['label_coverage']:.0%} "
            f"| {r['derived_groups']} | {r['derived_sum']:g} | {level_cn[r['basis_level']]} "
            f"| {dual} | {false_desc} |"
        )
    lines += [
        "",
        "## 说明",
        "",
        "- L1/L2/L3/L4 为四级降级链探针版；L2 推导基准与 L4 在生产语义下均不得作为通过依据，",
        "  仅提供缺口定位与 REVIEW_REQUIRED 提示（默认100候选恒记低置信）。",
        "- 误报口径为保守上限：捕获组不在真值注解标签集即计入，可能包含真值注解未列的合法组。",
        "- 冻结验证集（structure-35~44）未接触。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    """v2 探针入口。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    summary = probe_dual_source_ledger()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(format_dual_report(summary), encoding="utf-8")
    print(f"[probe-v2] 报告已写入: {REPORT_PATH}")
    print(
        f"[probe-v2] R1双源召回={summary['dual_recall']:.2%} "
        f"({summary['dual_hit_total']}/{summary['ref_total']}) 阈值>=90%"
    )
    print(
        f"[probe-v2] 组级基准覆盖率={summary['group_basis_rate']:.2%} "
        f"基准可用率={summary['basis_rate']:.2%} (阈值>=90%)"
    )
    print(
        f"[probe-v2] 显式误报上限={summary['false_rate']:.2%} "
        f"({summary['false_total']}/{summary['explicit_total']}) 阈值<=15%"
    )
    print(f"[probe-v2] 平均组标签覆盖率={summary['avg_label_coverage']:.2%}")


if __name__ == "__main__":
    main()
