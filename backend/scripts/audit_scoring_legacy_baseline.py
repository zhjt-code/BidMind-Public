"""评分项提取 Legacy 基线审计脚本（P1-4 / 换轴重构 P0）。

职责: 读取训练集真值文档和确定性链输出，生成训练集 item-level precision/recall、
      闭合率、逐样本摘要；换轴重构 P0 起以（归一化名称, 分值）二元组匹配为主口径，
      保留纯名称口径作对照，输出到 docs/reports/scoring_content_baseline_v2.md。
不做: 不调用 LLM、不访问 DB、不读取冻结验证集 fixtures
      （sample_corpus_holdout_round2 目录绝对禁止；首轮 holdout 23~34 已降级训练，允许读取）、
      不修改被审计代码。
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

# ── 路径常量 ─────────────────────────────────────────────────────────────────
_BACKEND_DIR = Path(__file__).parent.parent
_PROJECT_ROOT = _BACKEND_DIR.parent

CORPUS_DIR = _BACKEND_DIR / "tests" / "fixtures" / "scoring" / "sample_corpus"
TRUTH_PATH = _BACKEND_DIR / "tests" / "fixtures" / "scoring" / "sample_truth_training.md"
REPORT_PATH = _PROJECT_ROOT / "docs" / "reports" / "scoring_legacy_baseline_p1.md"
REPORT_PATH_V2 = _PROJECT_ROOT / "docs" / "reports" / "scoring_content_baseline_v2.md"

# 首轮 holdout（23~26, 29~34）已于 2026-07-27 降级为训练样本（见 implementation_plan）。
DOWNGRADED_HOLDOUT_CORPUS_DIR = (
    _BACKEND_DIR / "tests" / "fixtures" / "scoring" / "sample_corpus_holdout"
)
DOWNGRADED_HOLDOUT_TRUTH_PATH = (
    _BACKEND_DIR / "storage" / "samples" / "scoring_corpus" / "holdout_truth_sealed.md"
)
DOWNGRADED_HOLDOUT_KEYS: list[str] = ["23", "24", "25", "26", "29", "30", "31", "32", "33", "34"]

TRAINING_KEYS: list[str] = [*(f"{i:02d}" for i in range(1, 22)), "27", "28"]

SCORE_MATCH_TOLERANCE = 0.01


# ════════════════════════════════════════════════════════════════════════════
# 纯函数（无重依赖，可独立测试）
# ════════════════════════════════════════════════════════════════════════════


def parse_truth_markdown(content: str) -> dict[str, dict]:
    """解析真值 Markdown，返回 {sample_key: {declared_total, items}}。

    Args:
        content: sample_truth_training.md 全文内容。

    Returns:
        字典，key 为零填充样本编号（如 "01"、"27"），
        value 为 {"declared_total": float, "items": [{"item_name": str, "max_score": float}]}。
    """
    result: dict[str, dict] = {}

    # 按 ## structure-<digits> 切分（可能含前缀零，如 structure-01 或 structure-7）
    parts = re.split(r"\n## structure-(\d+)\n", "\n" + content)
    # parts layout: [preamble, key1, body1, key2, body2, ...]
    i = 1
    while i + 1 < len(parts):
        raw_key = parts[i]
        body = parts[i + 1]
        i += 2

        sample_key = raw_key.zfill(2)

        # 提取 declared_total；多方案样本可能用文字说明“单套各100分”，此时按
        # volume_totals 中的显式 =N 声明求和，避免把多方案语料误判为单100分。
        m = re.search(r"declared_total:\s*(\d+(?:\.\d+)?)", body)
        if m:
            declared_total = float(m.group(1))
        else:
            volume_total_match = re.search(r"volume_totals[^\n]*", body)
            volume_values = (
                [float(v) for v in re.findall(r"=\s*(\d+(?:\.\d+)?)", volume_total_match.group(0))]
                if volume_total_match
                else []
            )
            declared_total = sum(volume_values) if volume_values else 100.0

        # 解析表格
        items: list[dict] = []
        in_table = False
        for line in body.split("\n"):
            stripped = line.strip()
            if stripped.startswith("| sample_id") or stripped.startswith("|sample_id"):
                in_table = True
                continue
            if not in_table:
                continue
            if stripped.startswith("|---") or stripped.startswith("|:---"):
                continue
            if stripped.startswith("|") and stripped.endswith("|"):
                cells = [c.strip() for c in stripped.split("|")]
                # 去除首尾空字符串
                cells = [c for c in cells if c != ""]
                # 需至少: sample_id, item_name, max_score
                if len(cells) < 3:
                    continue
                item_name = cells[1]
                try:
                    max_score = float(cells[2])
                except ValueError:
                    continue
                items.append({"item_name": item_name, "max_score": max_score})
            elif in_table:
                # 非管道行表示表格结束
                in_table = False

        result[sample_key] = {"declared_total": declared_total, "items": items}

    return result


def normalize_name_for_match(name: str) -> str:
    """归一化评分项名称用于匹配：去除所有空白，剥离数字消歧后缀 (N)。

    示例:
        "施工单位企业 业绩 1" → "施工单位企业业绩1"
        "最低分为0分，没有或无效的为0分(2)" → "最低分为0分，没有或无效的为0分"
    """
    name = name.strip()
    # 剥离末尾 (N) 或 （N） 消歧后缀
    name = re.sub(r"\s*[\(（]\d+[\)）]\s*$", "", name)
    # 去除所有内部空白（统一处理全/半角空格）
    name = re.sub(r"\s+", "", name)
    return name


def compute_item_metrics(
    truth_names: list[str],
    extracted_names: list[str],
) -> dict:
    """计算 item-level precision / recall / F1（基于归一化名称集合匹配）。

    Args:
        truth_names: 真值评分项名称列表。
        extracted_names: 提取结果评分项名称列表。

    Returns:
        {"precision": float, "recall": float, "f1": float,
         "true_positive": int, "truth_count": int, "extracted_count": int}
    """
    truth_set = {normalize_name_for_match(n) for n in truth_names if n}
    extracted_set = {normalize_name_for_match(n) for n in extracted_names if n}

    tp = len(truth_set & extracted_set)
    precision = tp / len(extracted_set) if extracted_set else 0.0
    recall = tp / len(truth_set) if truth_set else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "true_positive": tp,
        "truth_count": len(truth_set),
        "extracted_count": len(extracted_set),
    }


def compute_item_pair_metrics(
    truth_items: list[dict],
    extracted_pairs: list[tuple[str, float]],
    score_tolerance: float = SCORE_MATCH_TOLERANCE,
) -> dict:
    """计算（归一化名称, 分值）二元组匹配的 item-level P/R/F1（换轴重构 P0 主口径）。

    与 compute_item_metrics（纯名称集合）不同:
    1. 匹配要求名称归一相等 **且** 分值差 <= score_tolerance；
    2. 采用多重集计数（同名多项不去重），补偿性错误（A项多提N分+B项少提N分）
       不再能穿透指标。

    Args:
        truth_items: 真值条目列表，元素含 "item_name" 与 "max_score"。
        extracted_pairs: 提取结果 (名称, 分值) 元组列表。
        score_tolerance: 分值匹配容差（默认 0.01）。

    Returns:
        {"precision", "recall", "f1", "true_positive", "truth_count", "extracted_count"}
    """
    truth_by_name: dict[str, list[float]] = defaultdict(list)
    for entry in truth_items:
        name = normalize_name_for_match(str(entry.get("item_name") or ""))
        if not name:
            continue
        truth_by_name[name].append(float(entry.get("max_score") or 0.0))

    extracted_by_name: dict[str, list[float]] = defaultdict(list)
    for raw_name, raw_score in extracted_pairs:
        name = normalize_name_for_match(str(raw_name or ""))
        if not name:
            continue
        try:
            score = float(raw_score or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        extracted_by_name[name].append(score)

    truth_count = sum(len(scores) for scores in truth_by_name.values())
    extracted_count = sum(len(scores) for scores in extracted_by_name.values())

    tp = 0
    for name, truth_scores in truth_by_name.items():
        candidates = sorted(extracted_by_name.get(name, []))
        for truth_score in sorted(truth_scores):
            for idx, candidate in enumerate(candidates):
                if abs(candidate - truth_score) <= score_tolerance:
                    tp += 1
                    candidates.pop(idx)
                    break

    precision = tp / extracted_count if extracted_count else 0.0
    recall = tp / truth_count if truth_count else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "true_positive": tp,
        "truth_count": truth_count,
        "extracted_count": extracted_count,
    }


def compute_closure_status(
    extracted_total: float,
    declared_total: float,
    tolerance: float = 1.0,
) -> dict:
    """计算单样本闭合状态。

    Args:
        extracted_total: 确定性链提取总分。
        declared_total: 真值文档声明总分。
        tolerance: 闭合容差（默认 1.0 分）。

    Returns:
        {"closed": bool, "gap": float, "extracted": float, "declared": float}
    """
    gap = round(extracted_total - declared_total, 4)
    closed = abs(gap) <= tolerance
    return {
        "closed": closed,
        "gap": gap,
        "extracted": extracted_total,
        "declared": declared_total,
    }


# ════════════════════════════════════════════════════════════════════════════
# 提取链调用（需要后端依赖，在函数内惰性导入）
# ════════════════════════════════════════════════════════════════════════════


def _ensure_backend_on_path() -> None:
    """确保 backend/ 在 sys.path 中以便 import app.*。"""
    backend_str = str(_BACKEND_DIR)
    if backend_str not in sys.path:
        sys.path.insert(0, backend_str)


def _ensure_test_env() -> None:
    """设置脚本运行所需的最小测试环境变量（与 conftest.py 对齐）。"""
    import os

    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
    os.environ.setdefault("JWT_SECRET_KEY", "audit-script-only-not-for-prod")
    os.environ.setdefault("LOG_LEVEL", "DEBUG")
    os.environ.setdefault("ADMIN_PASSWORD", "<TEST_PASSWORD>")
    os.environ.setdefault("EMAIL_VERIFICATION_ENABLED", "false")


def run_deterministic_extraction_for_sample(key: str, corpus_dir: Path | None = None) -> dict:
    """运行确定性链并返回提取结果（复用 test_scoring_sample_corpus 逻辑）。

    Args:
        key: 样本编号（fixture 文件名主干）。
        corpus_dir: 语料目录；缺省使用模块级 CORPUS_DIR（保持既有调用方兼容）。

    Returns:
        {"item_count", "total", "needs_review", "names": list[str],
         "item_pairs": list[tuple[str, float]], "llm_fallback_chunks"}
    """
    import json
    from types import SimpleNamespace

    _ensure_backend_on_path()
    _ensure_test_env()

    from app.core.extractor.scoring.scoring_extractor import ScoringExtractor, backfill_source_pages
    from app.core.extractor.scoring.scoring_facts import build_scoring_fact_set
    from app.core.extractor.scoring.scoring_scope_validator import validate_scoring_fact_set
    from app.core.extractor.scoring.scoring_table_parser import parse_scoring_table
    from app.core.extractor.scoring.volume_parser import apply_volume_definitions, split_definitions_and_items
    from app.core.extractor.scoring.scoring_grid_payload import grid_from_payload, payload_from_chunk
    from app.core.extractor.scoring.scheme_segmentation import SchemeSegmenter, stamp_items_scheme
    from app.core.extractor.scoring.grade_band_consolidator import consolidate_grade_bands_if_enabled
    from app.core.parser import ChunkPlanner
    from app.core.parser.pdf_parser import ParsedDocument, ParsedPage
    from app.core.parser.scoring_chunk_builder import build_scoring_table_chunks

    fixture_path = (corpus_dir or CORPUS_DIR) / f"{key}.json"
    if not fixture_path.exists():
        return {
            "item_count": 0,
            "total": 0.0,
            "needs_review": True,
            "names": [],
            "item_pairs": [],
            "llm_fallback_chunks": 0,
        }

    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    def _fixture_table(raw: object, index: int, page_num: int) -> SimpleNamespace:
        if isinstance(raw, dict):
            return SimpleNamespace(
                data=raw.get("data") or [],
                cell_grid=raw.get("cell_grid"),
                table_index=index,
                page_num=page_num,
            )
        return SimpleNamespace(data=raw, cell_grid=None, table_index=index, page_num=page_num)

    pages = [
        ParsedPage(
            page_num=p["page_num"],
            text=p.get("text") or "",
            tables=[_fixture_table(t, i, p["page_num"]) for i, t in enumerate(p["tables"])],
        )
        for p in fixture["pages"]
    ]
    doc = ParsedDocument(
        file_path=fixture["source"],
        filename=fixture["source"],
        page_count=fixture["page_count"],
        pages=pages,
    )

    chunks = build_scoring_table_chunks(doc, ChunkPlanner(), 0)
    survivors: list = []
    llm_fallback_chunks = 0
    volume_defs: dict = {}
    segmenter = SchemeSegmenter()

    for chunk in chunks:
        section_path = (
            getattr(chunk, "section_path", None)
            or (getattr(chunk, "metadata", None) or {}).get("section_path")
            or ""
        )
        payload = payload_from_chunk(chunk)
        parsed = parse_scoring_table(
            chunk.content,
            section_path=section_path,
            grid=grid_from_payload(payload),
            column_roles=(payload or {}).get("column_roles"),
            scope_rows=(payload or {}).get("scope_rows"),
        )
        if parsed.confident:
            for vol, vdef in parsed.volume_defs.items():
                volume_defs.setdefault(vol, vdef)
            chunk_items = list(parsed.items)
            backfill_source_pages(chunk_items, chunk)
            scheme = segmenter.assign_scheme_for_chunk(
                chunk_fingerprint=(payload or {}).get("table_fingerprint"),
                incoming_item_count=len(chunk_items),
                current_survivors=survivors,
                volume_defs=volume_defs,
            )
            stamp_items_scheme(chunk_items, scheme)
            survivors = ScoringExtractor._absorb(
                survivors, ScoringExtractor._dedupe_exact(chunk_items)
            )
        else:
            llm_fallback_chunks += 1

    llm_defs, items = split_definitions_and_items(survivors)
    for vol, vdef in llm_defs.items():
        volume_defs.setdefault(vol, vdef)
    apply_volume_definitions(items, volume_defs)

    items, _ = consolidate_grade_bands_if_enabled(items)
    fact_set = build_scoring_fact_set(items, source_id=f"corpus:{key}")
    validation = validate_scoring_fact_set(fact_set, declared_volume_defs=volume_defs)

    by_volume: dict[str, float] = defaultdict(float)
    for item in items:
        by_volume[item.volume or "unspecified"] += float(item.score_weight or 0)

    return {
        "item_count": len(items),
        "total": fact_set.total_score,
        "needs_review": validation.needs_review,
        "names": sorted(item.score_item_name for item in items),
        "item_pairs": sorted(
            (item.score_item_name, float(item.score_weight or 0.0)) for item in items
        ),
        # (原文组标签, 分值): 供账本推导层观测；标签缺失记空串。
        "item_group_pairs": sorted(
            (str(getattr(item, "volume_label", None) or ""), float(item.score_weight or 0.0))
            for item in items
        ),
        # 条目明细: (名称, 分值, category, 分类依据) —— 供 G7 类别抽检与准入 harness。
        "item_details": sorted(
            (
                item.score_item_name,
                float(item.score_weight or 0.0),
                str(item.category),
                str(getattr(item, "category_basis", None) or ""),
            )
            for item in items
        ),
        "llm_fallback_chunks": llm_fallback_chunks,
    }


# ════════════════════════════════════════════════════════════════════════════
# 审计主逻辑
# ════════════════════════════════════════════════════════════════════════════


def load_downgraded_holdout_truth(
    truth_path: Path = DOWNGRADED_HOLDOUT_TRUTH_PATH,
) -> dict[str, dict]:
    """加载已降级为训练样本的首轮 holdout 真值（yamlish 密封格式）。

    职责: 兼容 markdown 表格与 yamlish 两种真值格式。
    不做: 不读取 round2 冻结验证集真值。
    """
    if not truth_path.exists():
        return {}
    content = truth_path.read_text(encoding="utf-8")
    parsed = parse_truth_markdown(content)
    if parsed:
        return parsed
    # 惰性导入避免模块级循环（run_scoring_holdout_admission 反向 import 本模块）；
    # 直接以脚本方式执行时 backend/ 不在 sys.path，需先补路径。
    _ensure_backend_on_path()
    from scripts.run_scoring_holdout_admission import parse_sealed_truth_items

    return parse_sealed_truth_items(content)


def audit_training_set(
    truth_path: Path = TRUTH_PATH,
    corpus_dir: Path = CORPUS_DIR,
    *,
    include_downgraded_holdout: bool = True,
) -> list[dict]:
    """对训练集全部样本执行对比审计，返回逐样本结果列表。

    主口径为（归一化名称, 分值）二元组匹配（pair_*），纯名称口径保留为对照列。
    """
    truth_data = parse_truth_markdown(truth_path.read_text(encoding="utf-8"))
    sample_specs: list[tuple[str, Path, dict]] = [
        (key, corpus_dir, truth_data.get(key, {})) for key in TRAINING_KEYS
    ]

    if include_downgraded_holdout:
        downgraded_truth = load_downgraded_holdout_truth()
        sample_specs.extend(
            (key, DOWNGRADED_HOLDOUT_CORPUS_DIR, downgraded_truth.get(key, {}))
            for key in DOWNGRADED_HOLDOUT_KEYS
        )

    results: list[dict] = []
    for key, sample_corpus_dir, truth_entry in sample_specs:
        truth_items = truth_entry.get("items", [])
        declared_total = truth_entry.get("declared_total", 100.0)
        truth_names = [item["item_name"] for item in truth_items]

        try:
            extracted = run_deterministic_extraction_for_sample(key, corpus_dir=sample_corpus_dir)
        except Exception as exc:  # noqa: BLE001
            results.append({
                "key": key,
                "error": str(exc),
                "truth_count": len(truth_names),
                "extracted_count": 0,
                "precision": 0.0,
                "recall": 0.0,
                "f1": 0.0,
                "true_positive": 0,
                "pair_precision": 0.0,
                "pair_recall": 0.0,
                "pair_f1": 0.0,
                "pair_true_positive": 0,
                "extracted_total": 0.0,
                "declared_total": declared_total,
                "closed": False,
                "gap": -declared_total,
            })
            continue

        extracted_names = extracted.get("names", [])
        extracted_pairs = extracted.get("item_pairs", [])
        extracted_total = extracted.get("total", 0.0)

        name_metrics = compute_item_metrics(truth_names, extracted_names)
        pair_metrics = compute_item_pair_metrics(truth_items, extracted_pairs)
        closure = compute_closure_status(extracted_total, declared_total)

        results.append({
            "key": key,
            "error": None,
            "truth_count": len(truth_names),
            "extracted_count": extracted.get("item_count", 0),
            "precision": name_metrics["precision"],
            "recall": name_metrics["recall"],
            "f1": name_metrics["f1"],
            "true_positive": name_metrics["true_positive"],
            "pair_precision": pair_metrics["precision"],
            "pair_recall": pair_metrics["recall"],
            "pair_f1": pair_metrics["f1"],
            "pair_true_positive": pair_metrics["true_positive"],
            "extracted_total": extracted_total,
            "declared_total": declared_total,
            "closed": closure["closed"],
            "gap": closure["gap"],
            "llm_fallback_chunks": extracted.get("llm_fallback_chunks", 0),
        })

    return results


# ════════════════════════════════════════════════════════════════════════════
# 报告生成
# ════════════════════════════════════════════════════════════════════════════


def format_report(results: list[dict], run_date: str = "") -> str:
    """将审计结果格式化为 Markdown 报告。"""
    from datetime import date as _date

    date_str = run_date or str(_date.today())
    sample_count = len(results)
    error_count = sum(1 for r in results if r.get("error"))

    valid = [r for r in results if not r.get("error")]
    avg_precision = round(sum(r["precision"] for r in valid) / len(valid), 4) if valid else 0.0
    avg_recall = round(sum(r["recall"] for r in valid) / len(valid), 4) if valid else 0.0
    avg_f1 = round(sum(r["f1"] for r in valid) / len(valid), 4) if valid else 0.0
    closed_count = sum(1 for r in valid if r["closed"])
    closure_rate = round(closed_count / len(valid), 4) if valid else 0.0

    lines: list[str] = [
        "# 评分项提取 Legacy 基线审计报告（P1 训练集）",
        "",
        f"**审计日期**: {date_str}",
        f"**训练集样本数**: {sample_count}（01~21, 27, 28）",
        f"**提取错误样本数**: {error_count}",
        "",
        "## 汇总指标",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| item-level precision (均值) | {avg_precision:.4f} |",
        f"| item-level recall (均值) | {avg_recall:.4f} |",
        f"| item-level F1 (均值) | {avg_f1:.4f} |",
        f"| 闭合样本数 | {closed_count}/{len(valid)} |",
        f"| 闭合率 | {closure_rate:.2%} |",
        "",
        "## 逐样本摘要",
        "",
        "| sample_id | truth | extracted | TP | P | R | F1 | extr_total | decl_total | closed | gap |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|",
    ]

    for r in results:
        if r.get("error"):
            lines.append(
                f"| {r['key']} | {r['truth_count']} | ERR | - | - | - | - | - | {r['declared_total']:.1f} | No | ERR |"
            )
        else:
            closed_str = "Yes" if r["closed"] else "No"
            lines.append(
                f"| {r['key']} | {r['truth_count']} | {r['extracted_count']} "
                f"| {r['true_positive']} "
                f"| {r['precision']:.3f} | {r['recall']:.3f} | {r['f1']:.3f} "
                f"| {r['extracted_total']:.1f} | {r['declared_total']:.1f} "
                f"| {closed_str} | {r['gap']:+.1f} |"
            )

    lines += [
        "",
        "## 说明",
        "",
        "- **P/R/F1**: 基于归一化名称集合匹配（剥离空白和末尾数字消歧后缀）。",
        "- **closed**: `|extracted_total - declared_total| <= 1.0 分` 判定为闭合。",
        "- **gap**: `extracted_total - declared_total`，负值表示漏提。",
        "- 留出集（holdout）不在本报告中。",
        "",
    ]

    return "\n".join(lines)


def format_report_v2(results: list[dict], run_date: str = "") -> str:
    """将审计结果格式化为换轴重构 P0 内容口径基线报告（Markdown）。

    主口径为（名称,分值）二元组匹配，纯名称口径保留为对照列。
    """
    from datetime import date as _date

    date_str = run_date or str(_date.today())
    sample_count = len(results)
    error_count = sum(1 for r in results if r.get("error"))

    valid = [r for r in results if not r.get("error")]

    def _avg(field: str) -> float:
        return round(sum(r[field] for r in valid) / len(valid), 4) if valid else 0.0

    closed_count = sum(1 for r in valid if r["closed"])
    closure_rate = round(closed_count / len(valid), 4) if valid else 0.0

    lines: list[str] = [
        "# 评分项提取内容口径基线报告（换轴重构 P0-2）",
        "",
        f"**审计日期**: {date_str}",
        f"**训练集样本数**: {sample_count}（01~21, 27, 28 + 降级首轮holdout 23~26, 29~34）",
        f"**提取错误样本数**: {error_count}",
        "**主口径**: （归一化名称, 分值±0.01）二元组匹配（pair_*）；纯名称口径为对照列。",
        "",
        "## 汇总指标",
        "",
        "| 指标 | 二元组主口径 | 纯名称对照 |",
        "|---|---:|---:|",
        f"| item-level precision (均值) | {_avg('pair_precision'):.4f} | {_avg('precision'):.4f} |",
        f"| item-level recall (均值) | {_avg('pair_recall'):.4f} | {_avg('recall'):.4f} |",
        f"| item-level F1 (均值) | {_avg('pair_f1'):.4f} | {_avg('f1'):.4f} |",
        "",
        f"- 闭合样本数: {closed_count}/{len(valid)}（闭合率 {closure_rate:.2%}）",
        "",
        "## 逐样本摘要",
        "",
        "| sample_id | truth | extracted | pairTP | pairP | pairR | pairF1 | nameF1 | extr_total | decl_total | closed | gap |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|",
    ]

    for r in results:
        if r.get("error"):
            lines.append(
                f"| {r['key']} | {r['truth_count']} | ERR | - | - | - | - | - | - "
                f"| {r['declared_total']:.1f} | No | ERR |"
            )
        else:
            closed_str = "Yes" if r["closed"] else "No"
            lines.append(
                f"| {r['key']} | {r['truth_count']} | {r['extracted_count']} "
                f"| {r['pair_true_positive']} "
                f"| {r['pair_precision']:.3f} | {r['pair_recall']:.3f} | {r['pair_f1']:.3f} "
                f"| {r['f1']:.3f} "
                f"| {r['extracted_total']:.1f} | {r['declared_total']:.1f} "
                f"| {closed_str} | {r['gap']:+.1f} |"
            )

    lines += [
        "",
        "## 说明",
        "",
        "- **pair 系列**: （归一化名称, 分值±0.01）二元组多重集匹配——补偿性错误不可穿透。",
        "- **nameF1**: 旧纯名称集合口径，仅作对照。",
        "- **closed**: `|extracted_total - declared_total| <= 1.0 分` 判定为闭合。",
        "- 冻结验证集（structure-35~44）不在本报告中，禁止在准入前接触。",
        "",
    ]

    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════════
# 入口
# ════════════════════════════════════════════════════════════════════════════


def main() -> None:
    """运行完整审计并写入内容口径基线报告（v2；不覆盖 P1 历史报告）。"""
    REPORT_PATH_V2.parent.mkdir(parents=True, exist_ok=True)
    print(f"[audit] 读取真值: {TRUTH_PATH}")
    print(f"[audit] 降级holdout真值: {DOWNGRADED_HOLDOUT_TRUTH_PATH}")
    print(f"[audit] 语料库: {CORPUS_DIR} + {DOWNGRADED_HOLDOUT_CORPUS_DIR}")
    print(f"[audit] 共 {len(TRAINING_KEYS) + len(DOWNGRADED_HOLDOUT_KEYS)} 个训练样本")

    results = audit_training_set()

    report = format_report_v2(results)
    REPORT_PATH_V2.write_text(report, encoding="utf-8")
    print(f"[audit] 报告已写入: {REPORT_PATH_V2}")

    # 摘要打印
    closed = sum(1 for r in results if not r.get("error") and r["closed"])
    print(f"[audit] 闭合: {closed}/{len(results)}")
    valid = [r for r in results if not r.get("error")]
    if valid:
        avg_pair_p = sum(r["pair_precision"] for r in valid) / len(valid)
        avg_pair_r = sum(r["pair_recall"] for r in valid) / len(valid)
        avg_pair_f1 = sum(r["pair_f1"] for r in valid) / len(valid)
        avg_name_f1 = sum(r["f1"] for r in valid) / len(valid)
        print(
            f"[audit] pair口径 avg P={avg_pair_p:.3f}, R={avg_pair_r:.3f}, F1={avg_pair_f1:.3f}"
            f" | name口径 avg F1={avg_name_f1:.3f}"
        )


if __name__ == "__main__":
    main()
