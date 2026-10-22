"""投标信封契约防御测试。

形态: 一个逻辑信封由 1..N 份物理文件构成, 齐套/冲突/归因按档案判定。
夹具含: 整本、商务标+技术标(本案例)、第一/第二信封(同形态第二例)。
"""

from types import SimpleNamespace

import pytest

from app.core.bid_envelope import (
    BID_PART_LABELS,
    BidEnvelope,
    envelope_is_complete,
    envelope_mode_conflict,
    format_page_locations,
    parse_bid_part,
)

# 同形态第二例: 两阶段招标的第一/第二信封档案(禁止只测商务/技术实体词)
SECOND_MORPH_PROFILES = {
    "integral": (None,),
    "two_envelope": ("first_envelope", "second_envelope"),
}


def _doc(doc_id: int, bid_part=None, parsed: int = 1) -> SimpleNamespace:
    return SimpleNamespace(id=doc_id, bid_part=bid_part, parsed=parsed)


def _hit(document_id: int, similarity: float, page_num: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        document_id=document_id, similarity=similarity, page_num=page_num
    )


# ---------------------------------------------------------------- 齐套判定


def test_integral_single_doc_is_complete() -> None:
    assert envelope_is_complete([None]) is True


def test_commercial_technical_pair_is_complete() -> None:
    assert envelope_is_complete(["commercial", "technical"]) is True
    assert envelope_is_complete(["technical", "commercial"]) is True


def test_commercial_only_is_incomplete() -> None:
    assert envelope_is_complete(["commercial"]) is False


def test_duplicate_part_is_incomplete() -> None:
    assert envelope_is_complete(["commercial", "commercial"]) is False


def test_two_integral_docs_are_incomplete() -> None:
    assert envelope_is_complete([None, None]) is False


def test_empty_envelope_is_incomplete() -> None:
    assert envelope_is_complete([]) is False


def test_mixed_integral_and_part_is_incomplete() -> None:
    assert envelope_is_complete([None, "commercial"]) is False


def test_second_morphology_pair_is_complete() -> None:
    """同形态第二例: 第一/第二信封档案同一机制齐套。"""
    assert envelope_is_complete(
        ["first_envelope", "second_envelope"], profiles=SECOND_MORPH_PROFILES
    ) is True
    assert envelope_is_complete(
        ["first_envelope"], profiles=SECOND_MORPH_PROFILES
    ) is False


# ------------------------------------------------------------ 模式冲突判定


def test_conflict_integral_then_named_part() -> None:
    assert envelope_mode_conflict([None], "commercial") is True


def test_conflict_named_part_then_integral() -> None:
    assert envelope_mode_conflict(["commercial"], None) is True


def test_same_named_part_reupload_is_not_conflict() -> None:
    """同卷重传走替换路径, 不算模式冲突。"""
    assert envelope_mode_conflict(["commercial"], "commercial") is False


def test_sibling_part_in_same_profile_is_not_conflict() -> None:
    assert envelope_mode_conflict(["commercial"], "technical") is False


def test_empty_envelope_never_conflicts() -> None:
    assert envelope_mode_conflict([], None) is False
    assert envelope_mode_conflict([], "technical") is False


def test_cross_profile_named_parts_conflict() -> None:
    """同形态第二例: 商务标与第二信封跨档案混装 → 冲突。"""
    profiles = {
        "integral": (None,),
        "commercial_technical": ("commercial", "technical"),
        "two_envelope": ("first_envelope", "second_envelope"),
    }
    assert envelope_mode_conflict(
        ["commercial"], "second_envelope", profiles=profiles
    ) is True


# ------------------------------------------------------------- 入参解析


def test_parse_bid_part_valid_values() -> None:
    assert parse_bid_part("commercial") == "commercial"
    assert parse_bid_part(" Technical ") == "technical"


def test_parse_bid_part_empty_means_integral() -> None:
    assert parse_bid_part(None) is None
    assert parse_bid_part("") is None
    assert parse_bid_part("   ") is None


def test_parse_bid_part_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        parse_bid_part("bogus")


def test_parse_bid_part_second_morphology_profile() -> None:
    assert (
        parse_bid_part("first_envelope", profiles=SECOND_MORPH_PROFILES)
        == "first_envelope"
    )


# ----------------------------------------------------------- BidEnvelope


def test_envelope_snapshot_properties() -> None:
    envelope = BidEnvelope(
        documents=(_doc(11, "commercial"), _doc(12, "technical"))
    )
    assert envelope.has_documents is True
    assert envelope.document_ids == (11, 12)
    assert envelope.parts == ("commercial", "technical")
    assert envelope.is_complete is True
    assert envelope.all_parsed is True
    assert envelope.primary_document_id == 11
    assert envelope.part_of(12) == "technical"
    assert envelope.part_of(999) is None
    assert envelope.part_by_document() == {11: "commercial", 12: "technical"}


def test_envelope_all_parsed_requires_every_document() -> None:
    envelope = BidEnvelope(
        documents=(_doc(11, "commercial", parsed=1), _doc(12, "technical", parsed=0))
    )
    assert envelope.is_complete is True
    assert envelope.all_parsed is False


def test_empty_envelope_defaults() -> None:
    envelope = BidEnvelope(documents=())
    assert envelope.has_documents is False
    assert envelope.is_complete is False
    assert envelope.all_parsed is False
    assert envelope.primary_document_id is None
    assert envelope.document_id_for([]) is None


def test_document_id_for_picks_highest_similarity_hit() -> None:
    envelope = BidEnvelope(
        documents=(_doc(11, "commercial"), _doc(12, "technical"))
    )
    hits = [_hit(11, 0.42), _hit(12, 0.87), _hit(999, 0.99)]
    assert envelope.document_id_for(hits) == 12


def test_document_id_for_falls_back_to_primary() -> None:
    envelope = BidEnvelope(documents=(_doc(21),))
    assert envelope.document_id_for([_hit(999, 0.9)]) == 21
    assert envelope.document_id_for([]) == 21


# ------------------------------------------------------- 页码定位格式化


def test_format_page_locations_split_groups_by_part() -> None:
    locations = [
        {"document_id": 11, "part": "commercial", "page": 3},
        {"document_id": 11, "part": "commercial", "page": 5},
        {"document_id": 12, "part": "technical", "page": 12},
    ]
    assert format_page_locations(locations) == "商务标 P3、P5；技术标 P12"


def test_format_page_locations_integral_has_no_label() -> None:
    locations = [
        {"document_id": 11, "part": None, "page": 3},
        {"document_id": 11, "part": None, "page": 5},
    ]
    assert format_page_locations(locations) == "P3、P5"


def test_format_page_locations_unknown_part_renders_raw_token() -> None:
    """同形态第二例: 未登记标签的卷角色按原 token 渲染, 不阻断。"""
    locations = [{"document_id": 31, "part": "first_envelope", "page": 7}]
    assert "first_envelope" not in BID_PART_LABELS
    assert format_page_locations(locations) == "first_envelope P7"


def test_format_page_locations_falls_back_when_empty() -> None:
    assert format_page_locations(None, fallback="3,5") == "3,5"
    assert format_page_locations([], fallback=None) == "-"
    assert format_page_locations([{"part": None, "page": None}]) == "-"


def test_format_page_locations_dedupes_pages_within_part() -> None:
    locations = [
        {"document_id": 11, "part": "technical", "page": 4},
        {"document_id": 11, "part": "technical", "page": 4},
    ]
    assert format_page_locations(locations) == "技术标 P4"
