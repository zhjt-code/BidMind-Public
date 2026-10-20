"""Task-2 RED: scope trace 与证据包测试。

职责: 验证 build_scoring_chunk_scope_trace/build_scoring_scope_evidence 的 scope 隔离、
      预算 fail-closed、元数据完整性与日志无原文。
不做: 不测试 LLM、数据库、API 路由或真实提取流程。
"""

from __future__ import annotations

import os
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.scoring.scoring_full_candidate_context import (  # noqa: E402
    ScoringScopeEvidenceBundle,
    ScoringScopeEvidenceRequest,
    ScoringScopeEvidenceUnit,
    ScoringChunkScopeTrace,
    build_scoring_chunk_scope_trace,
    build_scoring_scope_evidence,
)
from app.core.extractor.scoring.scoring_grid_payload import build_scoring_payload  # noqa: E402
from app.core.extractor.scoring.scoring_scope_contracts import ScopeIdentity  # noqa: E402

pytestmark = pytest.mark.scoring


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------

_TARGET_SCOPE: ScopeIdentity = ("__single__", "__single__")
_OTHER_SCOPE: ScopeIdentity = ("__single__", "lot_2")

_SCORING_TABLE_MD = (
    "| 评分项 | 分值 | 评分标准 |\n"
    "|---|---|---|\n"
    "| 业绩 | 20 | 近3年2项 |\n"
    "| 荣誉 | 10 | 省级奖项 |\n"
)


def _make_payload(
    lot: str | None = None,
    page_start: int = 3,
    page_end: int = 4,
    source: str = "assembled",
) -> dict:
    return build_scoring_payload(
        _SCORING_TABLE_MD,
        lot=lot,
        page_start=page_start,
        page_end=page_end,
        source=source,
    )


def _chunk(
    chunk_id: int,
    content: str,
    *,
    chunk_type: str = "text",
    page_num: int = 1,
    parent_block_id: str | None = None,
    document_id: int = 1,
    chunk_index: int = 0,
    payload: dict | None = None,
) -> SimpleNamespace:
    meta = {}
    if payload:
        from app.core.extractor.scoring.scoring_grid_payload import PAYLOAD_KEY
        meta[PAYLOAD_KEY] = payload
    return SimpleNamespace(
        id=chunk_id,
        content=content,
        chunk_type=chunk_type,
        page_num=page_num,
        parent_block_id=parent_block_id,
        document_id=document_id,
        chunk_index=chunk_index,
        chunk_metadata=meta if meta else None,
    )


def _scoring_chunk(
    chunk_id: int,
    content: str = _SCORING_TABLE_MD,
    *,
    lot: str | None = None,
    page_start: int = 3,
    page_end: int = 4,
) -> SimpleNamespace:
    payload = _make_payload(lot=lot, page_start=page_start, page_end=page_end)
    return _chunk(
        chunk_id,
        content,
        chunk_type="scoring_table",
        page_num=page_start,
        payload=payload,
    )


def _make_trace(
    chunk_id: int,
    scope: ScopeIdentity = _TARGET_SCOPE,
    *,
    page_start: int = 3,
    page_end: int = 4,
    chunk_type: str = "scoring_table",
    document_id: int = 1,
) -> ScoringChunkScopeTrace:
    return ScoringChunkScopeTrace(
        chunk_id=chunk_id,
        document_id=document_id,
        chunk_index=0,
        chunk_type=chunk_type,
        scope=scope,
        page_start=page_start,
        page_end=page_end,
        parent_block_id=None,
        table_fingerprint="abcd1234" * 4,
        source="assembled",
    )


def _make_request(
    scoring_chunks: list | None = None,
    context_chunks: list | None = None,
    traces: dict | None = None,
    current_items: tuple = (),
    diagnostics: tuple = (),
    budget: int = 50000,
    neighbor_limit: int = 2,
    target_scope: ScopeIdentity = _TARGET_SCOPE,
) -> ScoringScopeEvidenceRequest:
    sc = scoring_chunks or []
    cc = context_chunks or []
    tr = types.MappingProxyType(traces or {})
    return ScoringScopeEvidenceRequest(
        target_scope=target_scope,
        scoring_chunks=tuple(sc),
        context_chunks=tuple(cc),
        traces=tr,
        current_items=current_items,
        diagnostics=diagnostics,
        context_char_budget=budget,
        neighbor_limit=neighbor_limit,
    )


# ---------------------------------------------------------------------------
# A. ScoringChunkScopeTrace 与 build_scoring_chunk_scope_trace
# ---------------------------------------------------------------------------


class TestBuildScoringChunkScopeTrace:
    def test_reads_page_range_from_payload(self) -> None:
        chunk = _scoring_chunk(chunk_id=10, page_start=3, page_end=5)
        trace = build_scoring_chunk_scope_trace(chunk, _TARGET_SCOPE)
        assert trace.page_start == 3
        assert trace.page_end == 5

    def test_reads_source_from_payload(self) -> None:
        chunk = _scoring_chunk(chunk_id=11)
        trace = build_scoring_chunk_scope_trace(chunk, _TARGET_SCOPE)
        assert trace.source == "assembled"

    def test_reads_fingerprint_from_payload(self) -> None:
        chunk = _scoring_chunk(chunk_id=12)
        trace = build_scoring_chunk_scope_trace(chunk, _TARGET_SCOPE)
        assert trace.table_fingerprint is not None
        assert len(trace.table_fingerprint) > 0

    def test_lot_conflict_raises(self) -> None:
        # payload.lot="lot_X" but target scope lot="lot_Y"
        payload = _make_payload(lot="lot_X")
        chunk = _chunk(99, _SCORING_TABLE_MD, chunk_type="scoring_table", page_num=3, payload=payload)
        with pytest.raises(ValueError, match="[Ll]ot"):
            build_scoring_chunk_scope_trace(chunk, ("__single__", "lot_Y"))

    def test_unassigned_scope_raises(self) -> None:
        chunk = _scoring_chunk(chunk_id=20)
        with pytest.raises(ValueError):
            build_scoring_chunk_scope_trace(chunk, ("__single__", "__unassigned__"))

    def test_no_chunk_id_raises(self) -> None:
        chunk = SimpleNamespace(id=0, content="test", chunk_type="text", page_num=5)
        with pytest.raises(ValueError):
            build_scoring_chunk_scope_trace(chunk, _TARGET_SCOPE)

    def test_no_valid_page_raises(self) -> None:
        chunk = SimpleNamespace(id=5, content="test", chunk_type="text", page_num=0, chunk_metadata=None)
        with pytest.raises(ValueError):
            build_scoring_chunk_scope_trace(chunk, _TARGET_SCOPE)

    def test_fallback_to_chunk_page_num(self) -> None:
        # No payload → use chunk.page_num
        chunk = _chunk(30, "评分文本", page_num=7)
        trace = build_scoring_chunk_scope_trace(chunk, _TARGET_SCOPE)
        assert trace.page_start == 7

    def test_scheme_from_caller_not_guessed(self) -> None:
        chunk = _scoring_chunk(chunk_id=40)
        trace = build_scoring_chunk_scope_trace(chunk, ("scheme_B", "__single__"))
        assert trace.scope[0] == "scheme_B"


# ---------------------------------------------------------------------------
# B. ScoringScopeEvidenceUnit.render_for_prompt
# ---------------------------------------------------------------------------


class TestScoringScopeEvidenceUnit:
    def test_render_includes_metadata_header(self) -> None:
        unit = ScoringScopeEvidenceUnit(
            kind="target_table",
            chunk_id=1,
            page_start=3,
            page_end=4,
            parent_block_id="blk_01",
            table_fingerprint="fp123",
            source="assembled",
            scope=_TARGET_SCOPE,
            content="| 业绩 | 20 |",
        )
        rendered = unit.render_for_prompt()
        assert "chunk_id" in rendered or "1" in rendered
        assert "3" in rendered  # page info
        assert "| 业绩 | 20 |" in rendered  # original content preserved

    def test_render_includes_parent_block(self) -> None:
        unit = ScoringScopeEvidenceUnit(
            kind="neighbor",
            chunk_id=5,
            page_start=2,
            page_end=2,
            parent_block_id="block_A",
            table_fingerprint=None,
            source="assembled",
            scope=_TARGET_SCOPE,
            content="评分说明文本",
        )
        rendered = unit.render_for_prompt()
        assert "block_A" in rendered or "parent" in rendered.lower()

    def test_content_verbatim_in_render(self) -> None:
        content = "满分100分，技术评分占60分"
        unit = ScoringScopeEvidenceUnit(
            kind="score_declaration",
            chunk_id=2,
            page_start=6,
            page_end=6,
            parent_block_id=None,
            table_fingerprint=None,
            source="assembled",
            scope=_TARGET_SCOPE,
            content=content,
        )
        rendered = unit.render_for_prompt()
        assert content in rendered


# ---------------------------------------------------------------------------
# C. build_scoring_scope_evidence — 核心行为
# ---------------------------------------------------------------------------


class TestBuildScoringEvidence:
    def test_target_table_content_verbatim(self) -> None:
        sc = _scoring_chunk(chunk_id=1)
        trace = _make_trace(1)
        req = _make_request(scoring_chunks=[sc], traces={1: trace})
        bundle = build_scoring_scope_evidence(req)
        assert any(u.content == _SCORING_TABLE_MD for u in bundle.units)

    def test_cross_scope_chunk_excluded(self) -> None:
        sc_target = _scoring_chunk(chunk_id=1)
        sc_other = _scoring_chunk(chunk_id=2)
        trace_target = _make_trace(1, _TARGET_SCOPE)
        trace_other = _make_trace(2, _OTHER_SCOPE)
        req = _make_request(
            scoring_chunks=[sc_target, sc_other],
            traces={1: trace_target, 2: trace_other},
        )
        bundle = build_scoring_scope_evidence(req)
        chunk_ids = [u.chunk_id for u in bundle.units]
        assert 1 in chunk_ids
        assert 2 not in chunk_ids

    def test_no_trace_chunk_excluded(self) -> None:
        sc_with_trace = _scoring_chunk(chunk_id=1)
        sc_no_trace = _scoring_chunk(chunk_id=99)  # no trace
        trace = _make_trace(1)
        req = _make_request(
            scoring_chunks=[sc_with_trace, sc_no_trace],
            traces={1: trace},
        )
        bundle = build_scoring_scope_evidence(req)
        chunk_ids = [u.chunk_id for u in bundle.units]
        assert 99 not in chunk_ids

    def test_overflow_when_table_plus_reserved_exceeds_budget(self) -> None:
        # 大内容 + 少量保留预算 → overflow
        big_content = "X" * 1000
        sc = _scoring_chunk(chunk_id=1, content=big_content)
        trace = _make_trace(1)
        reserved_item = "ITEM" * 500  # 2000 chars in diagnostics
        req = _make_request(
            scoring_chunks=[sc],
            traces={1: trace},
            diagnostics=(reserved_item,),
            budget=200,  # 远小于 1000 content chars + reserved
        )
        bundle = build_scoring_scope_evidence(req)
        assert bundle.context_overflow is True

    def test_overflow_preserves_full_target_table(self) -> None:
        big_content = "Y" * 1000
        sc = _scoring_chunk(chunk_id=1, content=big_content)
        trace = _make_trace(1)
        optional_chunk = _chunk(2, "可选内容", page_num=5)
        trace_opt = _make_trace(2, _TARGET_SCOPE, chunk_type="text")
        req = _make_request(
            scoring_chunks=[sc],
            context_chunks=[optional_chunk],
            traces={1: trace, 2: trace_opt},
            budget=500,  # overflow scenario
        )
        bundle = build_scoring_scope_evidence(req)
        # 目标表必须完整保留
        assert any(u.content == big_content for u in bundle.units)

    def test_overflow_excludes_optional_units(self) -> None:
        big_content = "Z" * 1000
        sc = _scoring_chunk(chunk_id=1, content=big_content)
        trace = _make_trace(1)
        optional = _chunk(2, "可选邻近文本", page_num=5)
        trace_opt = _make_trace(2, _TARGET_SCOPE, chunk_type="text")
        req = _make_request(
            scoring_chunks=[sc],
            context_chunks=[optional],
            traces={1: trace, 2: trace_opt},
            budget=500,
        )
        bundle = build_scoring_scope_evidence(req)
        assert bundle.context_overflow is True
        # 可选单位不得装入
        assert 2 not in [u.chunk_id for u in bundle.units]

    def test_overflow_diagnostics_no_original_text(self) -> None:
        # diagnostics 只记录计数/字符数/chunk id，不记录原文
        marker = "SECRET_ORIGINAL_CONTENT_DO_NOT_LOG"
        sc = _scoring_chunk(chunk_id=1, content=marker * 100)
        trace = _make_trace(1)
        req = _make_request(
            scoring_chunks=[sc],
            traces={1: trace},
            budget=50,  # very small → overflow
        )
        bundle = build_scoring_scope_evidence(req)
        assert bundle.context_overflow is True
        for diag in bundle.diagnostics:
            assert marker not in diag

    def test_duplicate_chunk_id_deduped(self) -> None:
        sc = _scoring_chunk(chunk_id=1)
        trace = _make_trace(1)
        req = _make_request(
            scoring_chunks=[sc, sc],  # same chunk twice
            traces={1: trace},
        )
        bundle = build_scoring_scope_evidence(req)
        chunk_ids = [u.chunk_id for u in bundle.units]
        assert chunk_ids.count(1) == 1

    def test_duplicate_content_deduped(self) -> None:
        sc1 = _scoring_chunk(chunk_id=1)
        sc2 = _scoring_chunk(chunk_id=2, content=_SCORING_TABLE_MD)  # same content
        trace1 = _make_trace(1)
        trace2 = _make_trace(2)
        req = _make_request(
            scoring_chunks=[sc1, sc2],
            traces={1: trace1, 2: trace2},
        )
        bundle = build_scoring_scope_evidence(req)
        # 内容重复只保留一个
        contents = [u.content for u in bundle.units]
        assert contents.count(_SCORING_TABLE_MD) == 1

    def test_each_unit_has_metadata(self) -> None:
        sc = _scoring_chunk(chunk_id=1)
        trace = _make_trace(1, page_start=3, page_end=4)
        req = _make_request(scoring_chunks=[sc], traces={1: trace})
        bundle = build_scoring_scope_evidence(req)
        for unit in bundle.units:
            assert unit.chunk_id > 0
            assert unit.page_start > 0
            assert unit.scope == _TARGET_SCOPE

    def test_bundle_has_target_tables(self) -> None:
        sc = _scoring_chunk(chunk_id=1)
        trace = _make_trace(1)
        req = _make_request(scoring_chunks=[sc], traces={1: trace})
        bundle = build_scoring_scope_evidence(req)
        assert bundle.has_target_tables is True

    def test_bundle_no_target_tables_when_empty(self) -> None:
        req = _make_request()  # no chunks
        bundle = build_scoring_scope_evidence(req)
        assert bundle.has_target_tables is False

    def test_prompt_context_contains_rendered_units(self) -> None:
        sc = _scoring_chunk(chunk_id=1, content="业绩评分满分20分")
        trace = _make_trace(1)
        req = _make_request(scoring_chunks=[sc], traces={1: trace})
        bundle = build_scoring_scope_evidence(req)
        assert "业绩评分满分20分" in bundle.prompt_context

    def test_neighbor_limit_respected(self) -> None:
        # 4 个邻近 chunk，limit=1 → 最多 2 个 neighbor (N before + N after)
        sc = _scoring_chunk(chunk_id=1)
        trace = _make_trace(1)
        neighbors = [_chunk(i + 10, f"邻近文本{i}", page_num=i) for i in range(4)]
        neighbor_traces = {
            i + 10: _make_trace(i + 10, _TARGET_SCOPE, chunk_type="text", page_start=i)
            for i in range(4)
        }
        traces = {1: trace, **neighbor_traces}
        req = _make_request(
            scoring_chunks=[sc],
            context_chunks=neighbors,
            traces=traces,
            budget=100000,
            neighbor_limit=1,
        )
        bundle = build_scoring_scope_evidence(req)
        neighbor_ids = [u.chunk_id for u in bundle.units if u.kind == "neighbor"]
        assert len(neighbor_ids) <= 2  # N before + N after, N=1

    def test_reserved_chars_counted_from_current_items_and_diagnostics(self) -> None:
        sc = _scoring_chunk(chunk_id=1)
        trace = _make_trace(1)
        long_diag = "D" * 500
        req = _make_request(
            scoring_chunks=[sc],
            traces={1: trace},
            diagnostics=(long_diag,),
            budget=50000,
        )
        bundle = build_scoring_scope_evidence(req)
        assert bundle.reserved_char_count > 0

    def test_omitted_chunk_ids_recorded_on_overflow(self) -> None:
        big_content = "W" * 1000
        sc = _scoring_chunk(chunk_id=1, content=big_content)
        trace = _make_trace(1)
        opt = _chunk(2, "可选内容", page_num=5)
        trace_opt = _make_trace(2, _TARGET_SCOPE, chunk_type="text")
        req = _make_request(
            scoring_chunks=[sc],
            context_chunks=[opt],
            traces={1: trace, 2: trace_opt},
            budget=500,
        )
        bundle = build_scoring_scope_evidence(req)
        assert 2 in bundle.omitted_chunk_ids

    def test_target_scope_on_bundle(self) -> None:
        req = _make_request(target_scope=("scheme_A", "lot_1"))
        bundle = build_scoring_scope_evidence(req)
        assert bundle.target_scope == ("scheme_A", "lot_1")
