"""L-8 对拍防御测试: make_priority_shrink_reducer 与七处原始手写闭包逐字节等价。

七个 golden 实现均为重构前源码的逐字内联复制（仅把闭包提升为模块级函数），
覆盖 outline_generator 四处 / clause_extractor 一处 / composer 两条路由。
断言工厂产物与 golden 输出（reduced dict 与 meta dict）完全相等。
本文件为永久防御测试: 任何工厂行为漂移（分支顺序、min_lines、
kept_segments、str 包装、previous_summaries 前置分支）都会在此失败。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.core.writer.section_composer import _shrink_summary_items
from app.core.writer.text_budget import (
    make_priority_shrink_reducer,
    shrink_semantic_text,
)


# ---------------------------------------------------------------------------
# golden 依赖: 重构前各模块本地 _shrink_semantic_text 的逐字复制
# ---------------------------------------------------------------------------


def _shrink_semantic_text(text: str, *, min_lines: int = 1) -> str:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if len(lines) <= min_lines:
        return text
    keep_count = max(min_lines, len(lines) - max(1, len(lines) // 3))
    return "\n".join(lines[:keep_count])


# ---------------------------------------------------------------------------
# 七处 golden 闭包（逐字复制自重构前源码）
# ---------------------------------------------------------------------------


def _golden_skeleton_enrich(
    payload: dict[str, str],
    retry_round: int,
) -> tuple[dict[str, str], dict[str, object]]:
    """outline_generator._enrich_skeleton_node 骨架丰富（原约551-576行）"""
    reduced = dict(payload)
    removed_segments: list[str] = []
    if reduced["relevant_materials"]:
        reduced["relevant_materials"] = _shrink_semantic_text(reduced["relevant_materials"], min_lines=1)
        removed_segments.append("relevant_materials")
    elif reduced["relevant_content_reqs"]:
        reduced["relevant_content_reqs"] = _shrink_semantic_text(reduced["relevant_content_reqs"], min_lines=1)
        removed_segments.append("relevant_content_reqs")
    elif reduced["relevant_scoring"]:
        reduced["relevant_scoring"] = _shrink_semantic_text(reduced["relevant_scoring"], min_lines=1)
        removed_segments.append("relevant_scoring")
    elif reduced["project_context"]:
        reduced["project_context"] = _shrink_semantic_text(reduced["project_context"], min_lines=1)
        removed_segments.append("project_context")
    else:
        reduced["sibling_titles_text"] = _shrink_semantic_text(reduced["sibling_titles_text"], min_lines=1)
        removed_segments.append("sibling_titles_text")
    return reduced, {
        "removed_segments": removed_segments,
        "kept_segments": ["node_title", "volume_title"],
        "retry_attempt": retry_round,
    }


def _golden_volume_fill(
    payload: dict[str, str],
    retry_round: int,
) -> tuple[dict[str, str], dict[str, object]]:
    """outline_generator._fill_volume 分卷填充（原约803-825行）"""
    reduced = dict(payload)
    removed_segments: list[str] = []
    if reduced["relevant_materials"]:
        reduced["relevant_materials"] = _shrink_semantic_text(reduced["relevant_materials"], min_lines=1)
        removed_segments.append("relevant_materials")
    elif reduced["relevant_content_reqs"]:
        reduced["relevant_content_reqs"] = _shrink_semantic_text(reduced["relevant_content_reqs"], min_lines=1)
        removed_segments.append("relevant_content_reqs")
    elif reduced["relevant_scoring"]:
        reduced["relevant_scoring"] = _shrink_semantic_text(reduced["relevant_scoring"], min_lines=1)
        removed_segments.append("relevant_scoring")
    else:
        reduced["industry_profile"] = _shrink_semantic_text(reduced["industry_profile"], min_lines=2)
        removed_segments.append("industry_profile")
    return reduced, {
        "removed_segments": removed_segments,
        "kept_segments": ["section_title", "category", "directory_rules"],
        "retry_attempt": retry_round,
    }


def _golden_unified(
    payload: dict[str, str],
    retry_round: int,
) -> tuple[dict[str, str], dict[str, object]]:
    """outline_generator._generate_unified（原约869-904行）"""
    reduced = dict(payload)
    removed_segments: list[str] = []
    if reduced.get("material_requirements"):
        reduced["material_requirements"] = _shrink_semantic_text(
            reduced["material_requirements"],
            min_lines=1,
        )
        removed_segments.append("material_requirements")
    elif reduced.get("content_requirements"):
        reduced["content_requirements"] = _shrink_semantic_text(
            reduced["content_requirements"],
            min_lines=1,
        )
        removed_segments.append("content_requirements")
    elif reduced.get("scoring_criteria"):
        reduced["scoring_criteria"] = _shrink_semantic_text(
            reduced["scoring_criteria"],
            min_lines=1,
        )
        removed_segments.append("scoring_criteria")
    else:
        reduced["industry_profile"] = _shrink_semantic_text(
            reduced["industry_profile"],
            min_lines=2,
        )
        removed_segments.append("industry_profile")

    return reduced, {
        "removed_segments": removed_segments,
        "kept_segments": ["directory_rules"],
        "retry_attempt": retry_round,
    }


def _make_golden_chapter_profile(volume_label: str):
    """outline_generator 章节画像（原约1598-1625行，闭包捕获 volume_label）"""

    def _reduce_payload(
        payload: dict[str, str],
        retry_round: int,
    ) -> tuple[dict[str, str], dict[str, object]]:
        reduced = dict(payload)
        removed_segments: list[str] = []
        # 超长优先裁剪 industry_profile_excerpt, 其次 tree_text, 最后 leaf_list
        if reduced.get("industry_profile_excerpt"):
            reduced["industry_profile_excerpt"] = _shrink_semantic_text(
                reduced["industry_profile_excerpt"], min_lines=1,
            )
            removed_segments.append("industry_profile_excerpt")
        elif reduced.get("clause_examples_summary"):
            reduced["clause_examples_summary"] = _shrink_semantic_text(
                reduced["clause_examples_summary"], min_lines=3,
            )
            removed_segments.append("clause_examples_summary")
        elif reduced["tree_text"]:
            reduced["tree_text"] = _shrink_semantic_text(reduced["tree_text"], min_lines=3)
            removed_segments.append("tree_text")
        else:
            reduced["leaf_list"] = _shrink_semantic_text(reduced["leaf_list"], min_lines=3)
            removed_segments.append("leaf_list")
        return reduced, {
            "removed_segments": removed_segments,
            "kept_segments": [volume_label, "chapter_profile_contract"],
            "retry_attempt": retry_round,
        }

    return _reduce_payload


def _golden_bid_structure(
    payload: dict[str, str],
    retry_round: int,
) -> tuple[dict[str, str], dict[str, object]]:
    """clause_extractor.analyze_bid_structure 结构分析（原约496-521行）"""
    reduced = dict(payload)
    removed_segments: list[str] = []
    if reduced.get("material_requirements"):
        reduced["material_requirements"] = _shrink_semantic_text(reduced["material_requirements"], min_lines=1)
        removed_segments.append("material_requirements")
    elif reduced.get("content_requirements"):
        reduced["content_requirements"] = _shrink_semantic_text(reduced["content_requirements"], min_lines=1)
        removed_segments.append("content_requirements")
    elif reduced.get("scoring_criteria"):
        reduced["scoring_criteria"] = _shrink_semantic_text(reduced["scoring_criteria"], min_lines=1)
        removed_segments.append("scoring_criteria")
    elif reduced.get("hard_structure_signals"):
        reduced["hard_structure_signals"] = _shrink_semantic_text(reduced["hard_structure_signals"], min_lines=1)
        removed_segments.append("hard_structure_signals")
    else:
        reduced["industry_profile"] = _shrink_semantic_text(reduced["industry_profile"], min_lines=2)
        removed_segments.append("industry_profile")
    return reduced, {
        "removed_segments": removed_segments,
        "kept_segments": ["directory_rules", "bid_structure_contract"],
        "retry_attempt": retry_round,
    }


def _golden_free_writing(
    payload: dict[str, object],
    retry_round: int,
) -> tuple[dict[str, object], dict[str, object]]:
    """composer/free_writing_route.compose_free（原约283-309行）"""
    reduced = dict(payload)
    removed_segments: list[str] = []
    previous_items = list(reduced.get("previous_summaries", []))
    if len(previous_items) > 3:
        reduced["previous_summaries"] = _shrink_summary_items(previous_items)
        removed_segments.append("previous_summaries")
    elif reduced.get("materials_text"):
        reduced["materials_text"] = _shrink_semantic_text(str(reduced["materials_text"]), min_lines=1)
        removed_segments.append("materials_text")
    elif reduced.get("clauses_text"):
        reduced["clauses_text"] = _shrink_semantic_text(str(reduced["clauses_text"]), min_lines=1)
        removed_segments.append("clauses_text")
    elif reduced.get("chapter_profile_text"):
        reduced["chapter_profile_text"] = _shrink_semantic_text(str(reduced["chapter_profile_text"]), min_lines=1)
        removed_segments.append("chapter_profile_text")
    else:
        reduced["profile_excerpt"] = _shrink_semantic_text(str(reduced["profile_excerpt"]), min_lines=1)
        removed_segments.append("profile_excerpt")
    return reduced, {
        "removed_segments": removed_segments,
        "kept_segments": ["node_title", "selected_material_ids"],
        "retry_attempt": retry_round,
    }


def _golden_data_fill(
    payload: dict[str, object],
    retry_round: int,
) -> tuple[dict[str, object], dict[str, object]]:
    """composer/data_fill_route.fill_data（原约294-316行）"""
    reduced = dict(payload)
    removed_segments: list[str] = []
    if reduced.get("materials_text"):
        reduced["materials_text"] = _shrink_semantic_text(str(reduced["materials_text"]), min_lines=1)
        removed_segments.append("materials_text")
    elif reduced.get("clauses_text"):
        reduced["clauses_text"] = _shrink_semantic_text(str(reduced["clauses_text"]), min_lines=1)
        removed_segments.append("clauses_text")
    elif reduced.get("chapter_profile_text"):
        reduced["chapter_profile_text"] = _shrink_semantic_text(str(reduced["chapter_profile_text"]), min_lines=1)
        removed_segments.append("chapter_profile_text")
    else:
        reduced["profile_excerpt"] = _shrink_semantic_text(str(reduced["profile_excerpt"]), min_lines=1)
        removed_segments.append("profile_excerpt")
    return reduced, {
        "removed_segments": removed_segments,
        "kept_segments": ["node_title", "material_usage_rules"],
        "retry_attempt": retry_round,
    }


# ---------------------------------------------------------------------------
# 工厂实例（与生产调用点参数逐字一致）
# ---------------------------------------------------------------------------


def _make_free_writing_pre_branch():
    """与 free_writing_route 生产前置分支同构（previous_summaries 列表收缩）。"""

    def _shrink_previous_summaries(reduced: dict[str, object]):
        previous_items = list(reduced.get("previous_summaries", []))
        if len(previous_items) > 3:
            reduced["previous_summaries"] = _shrink_summary_items(previous_items)
            return "previous_summaries"
        return None

    return _shrink_previous_summaries


_FACTORY_SKELETON_ENRICH = make_priority_shrink_reducer(
    [
        ("relevant_materials", 1),
        ("relevant_content_reqs", 1),
        ("relevant_scoring", 1),
        ("project_context", 1),
        ("sibling_titles_text", 1),
    ],
    kept_segments=["node_title", "volume_title"],
)

_FACTORY_VOLUME_FILL = make_priority_shrink_reducer(
    [
        ("relevant_materials", 1),
        ("relevant_content_reqs", 1),
        ("relevant_scoring", 1),
        ("industry_profile", 2),
    ],
    kept_segments=["section_title", "category", "directory_rules"],
)

_FACTORY_UNIFIED = make_priority_shrink_reducer(
    [
        ("material_requirements", 1),
        ("content_requirements", 1),
        ("scoring_criteria", 1),
        ("industry_profile", 2),
    ],
    kept_segments=["directory_rules"],
)


def _make_factory_chapter_profile(volume_label: str):
    return make_priority_shrink_reducer(
        [
            ("industry_profile_excerpt", 1),
            ("clause_examples_summary", 3),
            ("tree_text", 3),
            ("leaf_list", 3),
        ],
        kept_segments=[volume_label, "chapter_profile_contract"],
    )


_FACTORY_BID_STRUCTURE = make_priority_shrink_reducer(
    [
        ("material_requirements", 1),
        ("content_requirements", 1),
        ("scoring_criteria", 1),
        ("hard_structure_signals", 1),
        ("industry_profile", 2),
    ],
    kept_segments=["directory_rules", "bid_structure_contract"],
)

_FACTORY_FREE_WRITING = make_priority_shrink_reducer(
    [
        ("materials_text", 1),
        ("clauses_text", 1),
        ("chapter_profile_text", 1),
        ("profile_excerpt", 1),
    ],
    kept_segments=["node_title", "selected_material_ids"],
    coerce_str=True,
    pre_branch=_make_free_writing_pre_branch(),
)

_FACTORY_DATA_FILL = make_priority_shrink_reducer(
    [
        ("materials_text", 1),
        ("clauses_text", 1),
        ("chapter_profile_text", 1),
        ("profile_excerpt", 1),
    ],
    kept_segments=["node_title", "material_usage_rules"],
    coerce_str=True,
)


# ---------------------------------------------------------------------------
# 样本构造
# ---------------------------------------------------------------------------

LONG = "\n".join(f"- 条目{i}: 内容片段若干" for i in range(30))
MID = "\n".join(f"- 中等条目{i}" for i in range(5))
SHORT = "- 仅有一条"
EMPTY = ""

RETRY_ROUNDS = (1, 2, 5)


def _payload(fields: list[str], **overrides) -> dict:
    """默认全字段 LONG，可用 overrides 覆盖为短/空，或用 ... 删除该键。"""
    payload = {name: LONG for name in fields}
    for key, value in overrides.items():
        if value is ...:
            payload.pop(key, None)
        else:
            payload[key] = value
    return payload


def _assert_parity(golden_fn, factory_fn, payload: dict) -> None:
    for retry_round in RETRY_ROUNDS:
        golden_reduced, golden_meta = golden_fn(dict(payload), retry_round)
        factory_reduced, factory_meta = factory_fn(dict(payload), retry_round)
        assert factory_reduced == golden_reduced, (
            f"reduced 不一致: payload={payload!r}, retry={retry_round}"
        )
        assert factory_meta == golden_meta, (
            f"meta 不一致: payload={payload!r}, retry={retry_round}"
        )


# ---------------------------------------------------------------------------
# 0. 共享收缩函数本体对拍
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [LONG, MID, SHORT, EMPTY, "行1\n\n  行2  \n行3"])
@pytest.mark.parametrize("min_lines", [1, 2, 3])
def test_shrink_semantic_text_matches_golden(text: str, min_lines: int) -> None:
    assert shrink_semantic_text(text, min_lines=min_lines) == _shrink_semantic_text(
        text, min_lines=min_lines
    )


# ---------------------------------------------------------------------------
# 1. outline_generator 骨架丰富（5 字段，直接索引风格）
# ---------------------------------------------------------------------------

_SKELETON_FIELDS = [
    "relevant_materials", "relevant_content_reqs", "relevant_scoring",
    "project_context", "sibling_titles_text",
]


@pytest.mark.parametrize("payload", [
    _payload(_SKELETON_FIELDS),                                        # 全字段超长
    _payload(_SKELETON_FIELDS, relevant_materials=EMPTY),              # 部分超长 -> 第2分支
    _payload(_SKELETON_FIELDS, relevant_materials=EMPTY,
             relevant_content_reqs=EMPTY, relevant_scoring=EMPTY),     # -> project_context
    _payload(_SKELETON_FIELDS, relevant_materials=EMPTY,
             relevant_content_reqs=EMPTY, relevant_scoring=EMPTY,
             project_context=EMPTY),                                   # else -> sibling
    {name: SHORT for name in _SKELETON_FIELDS},                        # 全部不超长
    {name: EMPTY for name in _SKELETON_FIELDS},                        # 全空 -> else 收缩空串
])
def test_parity_skeleton_enrich(payload: dict) -> None:
    _assert_parity(_golden_skeleton_enrich, _FACTORY_SKELETON_ENRICH, payload)


# ---------------------------------------------------------------------------
# 2. outline_generator 分卷填充（4 字段，末位 min_lines=2）
# ---------------------------------------------------------------------------

_VOLUME_FIELDS = [
    "relevant_materials", "relevant_content_reqs", "relevant_scoring",
    "industry_profile",
]


@pytest.mark.parametrize("payload", [
    _payload(_VOLUME_FIELDS),
    _payload(_VOLUME_FIELDS, relevant_materials=EMPTY),
    _payload(_VOLUME_FIELDS, relevant_materials=EMPTY,
             relevant_content_reqs=EMPTY, relevant_scoring=EMPTY),     # else -> min_lines=2
    {name: SHORT for name in _VOLUME_FIELDS},
    {name: EMPTY for name in _VOLUME_FIELDS},
])
def test_parity_volume_fill(payload: dict) -> None:
    _assert_parity(_golden_volume_fill, _FACTORY_VOLUME_FILL, payload)


# ---------------------------------------------------------------------------
# 3. outline_generator unified（4 字段，.get 风格，容忍缺失字段）
# ---------------------------------------------------------------------------

_UNIFIED_FIELDS = [
    "material_requirements", "content_requirements", "scoring_criteria",
    "industry_profile",
]


@pytest.mark.parametrize("payload", [
    _payload(_UNIFIED_FIELDS),
    _payload(_UNIFIED_FIELDS, material_requirements=EMPTY),
    _payload(_UNIFIED_FIELDS, material_requirements=...,
             content_requirements=...),                                # 缺失字段 -> scoring
    {name: SHORT for name in _UNIFIED_FIELDS},
    {name: EMPTY for name in _UNIFIED_FIELDS},
])
def test_parity_unified(payload: dict) -> None:
    _assert_parity(_golden_unified, _FACTORY_UNIFIED, payload)


# ---------------------------------------------------------------------------
# 4. outline_generator 章节画像（kept_segments 含闭包变量 volume_label）
# ---------------------------------------------------------------------------

_PROFILE_FIELDS = [
    "industry_profile_excerpt", "clause_examples_summary",
    "tree_text", "leaf_list",
]


@pytest.mark.parametrize("volume_label", ["技术标", "默认分卷"])
@pytest.mark.parametrize("payload", [
    _payload(_PROFILE_FIELDS),
    _payload(_PROFILE_FIELDS, industry_profile_excerpt=EMPTY),
    _payload(_PROFILE_FIELDS, industry_profile_excerpt=...,
             clause_examples_summary=...),                             # 缺失前两键 -> tree_text
    _payload(_PROFILE_FIELDS, industry_profile_excerpt=EMPTY,
             clause_examples_summary=EMPTY, tree_text=EMPTY),          # else -> leaf_list
    {name: SHORT for name in _PROFILE_FIELDS},
    {name: EMPTY for name in _PROFILE_FIELDS},
])
def test_parity_chapter_profile(payload: dict, volume_label: str) -> None:
    _assert_parity(
        _make_golden_chapter_profile(volume_label),
        _make_factory_chapter_profile(volume_label),
        payload,
    )


# ---------------------------------------------------------------------------
# 5. clause_extractor 结构分析（5 字段，.get 风格）
# ---------------------------------------------------------------------------

_BID_STRUCTURE_FIELDS = [
    "material_requirements", "content_requirements", "scoring_criteria",
    "hard_structure_signals", "industry_profile",
]


@pytest.mark.parametrize("payload", [
    _payload(_BID_STRUCTURE_FIELDS),
    _payload(_BID_STRUCTURE_FIELDS, material_requirements=EMPTY,
             content_requirements=EMPTY),
    _payload(_BID_STRUCTURE_FIELDS, material_requirements=...,
             content_requirements=..., scoring_criteria=...),          # 缺失 -> hard_structure
    _payload(_BID_STRUCTURE_FIELDS, material_requirements=EMPTY,
             content_requirements=EMPTY, scoring_criteria=EMPTY,
             hard_structure_signals=EMPTY),                            # else -> min_lines=2
    {name: SHORT for name in _BID_STRUCTURE_FIELDS},
    {name: EMPTY for name in _BID_STRUCTURE_FIELDS},
])
def test_parity_bid_structure(payload: dict) -> None:
    _assert_parity(_golden_bid_structure, _FACTORY_BID_STRUCTURE, payload)


# ---------------------------------------------------------------------------
# 6. composer free_writing（previous_summaries 前置分支 + str 包装）
# ---------------------------------------------------------------------------

_COMPOSE_FIELDS = [
    "materials_text", "clauses_text", "chapter_profile_text", "profile_excerpt",
]

_SUMMARIES_5 = [{"title": f"章节{i}", "summary": f"摘要{i}" * 10} for i in range(5)]
_SUMMARIES_3 = _SUMMARIES_5[:3]


@pytest.mark.parametrize("payload", [
    _payload(_COMPOSE_FIELDS, previous_summaries=_SUMMARIES_5),        # 列表分支短路
    _payload(_COMPOSE_FIELDS, previous_summaries=_SUMMARIES_3),        # 列表<=3 -> 字段链
    _payload(_COMPOSE_FIELDS),                                         # 缺失列表键 -> 字段链
    _payload(_COMPOSE_FIELDS, previous_summaries=_SUMMARIES_3,
             materials_text=EMPTY),                                    # -> clauses_text
    _payload(_COMPOSE_FIELDS, previous_summaries=[],
             materials_text=EMPTY, clauses_text=EMPTY,
             chapter_profile_text=EMPTY),                              # else -> profile_excerpt
    {**{name: SHORT for name in _COMPOSE_FIELDS},
     "previous_summaries": _SUMMARIES_3},                              # 全部不超长
    {**{name: EMPTY for name in _COMPOSE_FIELDS},
     "previous_summaries": []},                                        # 全空
])
def test_parity_free_writing(payload: dict) -> None:
    _assert_parity(_golden_free_writing, _FACTORY_FREE_WRITING, payload)


# ---------------------------------------------------------------------------
# 7. composer data_fill（str 包装，无前置分支）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", [
    _payload(_COMPOSE_FIELDS),
    _payload(_COMPOSE_FIELDS, materials_text=EMPTY),
    _payload(_COMPOSE_FIELDS, materials_text=..., clauses_text=...),   # 缺失 -> chapter_profile
    _payload(_COMPOSE_FIELDS, materials_text=EMPTY, clauses_text=EMPTY,
             chapter_profile_text=EMPTY),                              # else
    {name: SHORT for name in _COMPOSE_FIELDS},
    {name: EMPTY for name in _COMPOSE_FIELDS},
])
def test_parity_data_fill(payload: dict) -> None:
    _assert_parity(_golden_data_fill, _FACTORY_DATA_FILL, payload)
