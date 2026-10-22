"""评分提取失败归因审计（换轴重构 P0 后续：0.637 基线的成分拆解）。

职责: 对训练语料逐样本把"真值项 vs 提取项"做精确配对后的残差交叉归因，
      区分名称口径差异/分值错误/真实漏提/真实多提，并给出"名称口径校准后"
      的 P/R/F1 对照，回答"pair F1=0.637 里有多少是量尺过严造成的假象"。
不做: 不修改生产代码与量尺、不调用 LLM/DB、
      不读取冻结验证集(sample_corpus_holdout_round2 与 holdout_round2_truth_sealed.md)。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).parent.parent
_PROJECT_ROOT = _BACKEND_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from scripts.audit_scoring_legacy_baseline import (  # noqa: E402
    CORPUS_DIR,
    DOWNGRADED_HOLDOUT_CORPUS_DIR,
    DOWNGRADED_HOLDOUT_KEYS,
    SCORE_MATCH_TOLERANCE,
    TRAINING_KEYS,
    TRUTH_PATH,
    load_downgraded_holdout_truth,
    normalize_name_for_match,
    parse_truth_markdown,
    run_deterministic_extraction_for_sample,
)

REPORT_PATH = _PROJECT_ROOT / "docs" / "reports" / "scoring_failure_attribution.md"

# 名称相似度阈值: >=0.4 视为"同一评分对象的不同写法"候选（保守取值，
# 低于 scoring_reconcile 的 0.5 合并阈值仅用于归因分析，不用于任何合并动作）。
_NAME_SIMILARITY_THRESHOLD = 0.4

# 归因类别（按判定优先级排列）
CATEGORY_SCORE_MISMATCH = "score_mismatch"
CATEGORY_NAME_VARIANT = "name_variant"
CATEGORY_SCORE_ALIGNED_UNRELATED = "score_aligned_name_unrelated"
CATEGORY_NAME_SIMILAR_SCORE_DIFF = "name_similar_score_diff"
CATEGORY_TRUTH_MISSING = "truth_missing"
CATEGORY_EXTRACTED_EXTRA = "extracted_extra"

_CATEGORY_LABELS = {
    CATEGORY_SCORE_MISMATCH: "名称一致分值不符（真实分值错误）",
    CATEGORY_NAME_VARIANT: "分值一致名称写法差异（量尺假象候选）",
    CATEGORY_SCORE_ALIGNED_UNRELATED: "分值一致但名称无关（存疑，需人工判读）",
    CATEGORY_NAME_SIMILAR_SCORE_DIFF: "名称相近分值不符（粒度或分值错误）",
    CATEGORY_TRUTH_MISSING: "真值项无任何对应（真实漏提）",
    CATEGORY_EXTRACTED_EXTRA: "提取项无任何对应（真实多提/伪项）",
}


# ── 纯函数区 ─────────────────────────────────────────────────────────────────


def _bigrams(text: str) -> set[str]:
    if len(text) < 2:
        return {text} if text else set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


def name_similarity(left: str, right: str) -> float:
    """名称 bigram Jaccard 相似度（归一化后计算）。"""
    a, b = normalize_name_for_match(left), normalize_name_for_match(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ba, bb = _bigrams(a), _bigrams(b)
    union = len(ba | bb)
    return (len(ba & bb) / union) if union else 0.0


def is_name_related(left: str, right: str) -> bool:
    """名称是否可视为同一对象的不同写法：包含关系或相似度达阈值。"""
    a, b = normalize_name_for_match(left), normalize_name_for_match(right)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return name_similarity(left, right) >= _NAME_SIMILARITY_THRESHOLD


def match_exact_pairs(
    truth_items: list[tuple[str, float]],
    extracted_items: list[tuple[str, float]],
) -> tuple[list[tuple[str, float]], list[tuple[str, float]], int]:
    """按（归一名称, 分值±容差）多重集精确配对，返回 (剩余真值, 剩余提取, TP)。"""
    remaining_extracted = list(extracted_items)
    unmatched_truth: list[tuple[str, float]] = []
    true_positive = 0
    for name, score in truth_items:
        norm = normalize_name_for_match(name)
        hit_index = next(
            (
                idx
                for idx, (cand_name, cand_score) in enumerate(remaining_extracted)
                if normalize_name_for_match(cand_name) == norm
                and abs(cand_score - score) <= SCORE_MATCH_TOLERANCE
            ),
            None,
        )
        if hit_index is None:
            unmatched_truth.append((name, score))
        else:
            remaining_extracted.pop(hit_index)
            true_positive += 1
    return unmatched_truth, remaining_extracted, true_positive


def attribute_residuals(
    unmatched_truth: list[tuple[str, float]],
    unmatched_extracted: list[tuple[str, float]],
) -> list[dict]:
    """对精确配对后的残差做交叉归因，返回逐条归因记录。

    判定优先级: 名称相等分值不符 > 分值相等名称相近 > 分值相等名称无关 >
    名称相近分值不符 > 孤立真值项/孤立提取项。
    """
    truth_pool = list(unmatched_truth)
    extracted_pool = list(unmatched_extracted)
    findings: list[dict] = []

    def _consume(
        predicate, category: str, *, prefer_similarity: bool
    ) -> None:
        for truth_name, truth_score in list(truth_pool):
            candidates = [
                (idx, item)
                for idx, item in enumerate(extracted_pool)
                if predicate(truth_name, truth_score, item[0], item[1])
            ]
            if not candidates:
                continue
            if prefer_similarity:
                candidates.sort(key=lambda c: name_similarity(truth_name, c[1][0]), reverse=True)
            index, (extracted_name, extracted_score) = candidates[0]
            extracted_pool.pop(index)
            truth_pool.remove((truth_name, truth_score))
            findings.append({
                "category": category,
                "truth_name": truth_name,
                "truth_score": truth_score,
                "extracted_name": extracted_name,
                "extracted_score": extracted_score,
                "similarity": round(name_similarity(truth_name, extracted_name), 3),
            })

    _consume(
        lambda tn, _ts, en, _es: normalize_name_for_match(tn) == normalize_name_for_match(en),
        CATEGORY_SCORE_MISMATCH,
        prefer_similarity=False,
    )
    _consume(
        lambda tn, ts, en, es: abs(ts - es) <= SCORE_MATCH_TOLERANCE and is_name_related(tn, en),
        CATEGORY_NAME_VARIANT,
        prefer_similarity=True,
    )
    _consume(
        lambda _tn, ts, _en, es: abs(ts - es) <= SCORE_MATCH_TOLERANCE,
        CATEGORY_SCORE_ALIGNED_UNRELATED,
        prefer_similarity=True,
    )
    _consume(
        lambda tn, _ts, en, _es: is_name_related(tn, en),
        CATEGORY_NAME_SIMILAR_SCORE_DIFF,
        prefer_similarity=True,
    )

    findings.extend(
        {
            "category": CATEGORY_TRUTH_MISSING,
            "truth_name": name,
            "truth_score": score,
            "extracted_name": "",
            "extracted_score": None,
            "similarity": 0.0,
        }
        for name, score in truth_pool
    )
    findings.extend(
        {
            "category": CATEGORY_EXTRACTED_EXTRA,
            "truth_name": "",
            "truth_score": None,
            "extracted_name": name,
            "extracted_score": score,
            "similarity": 0.0,
        }
        for name, score in extracted_pool
    )
    return findings


def compute_calibrated_metrics(
    true_positive: int, name_variant: int, truth_count: int, extracted_count: int
) -> dict:
    """把"名称写法差异"计入命中后的校准 P/R/F1（量尺假象的量化上界）。"""
    hits = true_positive + name_variant
    precision = hits / extracted_count if extracted_count else 0.0
    recall = hits / truth_count if truth_count else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


# ── 主流程与报告 ─────────────────────────────────────────────────────────────


def attribute_scoring_failures() -> dict:
    """对训练语料逐样本执行归因，返回汇总结构。"""
    truth_data = parse_truth_markdown(TRUTH_PATH.read_text(encoding="utf-8"))
    downgraded_truth = load_downgraded_holdout_truth()
    specs = [(key, CORPUS_DIR, truth_data.get(key, {})) for key in TRAINING_KEYS]
    specs += [
        (key, DOWNGRADED_HOLDOUT_CORPUS_DIR, downgraded_truth.get(key, {}))
        for key in DOWNGRADED_HOLDOUT_KEYS
    ]

    rows: list[dict] = []
    for key, corpus_dir, truth_entry in specs:
        truth_items = [
            (str(item["item_name"]), float(item["max_score"]))
            for item in truth_entry.get("items", [])
        ]
        extracted = run_deterministic_extraction_for_sample(key, corpus_dir=corpus_dir)
        extracted_items = [
            (str(name), float(score)) for name, score in extracted.get("item_pairs", [])
        ]

        unmatched_truth, unmatched_extracted, true_positive = match_exact_pairs(
            truth_items, extracted_items
        )
        findings = attribute_residuals(unmatched_truth, unmatched_extracted)
        counts = {category: 0 for category in _CATEGORY_LABELS}
        for finding in findings:
            counts[finding["category"]] += 1

        strict = compute_calibrated_metrics(
            true_positive, 0, len(truth_items), len(extracted_items)
        )
        calibrated = compute_calibrated_metrics(
            true_positive, counts[CATEGORY_NAME_VARIANT], len(truth_items), len(extracted_items)
        )
        rows.append({
            "key": key,
            "truth_count": len(truth_items),
            "extracted_count": len(extracted_items),
            "true_positive": true_positive,
            "counts": counts,
            "strict": strict,
            "calibrated": calibrated,
            "findings": findings,
        })
    return {"rows": rows}


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def format_attribution_report(summary: dict, run_date: str = "") -> str:
    """渲染失败归因报告。"""
    from datetime import date as _date

    rows = summary["rows"]
    totals = {category: sum(row["counts"][category] for row in rows) for category in _CATEGORY_LABELS}
    residual_total = sum(totals.values())
    strict_f1 = _mean([row["strict"]["f1"] for row in rows])
    calibrated_f1 = _mean([row["calibrated"]["f1"] for row in rows])
    strict_recall = _mean([row["strict"]["recall"] for row in rows])
    calibrated_recall = _mean([row["calibrated"]["recall"] for row in rows])

    lines = [
        "# 评分提取失败归因报告（0.637 基线成分拆解）",
        "",
        f"**审计日期**: {run_date or _date.today()}",
        f"**样本范围**: 训练集 33 份（01~21, 27, 28 + 降级首轮 holdout 23~26, 29~34）",
        "**方法**: 先按（归一名称, 分值±0.01）精确配对，残差按判定优先级交叉归因。",
        "**说明**: 归因仅用于分析，不触发任何合并动作；冻结验证集未接触。",
        "",
        "## 核心结论：严格口径 vs 名称校准口径",
        "",
        "| 指标 | 严格口径（现行量尺） | 名称校准口径 | 差值 |",
        "|---|---:|---:|---:|",
        f"| item F1（均值） | {strict_f1:.4f} | {calibrated_f1:.4f} | {calibrated_f1 - strict_f1:+.4f} |",
        f"| item recall（均值） | {strict_recall:.4f} | {calibrated_recall:.4f} | {calibrated_recall - strict_recall:+.4f} |",
        "",
        "名称校准口径 = 把\"分值一致且名称可判为同一对象不同写法\"的条目计为命中，",
        "即量尺过严造成的假象上界；两者差值即\"0.637 中属于量尺口径的部分\"。",
        "",
        "## 残差归因分布",
        "",
        "| 归因类别 | 条数 | 占残差比 |",
        "|---|---:|---:|",
    ]
    for category, label in _CATEGORY_LABELS.items():
        count = totals[category]
        share = (count / residual_total) if residual_total else 0.0
        lines.append(f"| {label} | {count} | {share:.1%} |")

    lines += [
        "",
        "## 逐样本对照",
        "",
        "| sample | 真值 | 提取 | 严格TP | 写法差异 | 分值错 | 名近分异 | 分同名异 | 漏提 | 多提 | 严格F1 | 校准F1 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        counts = row["counts"]
        lines.append(
            f"| {row['key']} | {row['truth_count']} | {row['extracted_count']} | {row['true_positive']} "
            f"| {counts[CATEGORY_NAME_VARIANT]} | {counts[CATEGORY_SCORE_MISMATCH]} "
            f"| {counts[CATEGORY_NAME_SIMILAR_SCORE_DIFF]} | {counts[CATEGORY_SCORE_ALIGNED_UNRELATED]} "
            f"| {counts[CATEGORY_TRUTH_MISSING]} | {counts[CATEGORY_EXTRACTED_EXTRA]} "
            f"| {row['strict']['f1']:.3f} | {row['calibrated']['f1']:.3f} |"
        )

    focus_rows = [row for row in rows if row["true_positive"] == 0 and row["truth_count"] > 0]
    lines += [
        "",
        "## 重点案例逐条对照（严格 TP=0 样本）",
        "",
    ]
    if not focus_rows:
        lines.append("（无严格 TP=0 样本）")
    for row in focus_rows:
        lines += [
            f"### 样本 {row['key']}（真值 {row['truth_count']} 项 / 提取 {row['extracted_count']} 项）",
            "",
            "| 归因 | 真值项 | 真值分 | 提取项 | 提取分 | 名称相似度 |",
            "|---|---|---:|---|---:|---:|",
        ]
        for finding in row["findings"]:
            truth_score = "-" if finding["truth_score"] is None else f"{finding['truth_score']:g}"
            extracted_score = (
                "-" if finding["extracted_score"] is None else f"{finding['extracted_score']:g}"
            )
            lines.append(
                f"| {_CATEGORY_LABELS[finding['category']].split('（')[0]} "
                f"| {finding['truth_name'][:38] or '-'} | {truth_score} "
                f"| {finding['extracted_name'][:38] or '-'} | {extracted_score} "
                f"| {finding['similarity']:.2f} |"
            )
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    """归因审计入口。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    summary = attribute_scoring_failures()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(format_attribution_report(summary), encoding="utf-8")
    rows = summary["rows"]
    strict_f1 = _mean([row["strict"]["f1"] for row in rows])
    calibrated_f1 = _mean([row["calibrated"]["f1"] for row in rows])
    totals = {category: sum(row["counts"][category] for row in rows) for category in _CATEGORY_LABELS}
    print(f"[attribution] 报告已写入: {REPORT_PATH}")
    print(f"[attribution] 严格F1={strict_f1:.4f} -> 名称校准F1={calibrated_f1:.4f} (差 {calibrated_f1 - strict_f1:+.4f})")
    for category, label in _CATEGORY_LABELS.items():
        print(f"[attribution] {label}: {totals[category]}")


if __name__ == "__main__":
    main()
