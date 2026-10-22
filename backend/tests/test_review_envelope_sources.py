"""审查结果信封来源标注防御测试。

形态: 证据页码必须绑定所在文档才能跳转/展示正确(a/522 同形态)。
夹具含: 商务标+技术标(本案例)、第一/第二信封(同形态第二例)、整本回归。
"""

from app.services.review_envelope_sources import attach_envelope_sources


def _item(pages: list[int]) -> dict:
    return {"source_page_numbers": pages}


def test_split_envelope_pages_bind_to_hit_document() -> None:
    item = attach_envelope_sources(
        _item([3, 12]),
        source_document_id=11,
        retrieval_chunks=[
            {"page_num": 3, "document_id": 11},
            {"page_num": 12, "document_id": 12},
        ],
        part_by_document={11: "commercial", 12: "technical"},
    )

    assert item["source_document_id"] == 11
    assert item["source_parts"] == ["commercial", "technical"]
    assert item["source_page_locations"] == [
        {"document_id": 11, "part": "commercial", "page": 3},
        {"document_id": 12, "part": "technical", "page": 12},
    ]


def test_same_page_number_in_both_volumes_yields_two_locations() -> None:
    """商务/技术同页号 P5: 必须产出两条定位, 不得串档。"""
    item = attach_envelope_sources(
        _item([5]),
        source_document_id=11,
        retrieval_chunks=[
            {"page_num": 5, "document_id": 11},
            {"page_num": 5, "document_id": 12},
        ],
        part_by_document={11: "commercial", 12: "technical"},
    )

    assert item["source_page_locations"] == [
        {"document_id": 11, "part": "commercial", "page": 5},
        {"document_id": 12, "part": "technical", "page": 5},
    ]


def test_page_without_hit_falls_back_to_source_document() -> None:
    item = attach_envelope_sources(
        _item([7]),
        source_document_id=12,
        retrieval_chunks=[],
        part_by_document={11: "commercial", 12: "technical"},
    )

    assert item["source_page_locations"] == [
        {"document_id": 12, "part": "technical", "page": 7},
    ]
    assert item["source_parts"] == ["technical"]


def test_integral_envelope_emits_no_volume_fields() -> None:
    """整本信封: 不产出分卷定位, 消费方回退单文档路径(存量零变化)。"""
    item = attach_envelope_sources(
        _item([3, 5]),
        source_document_id=21,
        retrieval_chunks=[{"page_num": 3, "document_id": 21}],
        part_by_document={21: None},
    )

    assert item["source_document_id"] == 21
    assert item["source_parts"] == []
    assert item["source_page_locations"] == []


def test_second_morphology_parts_covered() -> None:
    """同形态第二例: 第一/第二信封卷角色同一机制产出定位。"""
    item = attach_envelope_sources(
        _item([9]),
        source_document_id=31,
        retrieval_chunks=[{"page_num": 9, "document_id": 32}],
        part_by_document={31: "first_envelope", 32: "second_envelope"},
    )

    assert item["source_page_locations"] == [
        {"document_id": 32, "part": "second_envelope", "page": 9},
    ]
    assert item["source_parts"] == ["second_envelope"]


def test_hits_outside_envelope_are_ignored() -> None:
    item = attach_envelope_sources(
        _item([4]),
        source_document_id=11,
        retrieval_chunks=[{"page_num": 4, "document_id": 999}],
        part_by_document={11: "commercial", 12: "technical"},
    )

    # 999 不在信封内, 回退 source_document_id
    assert item["source_page_locations"] == [
        {"document_id": 11, "part": "commercial", "page": 4},
    ]


def test_no_source_pages_yields_empty_locations() -> None:
    item = attach_envelope_sources(
        _item([]),
        source_document_id=None,
        retrieval_chunks=None,
        part_by_document={11: "commercial", 12: "technical"},
    )

    assert item["source_parts"] == []
    assert item["source_page_locations"] == []
