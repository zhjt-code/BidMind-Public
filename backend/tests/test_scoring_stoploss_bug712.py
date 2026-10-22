""" 止损: Shadow 默认与部分卷定义拒收。"""

import re

import pytest

from app.core.config import Settings
from app.core.extractor.scoring.volume_parser import parse_flat_volume_composition
from app.core.scoring_full_candidate_config import ScoringFullCandidateSettings

pytestmark = pytest.mark.scoring

COMPOSITION_2308 = (
    "编列内容 资信业绩部分:3分 分值构成 项目管理大纲部分:30分 "
    "2.2.1(总分100分) 投标报价:60分 其他评分因素:7分"
)


def test_old_llm_paths_default_to_shadow() -> None:
    assert Settings().SCORING_SCHEME_READER_MODE == "shadow"
    assert ScoringFullCandidateSettings().SCORING_FULL_CANDIDATE_MODE == "shadow"


def test_composition_rejects_partial_label_mapping() -> None:
    from app.core.extractor.scoring.volume_parser import (
        _FLAT_LABEL_SKIP_TERMS,
        _FLAT_PAIR_PATTERN,
        _composition_volume,
    )

    text = (
        "分值构成（总分100分）：资信业绩部分:3分、未知专业部分:30分、"
        "投标报价:60分、其他评分因素:7分"
    )
    pairs: list[str] = []
    for match in _FLAT_PAIR_PATTERN.finditer(text):
        label = match.group(1).strip()
        if any(term in label for term in _FLAT_LABEL_SKIP_TERMS):
            continue
        pairs.append(label)
    assert len(pairs) == 4, f"expected 4 parsed pairs, got {pairs}"
    assert _composition_volume("未知专业部分") is None
    assert all(_composition_volume(label) is not None for label in pairs if label != "未知专业部分")

    definitions = parse_flat_volume_composition(text)
    assert definitions == {}


def test_composition_maps_all_2308_groups() -> None:
    definitions = parse_flat_volume_composition(COMPOSITION_2308)
    assert {
        key: definition.volume_max for key, definition in definitions.items()
    } == {
        "qualification": 3.0,
        "technical": 30.0,
        "price": 60.0,
        "other": 7.0,
    }
    assert sum(item.volume_max for item in definitions.values()) == 100.0
    assert definitions["qualification"].label == "资信业绩部分"


def test_inline_composition_rejects_partial_label_mapping() -> None:
    from app.core.extractor.scoring.volume_parser import (
        _INLINE_COMPOSITION_TRIGGER_RE,
        _INLINE_PAIR_RE,
        _composition_volume,
    )

    text = "总分100分，其中资信3分、未知专业30分、投标报价60分、其他评分因素7分"
    assert _INLINE_COMPOSITION_TRIGGER_RE.search(text) is not None
    trigger = _INLINE_COMPOSITION_TRIGGER_RE.search(text)
    assert trigger is not None
    tail = text[trigger.end():]
    pairs: list[str] = []
    for match in _INLINE_PAIR_RE.finditer(tail):
        label = match.group(1).strip().strip("、，, ")
        if label:
            pairs.append(label)
    assert len(pairs) == 4, f"expected 4 inline pairs, got {pairs}"
    assert _composition_volume("未知专业部分") is None
    assert all(
        _composition_volume(f"{label}部分") is not None
        for label in pairs
        if label != "未知专业"
    )

    definitions = parse_flat_volume_composition(text)
    assert definitions == {}


def test_each_part_composition_rejects_partial_label_mapping() -> None:
    from app.core.extractor.scoring.volume_parser import (
        _EACH_PART_PATTERN,
        _composition_volume,
    )

    text = (
        "分值构成 分技术、未知专业、价格三个部分分别进行量化评审,"
        "每个部分满分均为100分,并按分值合计计算最终量化评审得分。"
    )
    assert _EACH_PART_PATTERN.search(text) is not None
    match = _EACH_PART_PATTERN.search(text)
    assert match is not None
    labels = [
        token.strip()
        for token in re.split(r"[、，,和及]+", match.group(1))
        if token.strip()
    ]
    assert len(labels) == 3
    unknown_labels = [label for label in labels if _composition_volume(label) is None]
    assert unknown_labels == ["未知专业"]
    assert sum(1 for label in labels if _composition_volume(label) is not None) == 2

    definitions = parse_flat_volume_composition(text)
    assert definitions == {}
