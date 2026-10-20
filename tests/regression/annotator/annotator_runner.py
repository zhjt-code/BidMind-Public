"""三流程标注自决策 runner。

封装 annotator/prompts/ 下的 prompt 常量为可调用的标注接口，
支持多 seed 一致性检查与反向验证。

偏离 #003：Lite 版无人工标注带宽，用三重自决策替代人工核验。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from typing import Any, Literal

from tests.regression.annotator.prompts.investigate_annotator import (
    INVESTIGATE_ANNOTATOR_STRICT,
    INVESTIGATE_ANNOTATOR_SYSTEM,
    INVESTIGATE_ANNOTATOR_USER,
)
from tests.regression.annotator.prompts.collusion_annotator import (
    COLLUSION_ANNOTATOR_STRICT,
    COLLUSION_ANNOTATOR_SYSTEM,
    COLLUSION_ANNOTATOR_USER,
)
from tests.regression.annotator.prompts.writer_annotator import (
    WRITER_ANNOTATOR_STRICT,
    WRITER_ANNOTATOR_SYSTEM,
    WRITER_ANNOTATOR_USER,
)

logger = logging.getLogger(__name__)

Flow = Literal["investigate", "collusion", "writer"]

TEMPERATURE_SEEDS = [0.1, 0.3, 0.5]

_PROMPTS: dict[str, dict[str, str]] = {
    "investigate": {
        "system": INVESTIGATE_ANNOTATOR_SYSTEM,
        "user": INVESTIGATE_ANNOTATOR_USER,
        "strict": INVESTIGATE_ANNOTATOR_STRICT,
    },
    "collusion": {
        "system": COLLUSION_ANNOTATOR_SYSTEM,
        "user": COLLUSION_ANNOTATOR_USER,
        "strict": COLLUSION_ANNOTATOR_STRICT,
    },
    "writer": {
        "system": WRITER_ANNOTATOR_SYSTEM,
        "user": WRITER_ANNOTATOR_USER,
        "strict": WRITER_ANNOTATOR_STRICT,
    },
}

JUDGE_SYSTEM = """你是一名质量审核员。你会收到一条 ground truth 标注结果和它对应的原始样本。
请判定该标注是否自洽（标注结论与输入证据一致、无逻辑矛盾、字段间不冲突）。
只返回 JSON: {"consistent": true|false, "reason": "简短理由"}"""

JUDGE_USER = """## 原始样本
{sample_json}

## 候选标注
{expected_json}

请判定该标注是否自洽。"""


def _hash_fields(d: dict[str, Any], keys: list[str] | None = None) -> str:
    """Deterministic hash of selected fields for consistency comparison."""
    subset = {k: v for k, v in sorted(d.items()) if keys is None or k in keys}
    return hashlib.sha256(json.dumps(subset, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _call_llm(
    system: str,
    user: str,
    temperature: float = 0.1,
) -> dict[str, Any]:
    """Call project LLM via cloud_client. Returns parsed JSON dict.

    This is a thin wrapper; actual LLM call uses the project's existing
    CloudClient infrastructure. In dry-run / offline mode, callers should
    mock this function.
    """
    try:
        from backend.app.core.ai.cloud_client import get_cloud_client

        client = get_cloud_client()
        raw = client.chat_json(
            system_prompt=system,
            user_prompt=user,
            temperature=temperature,
        )
        if isinstance(raw, dict):
            return raw
        return json.loads(raw)
    except Exception:
        logger.exception("LLM call failed in annotator runner")
        return {}


def run(
    flow: Flow,
    sample: dict[str, Any],
    temperature: float = 0.1,
    use_strict: bool = False,
) -> dict[str, Any]:
    """Run a single annotation pass for the given flow and sample.

    Args:
        flow: One of "investigate", "collusion", "writer".
        sample: Dict with flow-specific input fields used to fill prompt templates.
        temperature: LLM sampling temperature.
        use_strict: If True, use the STRICT variant of the prompt.

    Returns:
        Parsed expected dict from LLM, or empty dict on failure.
    """
    prompts = _PROMPTS[flow]
    system = prompts["system"]

    if use_strict:
        user_filled = prompts["user"].format(**sample)
        user = prompts["strict"].format(standard_user_prompt=user_filled)
    else:
        user = prompts["user"].format(**sample)

    return _call_llm(system, user, temperature=temperature)


def run_self_consistency(
    flow: Flow,
    sample: dict[str, Any],
    n_seeds: int = 3,
    agreement_threshold: float = 2 / 3,
) -> tuple[dict[str, Any] | None, float]:
    """Run multiple annotation passes and check field-level consistency.

    Strategy:
      - Runs standard prompt at temperatures in TEMPERATURE_SEEDS[:n_seeds].
      - If n_seeds > len(TEMPERATURE_SEEDS), also runs STRICT variant.
      - Computes majority vote per field.
      - Returns (majority_result, agreement_rate).
      - Returns (None, rate) if agreement < threshold.
    """
    temps = TEMPERATURE_SEEDS[:n_seeds]
    results: list[dict[str, Any]] = []

    for t in temps:
        r = run(flow, sample, temperature=t, use_strict=False)
        if r:
            results.append(r)

    if len(results) < 2:
        return None, 0.0

    all_keys = set()
    for r in results:
        all_keys.update(r.keys())

    majority: dict[str, Any] = {}
    agreements = 0
    total_fields = len(all_keys)

    for key in sorted(all_keys):
        values = [json.dumps(r.get(key), sort_keys=True, ensure_ascii=False) for r in results]
        counter = Counter(values)
        most_common_val, most_common_count = counter.most_common(1)[0]
        if most_common_count / len(results) >= agreement_threshold:
            majority[key] = json.loads(most_common_val)
            agreements += 1
        else:
            majority[key] = json.loads(most_common_val)

    rate = agreements / total_fields if total_fields > 0 else 0.0

    if rate < agreement_threshold:
        return None, rate

    return majority, rate


def run_cross_judge(
    flow: Flow,
    sample: dict[str, Any],
    candidate_expected: dict[str, Any],
) -> bool:
    """Verify a candidate annotation via a separate judge prompt.

    Returns True if the judge deems the annotation self-consistent.
    """
    user = JUDGE_USER.format(
        sample_json=json.dumps(sample, ensure_ascii=False, indent=2),
        expected_json=json.dumps(candidate_expected, ensure_ascii=False, indent=2),
    )
    result = _call_llm(JUDGE_SYSTEM, user, temperature=0.0)
    return result.get("consistent", False) is True


def annotate_with_triple_check(
    flow: Flow,
    sample: dict[str, Any],
    n_seeds: int = 3,
) -> tuple[dict[str, Any] | None, float, bool]:
    """Full triple-check annotation pipeline.

    Returns:
        (expected_dict_or_None, consistency_rate, judge_passed)

    Collection rule:
      - consistency_rate >= 2/3 AND judge_passed -> collect into Level 1/2
      - Otherwise -> discard or log to low-confidence list
    """
    candidate, rate = run_self_consistency(flow, sample, n_seeds=n_seeds)

    if candidate is None:
        return None, rate, False

    judge_ok = run_cross_judge(flow, sample, candidate)

    if not judge_ok:
        return candidate, rate, False

    return candidate, rate, True
