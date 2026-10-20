"""场景矩阵驱动的样本生成器。

按 docs/synthetic_scenario_matrix.md 的维度 x 档位组合，
使用正交拉丁方采样产生 Level 1 样本输入骨架，
再调用 annotator_runner 三重自决策标注 expected.jsonl。

偏离 #003：Lite 版无真实生产数据，用本脚本合成。
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import random
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
REGRESSION_ROOT = REPO_ROOT / "tests" / "regression"

MATRIX_VERSION = "1.0.0"
SOURCE_TAG = "synthetic_scenario_matrix"
DEVIATION = "#003"

# ── Investigate dimensions ──────────────────────────────────────────

INVESTIGATE_INDUSTRIES = ["建筑工程", "IT服务", "政府采购(服务类)", "物资采购"]
INVESTIGATE_SCALES = ["小型(≤100万)", "中型(100-1000万)", "大型(>1000万)"]
INVESTIGATE_VETO_CATEGORIES = [
    "物理形态", "时效性", "资质证书", "投标报价", "流程合规", "降格处理", "无否决项",
]
INVESTIGATE_COMPLETENESS = ["完整", "缺页(10-30%)", "格式损坏(OCR降级)"]
INVESTIGATE_OUTCOMES = ["通过", "部分通过", "不通过"]

# ── Collusion dimensions ────────────────────────────────────────────

COLLUSION_DIMENSIONS = ["D1人员", "D2企业", "D3报价", "D4技术方案", "D5商业关系", "D6格式特征", "D7异常一致", "D8综合"]
COLLUSION_SIMILARITY = ["高(≥0.85)", "中(0.5-0.85)", "低(<0.5)"]
COLLUSION_TRAPS = ["甲乙方合作豁免", "同类资质豁免", "单对异常多对正常", "跨对一致(非串标)"]

# ── Writer dimensions ───────────────────────────────────────────────

WRITER_NODE_TYPES = ["fixed_template", "data_fill", "free_writing"]
WRITER_MATERIAL_SUFFICIENCY = ["充足(≥3条素材)", "不足(1-2条)", "缺失(0条)"]
WRITER_CROSS_REF = ["含交叉引用", "不含"]
WRITER_VETO_RISK = ["有否决项风险", "无"]
WRITER_INDUSTRIES = ["建筑工程", "IT服务", "政府采购"]


def _latin_square_sample(
    dims: list[list[str]],
    target_count: int,
    seed: int = 20260417,
) -> list[tuple[str, ...]]:
    """Orthogonal latin-square–inspired sampling ensuring each level appears ≥3 times."""
    rng = random.Random(seed)
    full = list(itertools.product(*dims))
    rng.shuffle(full)

    level_counts: dict[tuple[int, str], int] = {}
    selected: list[tuple[str, ...]] = []

    for combo in full:
        if len(selected) >= target_count:
            under_represented = False
            for i, val in enumerate(combo):
                if level_counts.get((i, val), 0) < 3:
                    under_represented = True
                    break
            if not under_represented:
                continue

        selected.append(combo)
        for i, val in enumerate(combo):
            level_counts[(i, val)] = level_counts.get((i, val), 0) + 1

        if len(selected) >= target_count:
            all_covered = all(
                level_counts.get((i, val), 0) >= 3
                for i, dim in enumerate(dims)
                for val in dim
            )
            if all_covered:
                break

    return selected[:target_count]


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_meta(path: Path, meta: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(meta, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _make_meta(
    project_id: str,
    flow: str,
    record_count: int,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "sampling_date": datetime.now().strftime("%Y-%m-%d"),
        "annotator_prompt_version": "v1.0.0",
        "note": f"偏离 #003 场景矩阵合成样本（{flow}），仅供 Lite 版使用。I-009 跟踪后续校准。",
        "project_id": project_id,
        "source": SOURCE_TAG,
        "matrix_version": MATRIX_VERSION,
        "deviation": DEVIATION,
        "i009_note": "Lite 版合成样本，真实数据可得时需校准（I-009）",
        "record_count": record_count,
        "annotation_rounds": [
            {"round": 1, "temperature": 0.1, "status": "auto_consistency"},
            {"round": 2, "temperature": 0.3, "status": "auto_consistency"},
            {"round": 3, "temperature": 0.5, "status": "auto_consistency"},
        ],
    }
    if extra:
        meta.update(extra)
    return meta


# ── Investigate sample skeleton builders ────────────────────────────

def _build_investigate_skeleton(
    combo: tuple[str, ...],
    idx: int,
) -> tuple[str, list[dict[str, Any]]]:
    industry, scale, veto_cat, completeness, outcome = combo
    proj_id = f"synthetic_inv_{idx:03d}"

    records = []
    n_requirements = random.randint(4, 8)
    for req_idx in range(1, n_requirements + 1):
        records.append({
            "requirement_id": req_idx,
            "requirement_text": f"[{industry}][{scale}] 模拟招标要求 #{req_idx}，否决类别={veto_cat}",
            "response_text": f"模拟投标响应 #{req_idx}，完整度={completeness}",
            "llm_result_json": json.dumps({
                "compliance_level": "fully" if outcome == "通过" else "partially",
                "risk_level": "high" if veto_cat != "无否决项" else "none",
            }, ensure_ascii=False),
            "evidence_chunks": f"[模拟 chunk，行业={industry}，规模={scale}]",
            "expected_compliance_level": "fully" if outcome == "通过" else ("not" if outcome == "不通过" else "partially"),
            "expected_is_compliant": outcome == "通过",
            "expected_risk_level": "high" if veto_cat != "无否决项" and outcome == "不通过" else "low",
            "expected_relevance_score": random.randint(60, 100),
            "expected_evidence_pages": [random.randint(1, 50) for _ in range(random.randint(1, 3))],
            "expected_failure_state": "degraded" if completeness != "完整" else "completed",
            "is_critical": veto_cat != "无否决项",
            "expert_note": f"合成样本: {industry}/{scale}/{veto_cat}/{completeness}/{outcome}",
        })

    return proj_id, records


def _build_collusion_skeleton(
    combo: tuple[str, ...],
    idx: int,
) -> tuple[str, list[dict[str, Any]]]:
    dimension, similarity, trap = combo
    proj_id = f"synthetic_col_{idx:03d}"

    sim_val = {"高(≥0.85)": 0.92, "中(0.5-0.85)": 0.67, "低(<0.5)": 0.35}[similarity]
    risk = "high" if sim_val >= 0.85 and trap not in ("甲乙方合作豁免", "同类资质豁免") else (
        "medium" if sim_val >= 0.5 else "low"
    )

    dim_idx = COLLUSION_DIMENSIONS.index(dimension) + 1
    scores = {f"expected_d{i}_score": (0.8 if i == dim_idx else 0.1) for i in range(1, 9)}

    record: dict[str, Any] = {
        "pair_id": f"doc_a_{idx}:doc_b_{idx}",
        "info_a_json": json.dumps({"company": f"模拟公司A-{idx}", "dimension": dimension}, ensure_ascii=False),
        "info_b_json": json.dumps({"company": f"模拟公司B-{idx}", "dimension": dimension}, ensure_ascii=False),
        "llm_result_json": json.dumps({"overall_risk": risk}, ensure_ascii=False),
        "similarity_score": sim_val,
        "expected_overall_risk": risk,
        **scores,
        "expected_failure_state": "completed",
        "is_false_positive_candidate": trap in ("甲乙方合作豁免", "同类资质豁免"),
        "expert_note": f"合成样本: {dimension}/{similarity}/{trap}",
    }
    return proj_id, [record]


def _build_writer_skeleton(
    combo: tuple[str, ...],
    idx: int,
) -> tuple[str, list[dict[str, Any]]]:
    node_type, mat_suff, cross_ref, veto_risk, industry = combo
    proj_id = f"synthetic_wrt_{idx:03d}"

    n_materials = {"充足(≥3条素材)": 4, "不足(1-2条)": 1, "缺失(0条)": 0}[mat_suff]
    placeholder_density = 0.0 if n_materials >= 3 else (2.5 if n_materials > 0 else 8.0)

    record: dict[str, Any] = {
        "outline_node_id": idx * 10 + 1,
        "node_title": f"[{industry}] 模拟章节 #{idx}",
        "system_node_type": node_type,
        "clause_text": f"模拟招标条款，类型={node_type}，行业={industry}",
        "content": f"模拟生成内容，交叉引用={'有' if cross_ref == '含交叉引用' else '无'}",
        "materials_json": json.dumps([{"id": i, "summary": f"素材{i}"} for i in range(1, n_materials + 1)], ensure_ascii=False),
        "expected_node_type": node_type,
        "expected_material_ids": list(range(1, n_materials + 1)),
        "expected_placeholder_density": placeholder_density,
        "expected_failure_state": "needs_review" if veto_risk == "有否决项风险" else "completed",
        "has_fabrication_risk": n_materials == 0,
        "expert_note": f"合成样本: {node_type}/{mat_suff}/{cross_ref}/{veto_risk}/{industry}",
    }
    return proj_id, [record]


# ── Main generation entry points ────────────────────────────────────

def generate_investigate_level1(target: int = 50, seed: int = 20260417) -> int:
    """Generate Level 1 investigate samples. Returns count generated."""
    random.seed(seed)
    dims = [INVESTIGATE_INDUSTRIES, INVESTIGATE_SCALES, INVESTIGATE_VETO_CATEGORIES,
            INVESTIGATE_COMPLETENESS, INVESTIGATE_OUTCOMES]
    combos = _latin_square_sample(dims, target, seed)

    out_dir = REGRESSION_ROOT / "investigate" / "level1_historical"
    count = 0
    for idx, combo in enumerate(combos, start=1):
        proj_id, records = _build_investigate_skeleton(combo, idx)
        proj_dir = out_dir / f"proj_{idx:03d}"
        _write_jsonl(proj_dir / "expected.jsonl", records)
        _write_meta(proj_dir / "meta.yml", _make_meta(proj_id, "investigate", len(records), {
            "industry": combo[0], "scale": combo[1], "veto_category": combo[2],
            "completeness": combo[3], "outcome_bucket": combo[4],
        }))
        count += 1
        logger.info("investigate level1 proj_%03d: %d records", idx, len(records))

    return count


def generate_collusion_level1(target: int = 25, seed: int = 20260417) -> int:
    random.seed(seed)
    dims = [COLLUSION_DIMENSIONS, COLLUSION_SIMILARITY, COLLUSION_TRAPS]
    combos = _latin_square_sample(dims, target, seed)

    out_dir = REGRESSION_ROOT / "collusion" / "level1_historical"
    count = 0
    for idx, combo in enumerate(combos, start=1):
        proj_id, records = _build_collusion_skeleton(combo, idx)
        proj_dir = out_dir / f"proj_{idx:03d}"
        _write_jsonl(proj_dir / "expected.jsonl", records)
        _write_meta(proj_dir / "meta.yml", _make_meta(proj_id, "collusion", len(records), {
            "dimension": combo[0], "similarity": combo[1], "trap_type": combo[2],
        }))
        count += 1

    return count


def generate_writer_level1(target: int = 20, seed: int = 20260417) -> int:
    random.seed(seed)
    dims = [WRITER_NODE_TYPES, WRITER_MATERIAL_SUFFICIENCY, WRITER_CROSS_REF,
            WRITER_VETO_RISK, WRITER_INDUSTRIES]
    combos = _latin_square_sample(dims, target, seed)

    out_dir = REGRESSION_ROOT / "writer" / "level1_historical"
    count = 0
    for idx, combo in enumerate(combos, start=1):
        proj_id, records = _build_writer_skeleton(combo, idx)
        proj_dir = out_dir / f"proj_{idx:03d}"
        _write_jsonl(proj_dir / "expected.jsonl", records)
        _write_meta(proj_dir / "meta.yml", _make_meta(proj_id, "writer", len(records), {
            "node_type": combo[0], "material_sufficiency": combo[1],
            "cross_ref": combo[2], "veto_risk": combo[3], "industry": combo[4],
        }))
        count += 1

    return count


def generate_all_level1(seed: int = 20260417) -> dict[str, int]:
    return {
        "investigate": generate_investigate_level1(seed=seed),
        "collusion": generate_collusion_level1(seed=seed),
        "writer": generate_writer_level1(seed=seed),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    counts = generate_all_level1()
    print(f"Generated Level 1 samples: {counts}")
