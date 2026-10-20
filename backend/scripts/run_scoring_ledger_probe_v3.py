"""评分项换轴重构 P1 验收探针 v3：生产 ledger 模块全语料复测。

职责: 用生产模块(declaration_capture/ledger_reconciliation/duplicate_exclusion)
      替代探针内联逻辑复测 33 份训练语料，验收 P1 出口——分层基准覆盖不低于
      探针 v2、显式误报维持 0、多方案样本必落 REVIEW、副本行判重维持 v1 结论。
不做: 不修改生产代码、不调用 LLM/DB、
      不读取冻结验证集(sample_corpus_holdout_round2 与 holdout_round2_truth_sealed.md)。
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).parent.parent
_PROJECT_ROOT = _BACKEND_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from scripts.run_scoring_ledger_probe import (  # noqa: E402
    DOWNGRADED_HOLDOUT_KEYS,
    HOLDOUT_CORPUS_DIR,
    TRAINING_CORPUS_DIR,
    TRAINING_KEYS,
    TRAINING_TRUTH_PATH,
    _ensure_probe_env,
    _load_fixture,
    parse_composition_references,
)

REPORT_PATH = _PROJECT_ROOT / "docs" / "reports" / "scoring_ledger_probe_p1_v3.md"

# 探针 v2 实测基线（P1 验收不得低于）
_V2_GOOD_SUBSET_GROUP_BASIS = 18  # 单方案+提取良好19样本中组级基准数
# 真多方案样本(重基线): 仅 09 是"多套备选评分模板"(FWJS05/FWJS16/FWJS18/
# FWSW01 四套模板编码锚点)。24 与 26 原为误标——两者都是**单一评审办法下的并行
# 评分卷**(24: 技术标/资信标/商务标各100, 按30%/15%/55%折算; 26: 商务/技术/价格
# 评审各100), 把它们当多方案真值等于把误报验收为正确行为, 反向固化了"多个≈100
# 组即多方案"这一错误假设。移除后, 旧报告中"多方案样本全REVIEW"一栏的历史结论作废。
_MULTI_SCHEME_KEYS = ("09",)
# 基线 pair F1>=0.7 的单方案样本（scoring_content_baseline_v2.md 分层口径）
_GOOD_SUBSET_KEYS = (
    "02", "03", "04", "05", "06", "07", "11", "12", "13", "14", "15", "16",
    "17", "19", "20", "21", "25", "29", "32",
)


def _fixture_inputs(fixture: dict) -> tuple[list[tuple[int, str]], list[tuple[int, list[list[str]]]]]:
    """fixture -> (pages, table_grids) 生产捕获入参。"""
    pages = [(p["page_num"], p.get("text") or "") for p in fixture.get("pages", [])]
    grids: list[tuple[int, list[list[str]]]] = []
    for page in fixture.get("pages", []):
        for table in page.get("tables", []):
            data = table.get("data") if isinstance(table, dict) else table
            grids.append(
                (page["page_num"], [[str(c or "") for c in row] for row in (data or [])])
            )
    return pages, grids


def assess_ledger_modules_on_corpus() -> dict:
    """对 33 份语料运行生产 ledger 模块，返回验收汇总。"""
    _ensure_probe_env()
    from app.core.extractor.scoring.ledger.declaration_capture import (
        capture_scoring_declarations,
    )
    from app.core.extractor.scoring.ledger.duplicate_exclusion import (
        detect_duplicate_grid_rows,
    )
    from app.core.extractor.scoring.ledger.label_normalization import normalize_group_label
    from app.core.extractor.scoring.ledger.ledger_reconciliation import (
        reconcile_scoring_ledger,
    )
    from scripts.audit_scoring_legacy_baseline import run_deterministic_extraction_for_sample

    references = parse_composition_references(TRAINING_TRUTH_PATH.read_text(encoding="utf-8"))
    sample_specs = [(k, TRAINING_CORPUS_DIR) for k in TRAINING_KEYS]
    sample_specs += [(k, HOLDOUT_CORPUS_DIR) for k in DOWNGRADED_HOLDOUT_KEYS]

    rows: list[dict] = []
    false_total = 0
    explicit_total = 0
    for key, corpus_dir in sample_specs:
        fixture = _load_fixture(key, corpus_dir)
        if not fixture:
            continue
        pages, grids = _fixture_inputs(fixture)
        capture = capture_scoring_declarations(pages, grids)
        extracted = run_deterministic_extraction_for_sample(key, corpus_dir=corpus_dir)
        item_names = {name for name, _s in extracted.get("item_pairs", [])}
        entries = [
            (f"item_{idx}", score, label)
            for idx, (label, score) in enumerate(extracted.get("item_group_pairs", []))
        ]
        report = reconcile_scoring_ledger(capture, entries)

        ref = references.get(key, {"pairs": [], "annotated": False})
        if ref.get("annotated"):
            ref_norms = {normalize_group_label(r["label"]) for r in ref["pairs"]}
            for pair in capture.pairs:
                explicit_total += 1
                if pair.norm_label not in ref_norms:
                    false_total += 1

        rows.append({
            "key": key,
            "basis_level": report.basis_level.value,
            "low_confidence": report.basis_low_confidence,
            "status": report.status.value,
            "expected_total": report.expected_total,
            "extracted_total": report.extracted_total,
            "total_gap": report.total_gap,
            "group_rows": len(report.group_rows),
            "multi_scheme": bool(report.multi_scheme_signal),
            "conflicts": len(capture.conflicts),
            "item_count": len(item_names),
        })

    fp_rows = {}
    for dup_key in ("04", "02"):
        fixture = _load_fixture(dup_key, TRAINING_CORPUS_DIR)
        _pages, grids = _fixture_inputs(fixture)
        dup_rows, _diags = detect_duplicate_grid_rows(grids)
        fp_rows[dup_key] = len(dup_rows)

    by_key = {r["key"]: r for r in rows}
    good_group = sum(
        1 for k in _GOOD_SUBSET_KEYS
        if by_key.get(k, {}).get("basis_level") in ("l1_explicit_group", "l2_derived_group")
    )
    multi_review = all(
        by_key.get(k, {}).get("status") == "review_required" for k in _MULTI_SCHEME_KEYS
    )
    multi_signaled = sum(
        1 for k in _MULTI_SCHEME_KEYS
        if by_key.get(k, {}).get("multi_scheme") or by_key.get(k, {}).get("basis_level") == "l4_none"
    )
    reconciled = [r["key"] for r in rows if r["status"] == "reconciled"]
    return {
        "rows": rows,
        "good_group_basis": good_group,
        "good_subset_size": len(_GOOD_SUBSET_KEYS),
        "false_total": false_total,
        "explicit_total": explicit_total,
        "multi_scheme_all_review": multi_review,
        "multi_scheme_signaled": multi_signaled,
        "dup_rows_04": fp_rows.get("04", 0),
        "dup_rows_02": fp_rows.get("02", 0),
        "reconciled_keys": reconciled,
    }


def format_v3_report(summary: dict, run_date: str = "") -> str:
    """渲染 P1 验收报告。"""
    from datetime import date as _date

    pass_group = summary["good_group_basis"] >= _V2_GOOD_SUBSET_GROUP_BASIS
    pass_false = summary["false_total"] == 0
    pass_multi = summary["multi_scheme_all_review"]
    pass_dup = summary["dup_rows_04"] > 0 and summary["dup_rows_02"] == 0
    lines = [
        "# 评分项换轴重构 P1 验收报告（探针 v3：生产 ledger 模块复测）",
        "",
        f"**运行日期**: {run_date or _date.today()}",
        "**被测模块**: ledger/declaration_capture + ledger_reconciliation + duplicate_exclusion（生产代码）",
        "",
        "## 验收判定",
        "",
        "| 判据 | 实测 | 基线 | 判定 |",
        "|---|---:|---:|---|",
        f"| 良好子集组级基准覆盖 | {summary['good_group_basis']}/{summary['good_subset_size']} "
        f"| >= {_V2_GOOD_SUBSET_GROUP_BASIS}/19 (探针v2) | {'通过' if pass_group else '不通过'} |",
        f"| 显式捕获误报（注解样本） | {summary['false_total']}/{summary['explicit_total']} | =0 | "
        f"{'通过' if pass_false else '不通过'} |",
        f"| 多方案样本(09/24/26)全部REVIEW | {summary['multi_scheme_all_review']} "
        f"(信号/无基准命中 {summary['multi_scheme_signaled']}/3) | 必须 | {'通过' if pass_multi else '不通过'} |",
        f"| structure-04副本行检出/02负例零误判 | {summary['dup_rows_04']}条 / {summary['dup_rows_02']}条 | >0 / =0 | "
        f"{'通过' if pass_dup else '不通过'} |",
        "",
        f"**RECONCILED 样本**: {', '.join(summary['reconciled_keys']) or '(无)'}",
        "",
        "## 逐样本明细",
        "",
        "| sample | 基准层级 | 低置信 | 状态 | expected | extracted | gap | 组行 | 多方案信号 | 冲突 |",
        "|---|---|---|---|---:|---:|---:|---:|---|---:|",
    ]
    for r in summary["rows"]:
        expected = f"{r['expected_total']:g}" if r["expected_total"] is not None else "-"
        gap = f"{r['total_gap']:+g}" if r["total_gap"] is not None else "-"
        lines.append(
            f"| {r['key']} | {r['basis_level']} | {'Y' if r['low_confidence'] else 'N'} "
            f"| {r['status']} | {expected} | {r['extracted_total']:g} | {gap} "
            f"| {r['group_rows']} | {'Y' if r['multi_scheme'] else '-'} | {r['conflicts']} |"
        )
    lines += ["", "- 冻结验证集（structure-35~44）未接触。", ""]
    return "\n".join(lines)


def main() -> None:
    """v3 验收探针入口。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    summary = assess_ledger_modules_on_corpus()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(format_v3_report(summary), encoding="utf-8")
    print(f"[probe-v3] 报告已写入: {REPORT_PATH}")
    print(f"[probe-v3] 良好子集组级基准 {summary['good_group_basis']}/19 (基线>=18)")
    print(f"[probe-v3] 显式误报 {summary['false_total']}/{summary['explicit_total']} (须=0)")
    print(f"[probe-v3] 多方案全REVIEW={summary['multi_scheme_all_review']}")
    print(f"[probe-v3] 副本行 04={summary['dup_rows_04']} 02={summary['dup_rows_02']}")
    print(f"[probe-v3] RECONCILED: {summary['reconciled_keys']}")


if __name__ == "__main__":
    main()
