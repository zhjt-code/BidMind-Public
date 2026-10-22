"""Level 2 对抗样本生成器。

对 Level 1 正常样本注入 8 类攻击变体，每类每流程 3 条。
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
REGRESSION_ROOT = REPO_ROOT / "tests" / "regression"

SOURCE_TAG = "synthetic_scenario_matrix"
MATRIX_VERSION = "1.0.0"

ATTACK_TYPES = [
    "truncation",
    "fabrication",
    "contradiction",
    "domain_mismatch",
    "boundary",
    "empty_return",
    "template_confusion",
    "numbering_date_ambiguity",
]

FLOWS = ["investigate", "collusion", "writer"]

ATTACK_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "truncation": {
        "inject": "LLM 输出截断在 JSON 中部",
        "expected_state": "degraded",
    },
    "fabrication": {
        "inject": "伪造不存在的证据编号/资质证书",
        "expected_state": "needs_review",
    },
    "contradiction": {
        "inject": "同一字段在不同位置给出矛盾值",
        "expected_state": "needs_review",
    },
    "domain_mismatch": {
        "inject": "输入领域与招标领域不匹配",
        "expected_state": "needs_review",
    },
    "boundary": {
        "inject": "关键数值恰好在阈值边界",
        "expected_state": "completed",
    },
    "empty_return": {
        "inject": "LLM 返回空对象/null",
        "expected_state": "inconclusive",
    },
    "template_confusion": {
        "inject": "固定模板字段被 LLM 篡改",
        "expected_state": "needs_review",
    },
    "numbering_date_ambiguity": {
        "inject": "日期/编号格式多义性",
        "expected_state": "needs_review",
    },
}


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_meta(path: Path, meta: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(meta, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _make_attack_meta(
    attack_type: str,
    flow: str,
    record_count: int,
) -> dict[str, Any]:
    desc = ATTACK_DESCRIPTIONS[attack_type]
    return {
        "sampling_date": datetime.now().strftime("%Y-%m-%d"),
        "annotator_prompt_version": "v1.0.0",
        "note": f"偏离 #003 Level 2 对抗样本（{flow}/{attack_type}），仅供 Lite 版使用。",
        "source": SOURCE_TAG,
        "matrix_version": MATRIX_VERSION,
        "deviation": "#003",
        "i009_note": "Lite 版合成样本，真实数据可得时需校准（I-009）",
        "attack_type": attack_type,
        "attack_description": desc["inject"],
        "expected_failure_state": desc["expected_state"],
        "record_count": record_count,
    }


def _generate_investigate_attack(attack_type: str, seed_idx: int) -> dict[str, Any]:
    desc = ATTACK_DESCRIPTIONS[attack_type]
    return {
        "requirement_id": 9000 + seed_idx,
        "requirement_text": f"[对抗] {attack_type} 注入的投标审查要求 #{seed_idx}",
        "response_text": f"[对抗] {desc['inject']}",
        "llm_result_json": "{}" if attack_type == "empty_return" else json.dumps(
            {"compliance_level": "fully", "risk_level": "none"}, ensure_ascii=False
        ),
        "evidence_chunks": "" if attack_type == "empty_return" else f"[对抗 chunk: {attack_type}]",
        "expected_failure_state": desc["expected_state"],
        "is_critical": attack_type in ("fabrication", "template_confusion"),
        "expert_note": f"Level 2 对抗: {attack_type} - {desc['inject']}",
    }


def _generate_collusion_attack(attack_type: str, seed_idx: int) -> dict[str, Any]:
    desc = ATTACK_DESCRIPTIONS[attack_type]
    return {
        "pair_id": f"atk_a_{seed_idx}:atk_b_{seed_idx}",
        "info_a_json": json.dumps({"company": f"对抗公司A-{seed_idx}"}, ensure_ascii=False),
        "info_b_json": json.dumps({"company": f"对抗公司B-{seed_idx}"}, ensure_ascii=False),
        "similarity_score": 0.0 if attack_type == "empty_return" else 0.75,
        "expected_overall_risk": "inconclusive" if attack_type == "empty_return" else "medium",
        "expected_failure_state": desc["expected_state"],
        "is_false_positive_candidate": attack_type == "domain_mismatch",
        "expert_note": f"Level 2 对抗: {attack_type} - {desc['inject']}",
    }


def _generate_writer_attack(attack_type: str, seed_idx: int) -> dict[str, Any]:
    desc = ATTACK_DESCRIPTIONS[attack_type]
    return {
        "outline_node_id": 9000 + seed_idx,
        "node_title": f"[对抗] {attack_type} 注入章节 #{seed_idx}",
        "system_node_type": "free_writing",
        "content": "" if attack_type == "empty_return" else f"[对抗内容: {desc['inject']}]",
        "expected_node_type": "free_writing",
        "expected_failure_state": desc["expected_state"],
        "has_fabrication_risk": attack_type == "fabrication",
        "expert_note": f"Level 2 对抗: {attack_type} - {desc['inject']}",
    }


_FLOW_GENERATORS = {
    "investigate": _generate_investigate_attack,
    "collusion": _generate_collusion_attack,
    "writer": _generate_writer_attack,
}


def generate_level2_for_flow(flow: str, per_attack: int = 3, seed: int = 20260417) -> int:
    """Generate Level 2 attack samples for a single flow. Returns total count."""
    random.seed(seed)
    out_dir = REGRESSION_ROOT / flow / "level2_attack"
    gen_fn = _FLOW_GENERATORS[flow]
    count = 0

    for attack_type in ATTACK_TYPES:
        attack_dir = out_dir / attack_type
        records = []
        for i in range(1, per_attack + 1):
            records.append(gen_fn(attack_type, i))

        _write_jsonl(attack_dir / "expected.jsonl", records)
        _write_meta(attack_dir / "meta.yml", _make_attack_meta(attack_type, flow, len(records)))
        count += len(records)

    return count


def generate_all_level2(per_attack: int = 3, seed: int = 20260417) -> dict[str, int]:
    return {flow: generate_level2_for_flow(flow, per_attack, seed) for flow in FLOWS}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    counts = generate_all_level2()
    print(f"Generated Level 2 attack samples: {counts}")
