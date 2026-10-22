"""评分候选项印刷页/相对页 → chunk 物理页重映射。

覆盖: 目录印刷页均匀偏移、附件相对页 1..N、已是物理页不改、缺页不编造、
 均匀偏移失败时就近落入物理页集合、从 chunk 载荷收集物理页、
 救援轮次引擎在 scope 回填前完成换算。
不做: 不调 LLM、不访问 DB、不解析 toc_map。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.extractor.scoring.physical_page_remap import (
    collect_chunk_physical_pages,
    remap_item_source_pages,
)
from app.schemas.requirement import ExtractedScoringItem

pytestmark = [pytest.mark.scoring]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _item(page: int | None, *, name: str = "技术方案") -> ExtractedScoringItem:
    pages = [page] if page is not None else []
    return ExtractedScoringItem(
        category="TECH",
        score_item_name=name,
        description=f"{name}评分标准",
        score_weight=10.0,
        source_pages=pages,
    )


def _chunk(*, page_num: int | None = None, payload: dict | None = None) -> SimpleNamespace:
    meta = {"scoring_payload": payload} if payload is not None else None
    return SimpleNamespace(page_num=page_num, chunk_metadata=meta, metadata=None)


class TestCollectChunkPhysicalPages:
    def test_unions_payload_range_row_pages_and_page_num(self) -> None:
        chunk = _chunk(
            page_num=48,
            payload={
                "page_start": 48,
                "page_end": 51,
                "row_pages": [None, 48, 49, 50, 51],
            },
        )
        assert collect_chunk_physical_pages([chunk]) == (48, 49, 50, 51)

    def test_falls_back_to_page_num_when_payload_missing(self) -> None:
        assert collect_chunk_physical_pages([_chunk(page_num=12)]) == (12,)

    def test_ignores_non_positive_and_empty_chunks(self) -> None:
        assert collect_chunk_physical_pages([]) == ()
        assert collect_chunk_physical_pages([_chunk(page_num=0)]) == ()


class TestRemapItemSourcePages:
    def test_printed_toc_pages_shift_by_uniform_offset(self) -> None:
        """项目 3426 形态: 模型抄目录印刷页 42-45, chunk 物理页 48-51。"""
        items = [_item(p) for p in (42, 42, 43, 43, 43, 44, 44, 44, 45, 45)]
        remapped = remap_item_source_pages(items, (48, 49, 50, 51))
        assert [it.source_pages for it in remapped] == [
            [48], [48], [49], [49], [49], [50], [50], [50], [51], [51],
        ]

    def test_relative_attachment_pages_map_onto_physical_span(self) -> None:
        items = [_item(p) for p in (1, 2, 3, 4)]
        remapped = remap_item_source_pages(items, (48, 49, 50, 51))
        assert [it.source_pages[0] for it in remapped] == [48, 49, 50, 51]

    def test_already_physical_pages_unchanged(self) -> None:
        items = [_item(48), _item(51)]
        remapped = remap_item_source_pages(items, (48, 49, 50, 51))
        assert [it.source_pages for it in remapped] == [[48], [51]]
        assert remapped[0] is items[0]
        assert remapped[1] is items[1]

    def test_missing_pages_are_left_empty(self) -> None:
        items = [_item(None)]
        remapped = remap_item_source_pages(items, (48, 49, 50, 51))
        assert remapped[0].source_pages == []

    def test_mixed_pages_snap_outliers_to_nearest_physical(self) -> None:
        """均匀偏移会把已是物理页的 48 推出集合, 改为逐项就近。"""
        items = [_item(42), _item(48)]
        remapped = remap_item_source_pages(items, (48, 49, 50, 51))
        assert remapped[0].source_pages == [48]
        assert remapped[1].source_pages == [48]


class TestRescueRoundsRemapWiring:
    @pytest.mark.anyio
    async def test_adopted_items_use_chunk_physical_page(self, monkeypatch) -> None:
        """救援采纳出口必须把印刷页换成 chunk 物理页, 不能把 P42 落库。"""
        from app.core.extractor.scoring.scoring_rescue_integration import (
            ScoringRescueIntegrationResult,
        )
        from app.core.extractor.scoring.vlm.rescue_rounds import (
            RescueRoundsRequest,
            run_candidate_rescue_rounds,
        )

        async def _fake_judge(**kwargs):
            return ("green", None)

        monkeypatch.setattr(
            "app.core.extractor.scoring.vlm.rescue_rounds._judge_candidate_verdict",
            _fake_judge,
        )

        printed_item = ExtractedScoringItem(
            category="TECH",
            score_item_name="技术方案",
            description="技术方案评分标准",
            score_weight=40.0,
            source_pages=[42],
        )
        price_item = ExtractedScoringItem(
            category="COMM",
            score_item_name="价格评审",
            description="价格评审评分标准",
            score_weight=60.0,
            source_pages=[45],
        )

        async def _extract(_feedback: str):
            return [printed_item, price_item], {"declared_total": 100}

        chunk = _chunk(
            page_num=48,
            payload={"page_start": 48, "page_end": 51, "grid": [["技术方案"]]},
        )
        tail = ScoringRescueIntegrationResult(
            items=[],
            report=SimpleNamespace(
                needs_review=True, total=0.0, expected_total=100.0, note="待复核"
            ),
            fidelity_sidecar={},
            diagnostics=[],
            l3_diagnostics=[],
            adopted=False,
            mode="enforce",
        )
        request = RescueRoundsRequest(
            tail=tail,
            db=object(),
            document_id=1,
            scoring_chunks=[chunk],
            volume_defs={},
            anchor_text="技术方案40分价格评审60分总分100分",
            max_rounds=1,
            meta={},
        )
        result = await run_candidate_rescue_rounds(request, _extract)
        assert result is not None
        pages = sorted(
            {p for it in result.items for p in (it.source_pages or [])}
        )
        assert pages == [48, 51]
        assert 42 not in pages
        assert 45 not in pages
