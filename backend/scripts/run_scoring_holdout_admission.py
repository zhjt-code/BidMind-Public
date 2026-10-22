"""P4 评分项提取 holdout 一次性准入脚本。

职责: 读取 sealed holdout 真值与确定性 legacy 输出，生成仅含聚合指标的准入报告。
不做: 不输出 holdout 项名/规则/版式/页码/分值明细；不调用 LLM；不访问 DB；不修改训练 EXPECTATIONS。
"""
from __future__ import annotations

import sys
import contextlib
import io
import re
import argparse
from datetime import date
from pathlib import Path
from typing import Any

# Ensure backend/ is importable when this script is executed directly.
_BACKEND_DIR = Path(__file__).parent.parent
_PROJECT_ROOT = _BACKEND_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from scripts import audit_scoring_legacy_baseline as legacy  # noqa: E402

HOLDOUT_CORPUS_DIR = _BACKEND_DIR / "tests" / "fixtures" / "scoring" / "sample_corpus_holdout"
TRUTH_PATH = _BACKEND_DIR / "storage" / "samples" / "scoring_corpus" / "holdout_truth_sealed.md"
REPORT_PATH = _PROJECT_ROOT / "docs" / "reports" / "scoring_holdout_admission_p4.md"
SCORE_EPSILON = 1e-6


def classify_sample_safety(
    *,
    item_errors: int,
    score_gap: float,
    validation_flags: int,
    runtime_error: int,
    extracted_count: int,
    truth_count: int,
) -> str:
    """按三态安全准入口径分类单样本。

    职责: 将聚合误差转换为 VERIFIED/PROVISIONAL/FAILED 安全状态。
    不做: 不读取 holdout 明细、不执行提取、不决定 P5 切流范围。
    """
    if runtime_error:
        return "FAILED"
    if truth_count > 0 and extracted_count == 0:
        return "FAILED"
    if item_errors == 0 and abs(score_gap) <= SCORE_EPSILON and validation_flags == 0:
        return "VERIFIED"
    return "PROVISIONAL"


def summarize_gate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总三态 holdout 准入结果（换轴P4: 附账本维度闸门）。"""
    failed_ids = [row["key"] for row in results if row["status"] == "FAILED"]
    verified_count = sum(1 for row in results if row["status"] == "VERIFIED")
    provisional_count = sum(1 for row in results if row["status"] == "PROVISIONAL")
    valid_metric_rows = [row for row in results if not row["runtime_error"]]
    # 账本闸门: ①假通过=0(真值缺口样本不得 RECONCILED); ②缺口可见(真值缺口样本必须 REVIEW);
    # ③账本REVIEW率不得高于legacy非VERIFIED率(不加重人工负担)。
    gap_rows = [
        row for row in valid_metric_rows
        if row.get("score_gap") is not None and abs(row["score_gap"]) > 1.0
    ]
    ledger_false_pass_ids = [
        row["key"] for row in gap_rows if row.get("ledger_status") == "reconciled"
    ]
    ledger_gap_invisible_ids = [
        row["key"] for row in gap_rows if row.get("ledger_status") != "review_required"
    ]
    ledger_review_count = sum(
        1 for row in valid_metric_rows if row.get("ledger_status") == "review_required"
    )
    # 负担恶化只统计"高置信基准下的REVIEW"——账本有能力判定却判REVIEW才是真缺口/
    # 冲突(正当)或误报(需查); 低置信基准(L2推导/默认100/歧义)的REVIEW是"证据不足以
    # 自证"的诚实保守, 单列观察不计入恶化(圆桌固化条件①: 低置信恒不作通过依据)。
    ledger_confident_review_count = sum(
        1 for row in valid_metric_rows
        if row.get("ledger_status") == "review_required"
        and not row.get("ledger_low_confidence", True)
    )
    legacy_flagged_count = sum(1 for row in results if row["status"] != "VERIFIED")
    ledger_gate = {
        "ledger_false_pass_ids": ledger_false_pass_ids,
        "ledger_gap_invisible_ids": ledger_gap_invisible_ids,
        "ledger_review_count": ledger_review_count,
        "ledger_confident_review_count": ledger_confident_review_count,
        "legacy_flagged_count": legacy_flagged_count,
        "ledger_gate_passed": (
            not ledger_false_pass_ids
            and not ledger_gap_invisible_ids
            and ledger_confident_review_count <= legacy_flagged_count
        ),
    }
    return {
        **ledger_gate,
        "overall": "PASS" if not failed_ids and results else "FAIL",
        "sample_count": len(results),
        "verified_count": verified_count,
        "provisional_count": provisional_count,
        "passed_count": verified_count,
        "failed_ids": failed_ids,
        "total_item_errors": sum(row["item_errors"] or 0 for row in results),
        "total_validation_flags": sum(row["validation_flags"] for row in results),
        "runtime_error_count": sum(row["runtime_error"] for row in results),
        "silent_empty_count": sum(
            1 for row in results if row.get("truth_count", 0) > 0 and row.get("extracted_count", 0) == 0
        ),
        "max_abs_score_gap": max((abs(row["score_gap"] or 0.0) for row in results), default=0.0),
        "avg_precision": (
            sum(row["precision"] for row in valid_metric_rows) / len(valid_metric_rows)
            if valid_metric_rows
            else 0.0
        ),
        "avg_recall": (
            sum(row["recall"] for row in valid_metric_rows) / len(valid_metric_rows)
            if valid_metric_rows
            else 0.0
        ),
        "results": results,
    }


def _sort_sample_key(key: str) -> tuple[int, str]:
    """Sort numeric sample IDs naturally while keeping a deterministic fallback."""
    return (int(key), key) if key.isdigit() else (10**9, key)


def discover_holdout_keys(corpus_dir: Path = HOLDOUT_CORPUS_DIR) -> list[str]:
    """Return sorted holdout sample IDs from fixture filenames."""
    return sorted((path.stem for path in corpus_dir.glob("*.json")), key=_sort_sample_key)


def load_holdout_truth(truth_path: Path = TRUTH_PATH) -> dict[str, dict[str, Any]]:
    """Parse the sealed holdout truth markdown without exposing item-level content."""
    content = truth_path.read_text(encoding="utf-8")
    parsed = legacy.parse_truth_markdown(content)
    return parsed or parse_sealed_truth_items(content)


# 汇总层事实: 其分值是对同一批叶子评分项的再表达(加权折算后的最终得分、组小计),
# 与叶子不在同一轴, 相加即重复计分。
_AGGREGATE_FACT_TYPES = frozenset({"final_score_formula", "score_group"})


def _parse_yamlish_scalar(value: str) -> str:
    """Parse a single-line scalar from the sealed truth YAML-like list."""
    value = value.strip()
    if value in {"null", "None", "~"}:
        return ""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_sealed_truth_items(content: str) -> dict[str, dict[str, Any]]:
    """Parse sealed truth list items into the same aggregate shape as legacy truth.: 真值条目分属两条轴——叶子评分项，以及"最终评标得分公式""组小计"这类
 **对同一批分值的再表达**。旧实现无视 `fact_type` 一律相加，structure-24 的基线
 因此被算成 400（三卷各100 + 汇总公式100），该错误基线又反过来把"多方案"误报
 验收为正确行为。此处叶子入 `declared_total`、汇总层单列 `aggregate_total`，
 两者都不丢弃、但绝不同轴相加。
 """
    result: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None

    def _flush() -> None:
        if not current:
            return
        sample_id = str(current.get("sample_id") or "")
        match = re.search(r"structure-(\d+)", sample_id) or re.search(r"(\d+)", sample_id)
        item_name = current.get("item_name")
        max_score = current.get("max_score")
        if not match or not item_name or max_score is None:
            return
        key = match.group(1).zfill(2)
        entry = result.setdefault(
            key, {"declared_total": 0.0, "aggregate_total": 0.0, "items": []}
        )
        fact_type = str(current.get("fact_type") or "")
        entry["items"].append(
            {"item_name": item_name, "max_score": max_score, "fact_type": fact_type}
        )
        axis = "aggregate_total" if fact_type in _AGGREGATE_FACT_TYPES else "declared_total"
        entry[axis] = round(float(entry[axis]) + float(max_score), 4)

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```"):
            continue
        if line.startswith("- sample_id:"):
            _flush()
            current = {"sample_id": _parse_yamlish_scalar(line.split(":", 1)[1])}
            continue
        if current is None or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        if key == "item_name":
            current["item_name"] = _parse_yamlish_scalar(raw_value)
        elif key == "max_score":
            try:
                current["max_score"] = float(_parse_yamlish_scalar(raw_value))
            except ValueError:
                current["max_score"] = None
        elif key == "fact_type":
            current["fact_type"] = _parse_yamlish_scalar(raw_value)

    _flush()
    return result


def _evaluate_ledger_for_sample(
    key: str, corpus_dir: Path, extracted: dict[str, Any]
) -> dict[str, Any]:
    """换轴P4: 对单样本运行生产账本模块, 返回三态/基准层级/多方案信号。

    职责: 组装 fixture 页与网格 + 提取组标签对, 调用生产 capture+reconcile。
    不做: 不改变 legacy 判定、不读取真值明细。
    """
    import json

    from app.core.extractor.scoring.ledger.declaration_capture import (
        capture_scoring_declarations,
    )
    from app.core.extractor.scoring.ledger.ledger_reconciliation import (
        reconcile_scoring_ledger,
    )

    fixture_path = corpus_dir / f"{key}.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    pages = [(p["page_num"], p.get("text") or "") for p in fixture.get("pages", [])]
    grids = []
    for page in fixture.get("pages", []):
        for table in page.get("tables", []):
            data = table.get("data") if isinstance(table, dict) else table
            grids.append(
                (page["page_num"], [[str(c or "") for c in row] for row in (data or [])])
            )
    capture = capture_scoring_declarations(pages, grids)
    entries = [
        (f"item_{idx}", score, label)
        for idx, (label, score) in enumerate(extracted.get("item_group_pairs", []))
    ]
    report = reconcile_scoring_ledger(capture, entries)
    return {
        "ledger_status": report.status.value,
        "ledger_basis": report.basis_level.value,
        "ledger_low_confidence": report.basis_low_confidence,
        "ledger_gap": report.total_gap,
        "ledger_multi_scheme": bool(report.multi_scheme_signal),
        "ledger_group_rows": len(report.group_rows),
    }


def evaluate_holdout_sample(
    key: str,
    truth_data: dict[str, dict[str, Any]],
    corpus_dir: Path = HOLDOUT_CORPUS_DIR,
) -> dict[str, Any]:
    """Evaluate one holdout sample using the deterministic legacy extraction chain."""
    truth_entry = truth_data.get(key)
    if not truth_entry:
        return {
            "key": key,
            "status": "FAILED",
            "item_errors": None,
            "score_gap": None,
            "validation_flags": 1,
            "runtime_error": 1,
            "truth_count": 0,
            "extracted_count": 0,
            "precision": 0.0,
            "recall": 0.0,
        }

    truth_items = truth_entry.get("items", [])
    truth_names = [item["item_name"] for item in truth_items]
    declared_total = float(truth_entry.get("declared_total", 100.0))

    try:
        original_corpus_dir = legacy.CORPUS_DIR
        legacy.CORPUS_DIR = corpus_dir
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            extracted = legacy.run_deterministic_extraction_for_sample(key)
    except Exception:  # noqa: BLE001 - report only aggregate failure, never fixture details.
        return {
            "key": key,
            "status": "FAILED",
            "item_errors": None,
            "score_gap": None,
            "validation_flags": 1,
            "runtime_error": 1,
            "truth_count": len(truth_names),
            "extracted_count": 0,
            "precision": 0.0,
            "recall": 0.0,
        }
    finally:
        legacy.CORPUS_DIR = original_corpus_dir

    # 换轴重构 P0 起主口径为（归一化名称, 分值±0.01）二元组匹配（补偿性错误不可穿透）。
    metrics = legacy.compute_item_pair_metrics(truth_items, extracted.get("item_pairs", []))
    true_positive = int(metrics["true_positive"])
    truth_count = int(metrics["truth_count"])
    extracted_count = int(metrics["extracted_count"])
    item_errors = (truth_count - true_positive) + (extracted_count - true_positive)
    score_gap = round(float(extracted.get("total", 0.0)) - declared_total, 4)
    validation_flags = 1 if extracted.get("needs_review") else 0
    safety_status = classify_sample_safety(
        item_errors=item_errors,
        score_gap=score_gap,
        validation_flags=validation_flags,
        runtime_error=0,
        extracted_count=extracted_count,
        truth_count=truth_count,
    )

    try:
        ledger_fields = _evaluate_ledger_for_sample(key, corpus_dir, extracted)
    except Exception:  # noqa: BLE001 账本评估失败按最保守口径记录, 不掩盖
        ledger_fields = {
            "ledger_status": "evaluation_error",
            "ledger_basis": "-",
            "ledger_low_confidence": True,
            "ledger_gap": None,
            "ledger_multi_scheme": False,
            "ledger_group_rows": 0,
        }

    return {
        "key": key,
        "status": safety_status,
        "item_errors": item_errors,
        "score_gap": score_gap,
        "validation_flags": validation_flags,
        "runtime_error": 0,
        "truth_count": truth_count,
        "extracted_count": extracted_count,
        "precision": float(metrics["precision"]),
        "recall": float(metrics["recall"]),
        **ledger_fields,
    }


def evaluate_holdout_gate(keys: list[str] | None = None) -> dict[str, Any]:
    """Evaluate all holdout samples and return aggregate gate results."""
    sample_keys = keys or discover_holdout_keys()
    truth_data = load_holdout_truth()
    results = [evaluate_holdout_sample(key, truth_data) for key in sample_keys]
    return summarize_gate(results)


def format_holdout_report(gate: dict[str, Any], run_date: str | None = None) -> str:
    """Format the holdout gate report without item-level holdout details."""
    date_str = run_date or str(date.today())
    failed_ids = ", ".join(gate["failed_ids"]) if gate["failed_ids"] else "-"

    lines = [
        "# 评分项提取 Holdout 一次性准入报告（P4）",
        "",
        f"**评估日期**: {date_str}",
        "**准入口径**: 新内核未切流，当前评估为legacy baseline holdout gate。",
        "**保密约束**: 本报告不包含 holdout 项名、规则、版式细节、页码或分值明细。",
        "",
        "## Overall",
        "",
        f"**OVERALL**: {gate['overall']}",
        f"**失败样本编号**: {failed_ids}",
        "",
        "## 聚合指标",
        "",
        "| 指标 | 值 |",
        "|---|---:|",
        f"| 样本数量 | {gate['sample_count']} |",
        f"| VERIFIED 数量 | {gate['verified_count']} |",
        f"| PROVISIONAL 数量 | {gate['provisional_count']} |",
        f"| FAILED 数量 | {len(gate['failed_ids'])} |",
        f"| item error 总数 | {gate['total_item_errors']} |",
        f"| validation flag 总数 | {gate['total_validation_flags']} |",
        f"| runtime error 数量 | {gate['runtime_error_count']} |",
        f"| silent-empty 数量 | {gate['silent_empty_count']} |",
        f"| max abs score gap | {gate['max_abs_score_gap']:.4f} |",
        f"| avg item precision | {gate['avg_precision']:.4f} |",
        f"| avg item recall | {gate['avg_recall']:.4f} |",
        "",
        "## 账本维度闸门（换轴P4）",
        "",
        f"**LEDGER_GATE**: {'PASS' if gate.get('ledger_gate_passed') else 'FAIL'}",
        f"- 账本假通过样本(真值缺口却RECONCILED): {', '.join(gate.get('ledger_false_pass_ids') or []) or '-'}",
        f"- 账本缺口不可见样本(真值缺口未落REVIEW): {', '.join(gate.get('ledger_gap_invisible_ids') or []) or '-'}",
        f"- 账本REVIEW数(总/高置信) vs legacy非VERIFIED数: {gate.get('ledger_review_count')}"
        f"({gate.get('ledger_confident_review_count')}) / {gate.get('legacy_flagged_count')}",
        "- 负担恶化判据只计高置信REVIEW; 低置信(L2推导/默认100/基准歧义)REVIEW为诚实保守, 单列观察。",
        "",
        "## 逐样本三态",
        "",
        "| sample_id | status | item_errors | score_gap | validation_flags | runtime_error | ledger_status | ledger_basis | 多方案 |",
        "|---|---|---:|---:|---:|---:|---|---|---|",
    ]

    for row in gate["results"]:
        item_errors = "NA" if row["item_errors"] is None else str(row["item_errors"])
        score_gap = "NA" if row["score_gap"] is None else f"{row['score_gap']:+.4f}"
        lines.append(
            f"| {row['key']} | {row['status']} | {item_errors} | {score_gap} "
            f"| {row['validation_flags']} | {row['runtime_error']} "
            f"| {row.get('ledger_status', '-')} | {row.get('ledger_basis', '-')} "
            f"| {'Y' if row.get('ledger_multi_scheme') else '-'} |"
        )

    lines += [
        "",
        "## 判定规则",
        "",
        "- VERIFIED: item_errors=0、score_gap=0、validation_flags=0、runtime_error=0。",
        "- PROVISIONAL: 有候选输出但存在缺口/冲突/复核标记，可准入但不得自动采纳。",
        "- FAILED: runtime_error>0 或 truth_count>0 但 extracted_count=0（silent-empty）。",
        "- 整体 PASS: 无 FAILED/silent-empty；P5 仅允许 VERIFIED in-scope 自动切流。",
        "- item_errors 与 P/R 基于（归一化名称, 分值±0.01）二元组多重集匹配口径（换轴重构 P0）。",
        "",
    ]
    return "\n".join(lines)


def write_holdout_report(
    report_path: Path = REPORT_PATH,
    *,
    corpus_dir: Path = HOLDOUT_CORPUS_DIR,
    truth_path: Path = TRUTH_PATH,
) -> dict[str, Any]:
    """Evaluate the holdout gate and write the markdown report."""
    sample_keys = discover_holdout_keys(corpus_dir)
    truth_data = load_holdout_truth(truth_path)
    results = [evaluate_holdout_sample(key, truth_data, corpus_dir=corpus_dir) for key in sample_keys]
    failed_ids = [row["key"] for row in results if row["status"] != "PASS"]
    valid_metric_rows = [row for row in results if not row["runtime_error"]]
    gate = summarize_gate(results)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(format_holdout_report(gate), encoding="utf-8")
    return gate


def main() -> None:
    """CLI entry point that writes the holdout admission report."""
    parser = argparse.ArgumentParser(description="Run scoring holdout admission gate.")
    parser.add_argument("--corpus-dir", default=str(HOLDOUT_CORPUS_DIR))
    parser.add_argument("--truth-path", default=str(TRUTH_PATH))
    parser.add_argument("--report-path", default=str(REPORT_PATH))
    args = parser.parse_args()

    report_path = Path(args.report_path)
    gate = write_holdout_report(
        report_path,
        corpus_dir=Path(args.corpus_dir),
        truth_path=Path(args.truth_path),
    )
    failed_ids = ",".join(gate["failed_ids"]) if gate["failed_ids"] else "-"
    print("STATUS=OK")
    print(f"REPORT_PATH={report_path}")
    print(f"OVERALL={gate['overall']}")
    print(f"SAMPLE_COUNT={gate['sample_count']}")
    print(f"VERIFIED_COUNT={gate['verified_count']}")
    print(f"PROVISIONAL_COUNT={gate['provisional_count']}")
    print(f"FAILED_SAMPLE_IDS={failed_ids}")


if __name__ == "__main__":
    main()
