"""页级实绘抽图闸：资源列表不可单独构成提取集，身份绑定不得全量解码。

夹具1: listed≈painted（原生 PDF 形态）提取集不变
夹具2: listed≫painted（共享资源泄漏，印章/图章实体）只取 painted
夹具3: painted 空且 listed 巨大 → 不解码
夹具4: painted 空且 listed 很小 → 回退旧行为
夹具5: 廉价绘制块用唯一宽高对回 xref，禁止 xrefs=True
夹具6: 同尺寸多资源无法唯一绑定时不回退成全量解码
"""

from unittest.mock import MagicMock

from app.core.parser.image_extractor import ImageExtractor
from app.core.parser.page_image_paint_gate import (
    PAINTED_EMPTY_FALLBACK_MAX,
    resolve_painted_identities,
    select_page_image_xrefs,
)


def test_listed_equals_painted_keeps_same_set() -> None:
    listed = [10, 20]
    painted = {10, 20}
    assert select_page_image_xrefs(listed, painted) == {10, 20}


def test_shared_resource_leak_keeps_only_painted_stamp() -> None:
    """同形态第二例: 5 个资源图章/印章，仅 1 个有绘制 bbox。"""
    listed = [101, 102, 103, 104, 105]
    painted = {103}
    assert select_page_image_xrefs(listed, painted) == {103}


def test_empty_paint_and_huge_listed_extracts_none() -> None:
    listed = list(range(1, 21))
    assert select_page_image_xrefs(listed, set()) == set()


def test_empty_paint_and_small_listed_keeps_listed() -> None:
    listed = [7, 8]
    assert len(listed) <= PAINTED_EMPTY_FALLBACK_MAX
    assert select_page_image_xrefs(listed, set()) == {7, 8}


def test_duplicate_listed_xrefs_deduped() -> None:
    assert select_page_image_xrefs([4, 4, 5, 5], {4}) == {4}


def test_painted_outside_listed_ignored() -> None:
    assert select_page_image_xrefs([1, 2], {9}) == set()


def test_unresolved_paint_instances_do_not_fallback_to_listed() -> None:
    """看见绘制实例但身份无法唯一绑定：禁止退回整份资源列表。"""
    assert select_page_image_xrefs([1, 2], set(), painted_instance_count=1) == set()


def _listed_item(xref: int, width: int, height: int) -> tuple:
    return (xref, 0, width, height, 8, "DeviceRGB", "", f"Im{xref}", "DCTDecode", 0)


def test_resolve_painted_identities_by_unique_size() -> None:
    listed = [
        _listed_item(101, 64, 64),
        _listed_item(102, 80, 90),
        _listed_item(103, 572, 408),
        _listed_item(104, 120, 40),
        _listed_item(105, 200, 80),
    ]
    cheap = [{"width": 572, "height": 408, "bbox": (20.0, 20.0, 90.0, 90.0)}]
    assert resolve_painted_identities(listed, cheap) == {
        103: (20.0, 20.0, 90.0, 90.0)
    }


def test_resolve_painted_identities_skips_ambiguous_size() -> None:
    listed = [_listed_item(1, 64, 64), _listed_item(2, 64, 64)]
    cheap = [{"width": 64, "height": 64, "bbox": (0.0, 0.0, 10.0, 10.0)}]
    assert resolve_painted_identities(listed, cheap) == {}


def test_resolve_painted_identities_skips_missing_bbox() -> None:
    listed = [_listed_item(3, 10, 10)]
    cheap = [{"width": 10, "height": 10, "bbox": None}]
    assert resolve_painted_identities(listed, cheap) == {}


def _listed_images(xrefs: list[int]) -> list[tuple]:
    return [_listed_item(xref, 100 + xref, 80 + xref) for xref in xrefs]


def _forbid_xref_attach(image_info: list[dict]):
    def get_image_info(*args, **kwargs):
        if kwargs.get("xrefs"):
            raise AssertionError("get_image_info(xrefs=True) 会全量解码资源字典")
        return image_info

    return get_image_info


def _page_mock(listed: list[int], image_info: list[dict]) -> MagicMock:
    page = MagicMock()
    page.get_images.return_value = _listed_images(listed)
    page.get_image_info.side_effect = _forbid_xref_attach(image_info)
    page.get_text.return_value = []
    return page


def _doc_mock() -> MagicMock:
    doc = MagicMock()
    doc.extract_image.side_effect = lambda xref: {
        "width": 200,
        "height": 200,
        "ext": "png",
        "image": b"x" * 200,
        "smask": 0,
    }
    return doc


def _cheap_block(xref: int, bbox: tuple) -> dict:
    return {
        "width": 100 + xref,
        "height": 80 + xref,
        "bbox": bbox,
    }


def test_extract_from_page_native_listed_equals_painted() -> None:
    extractor = ImageExtractor()
    page = _page_mock(
        [10, 20],
        [
            _cheap_block(10, (10, 10, 80, 80)),
            _cheap_block(20, (90, 90, 160, 160)),
        ],
    )
    doc = _doc_mock()

    images = extractor.extract_from_page(doc, page, page_num=1)

    assert [img.xref for img in images] == [10, 20]
    assert [img.bbox for img in images] == [(10, 10, 80, 80), (90, 90, 160, 160)]
    assert doc.extract_image.call_count == 2


def test_extract_from_page_does_not_decode_unpainted_resources() -> None:
    extractor = ImageExtractor()
    page = _page_mock(
        [101, 102, 103, 104, 105],
        [_cheap_block(103, (20, 20, 90, 90))],
    )
    doc = _doc_mock()

    images = extractor.extract_from_page(doc, page, page_num=2)

    assert [img.xref for img in images] == [103]
    doc.extract_image.assert_called_once_with(103)


def test_extract_from_page_skips_decode_on_empty_paint_huge_listed() -> None:
    extractor = ImageExtractor()
    listed = list(range(1, 12))
    page = _page_mock(listed, [])
    doc = _doc_mock()

    images = extractor.extract_from_page(doc, page, page_num=3)

    assert images == []
    doc.extract_image.assert_not_called()


def test_extract_from_page_fallback_when_paint_empty_and_listed_small() -> None:
    extractor = ImageExtractor()
    page = _page_mock([7, 8], [])
    doc = _doc_mock()

    images = extractor.extract_from_page(doc, page, page_num=4)

    assert [img.xref for img in images] == [7, 8]
    assert doc.extract_image.call_count == 2


def test_extract_from_page_rejects_xref_attach_api() -> None:
    extractor = ImageExtractor()
    page = _page_mock([10], [_cheap_block(10, (1, 1, 2, 2))])
    extractor.extract_from_page(_doc_mock(), page, page_num=5)
    page.get_image_info.assert_called()
    for call in page.get_image_info.call_args_list:
        assert call.kwargs.get("xrefs") is not True
