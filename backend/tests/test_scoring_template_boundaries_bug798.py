""" Task 5 — 2502 模板边界 TDD 测试。

职责: 锁定模板边界检测 → 装配 scope 切分 → payload scheme_boundary_id →
 build_scoring_table_chunks p24/p25-p26 独立 chunk 的完整修复链路。
不做: 不测 LLM、不测 DB 持久化、不修改 fixture。

RED → GREEN 路径:
- test_detect_*: detect_template_boundaries 不存在 → RED (ImportError); 实现后 GREEN。
- test_split_*: split_sections_by_template_boundaries 不存在 → RED; 实现后 GREEN。
- test_payload_*: build_scoring_payload 无 scheme_boundary_id → RED; 修改后 GREEN。
- test_segmenter_*: assign_scheme_for_chunk 无 scheme_boundary_id 参数 → RED; 改后 GREEN。
- test_build_*: build_scoring_table_chunks 产出1个 p24-p26 大 chunk → RED; 修复后 GREEN。
"""
from __future__ import annotations
from tests.synthetic_data import synthetic_text

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.scoring

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-testing-only-not-real")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")
sys.path.insert(0, str(Path(__file__).parent.parent))

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "scoring" / "bug798"


def _load_2502() -> dict[str, Any]:
    path = _FIXTURE_DIR / "project_2502_templates.json"
    assert path.exists(), f"Fixture 不存在: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 辅助：从 fixture 构建 ParsedDocument
# ---------------------------------------------------------------------------


def _build_parsed_doc_from_fixture(data: dict[str, Any]):
    """从 fixture 的 physical_pages 构造 ParsedDocument。"""
    from app.core.parser.pdf_parser import ExtractedTable, ParsedDocument, ParsedPage

    pages = []
    for pp in data.get("physical_pages", []):
        tables = []
        for t in pp.get("tables", []):
            rows = [list(r) for r in t.get("rows", [])]
            bbox = tuple(t.get("bbox", [0, 0, 0, 0]))
            tables.append(
                ExtractedTable(
                    table_index=t.get("table_index", 0),
                    page_num=pp["page_num"],
                    data=rows,
                    row_count=t.get("row_count", len(rows)),
                    col_count=t.get("col_count", 3),
                    bbox=bbox,
                )
            )
        pages.append(
            ParsedPage(
                page_num=pp["page_num"],
                text=pp.get("text", ""),
                tables=tables,
            )
        )
    return ParsedDocument(
        file_path="test_2502.pdf",
        filename="验证评分表结构-11.pdf",
        page_count=76,
        pages=pages,
    )


def _build_planner():
    from app.core.parser.chunk_planner import ChunkPlanner

    return ChunkPlanner()


# ===========================================================================
# Part 1: detect_template_boundaries — 单元测试
# ===========================================================================


class TestDetectTemplateBoundaries:
    """scoring_template_boundaries.detect_template_boundaries 单元测试。"""

    def test_detects_p24_and_p25_from_2502_fixture(self):
        """正例：fixture p24/p25 分别含 JS-GCKYSJ01 / JS-GCSJ01 → 2 个边界。"""
        from app.core.parser.scoring_template_boundaries import detect_template_boundaries

        data = _load_2502()
        page_texts = [
            (pp["page_num"], pp["text"]) for pp in data["physical_pages"]
        ]
        boundaries = detect_template_boundaries(page_texts)

        codes = {b.template_code for b in boundaries}
        pages = {b.page_num for b in boundaries}

        assert len(boundaries) >= 2, (
            f"期望>=2个模板边界，实际: {boundaries}"
        )
        assert "JS-GCKYSJ01" in codes, f"p24 编码 JS-GCKYSJ01 未检出，codes={codes}"
        assert "JS-GCSJ01" in codes, f"p25 编码 JS-GCSJ01 未检出，codes={codes}"
        assert 24 in pages, f"p24 未被识别为边界页，pages={pages}"
        assert 25 in pages, f"p25 未被识别为边界页，pages={pages}"

    def test_negative_single_code_no_boundary(self):
        """负例：全文仅1个不同编码 → 空列表。"""
        from app.core.parser.scoring_template_boundaries import detect_template_boundaries

        page_texts = [
            (1, "1.FWJS05:输变电工程施工服务技术详评模板V1.0\n评审要素 评审内容"),
        ]
        assert detect_template_boundaries(page_texts) == []

    def test_negative_no_template_context(self):
        """负例：有编码但同行无"模板/细则" → 空列表。"""
        from app.core.parser.scoring_template_boundaries import detect_template_boundaries

        page_texts = [
            (1, "ABC01:设备型号一\n技术参数说明"),
            (2, "ABC02:设备型号二\n技术参数说明"),
        ]
        assert detect_template_boundaries(page_texts) == []

    def test_negative_pure_letter_codes(self):
        """负例：TEL/ESG 纯字母（无数字后缀）→ 空列表。"""
        from app.core.parser.scoring_template_boundaries import detect_template_boundaries

        page_texts = [
            (1, synthetic_text('TEL:', '010-', '1234', '5678', ' 本细则适用于本项目模板。')),
            (2, synthetic_text('FAX:', '010-', '8765', '4321', ' 本细则适用于本项目模板。')),
        ]
        assert detect_template_boundaries(page_texts) == []

    def test_negative_cmmi_cisp_prose(self):
        """负例：CMMI/CISP 在正文句中出现 → 空列表。"""
        from app.core.parser.scoring_template_boundaries import detect_template_boundaries

        page_texts = [
            (1, "企业资质 具有SPCA或CMMI或CSMM软件能力成熟度等级证书,五级得3分。"),
            (2, "CCSC网络安全能力认证证书或注册信息安全专业人员(CISP)中任意一个证书得1分。"),
        ]
        assert detect_template_boundaries(page_texts) == []

    def test_sorted_by_page_num(self):
        """边界按 page_num 升序排列。"""
        from app.core.parser.scoring_template_boundaries import detect_template_boundaries

        page_texts = [
            (25, "3.JS-GCSJ01:输变电工程工程设计服务技术详评模板\n评审要素"),
            (24, "2.JS-GCKYSJ01:输变电工程工程可研设计服务技术详评模板\n评审要素"),
        ]
        result = detect_template_boundaries(page_texts)
        pages = [b.page_num for b in result]
        assert pages == sorted(pages), f"边界应按页码升序，实际: {pages}"


# ===========================================================================
# Part 2: split_sections_by_template_boundaries — 单元测试
# ===========================================================================


class TestSplitSectionsByTemplateBoundaries:
    """scoring_assembly_scopes.split_sections_by_template_boundaries 单元测试。"""

    def _make_section(self, page_start: int, page_end: int, title: str = "评分表"):
        from app.core.parser.scoring_section_locator import ScoringSection

        return ScoringSection(
            title=title, page_start=page_start, page_end=page_end, detection="test"
        )

    def _make_boundary(self, page_num: int, code: str):
        from app.core.parser.scoring_template_boundaries import TemplateBoundary

        return TemplateBoundary(page_num=page_num, template_code=code)

    def test_splits_section_at_boundary_page(self):
        """p22-p26 的大区段在 p25 处切成 p22-p24 和 p25-p26。"""
        from app.core.parser.scoring_assembly_scopes import split_sections_by_template_boundaries

        sections = [self._make_section(22, 26)]
        boundaries = [
            self._make_boundary(22, "JS-GCKYSJ01"),
            self._make_boundary(25, "JS-GCSJ01"),
        ]
        result = split_sections_by_template_boundaries(sections, boundaries)

        page_ranges = [(s.page_start, s.page_end) for s in result]
        assert (22, 24) in page_ranges, f"期望 p22-p24 段，实际: {page_ranges}"
        assert (25, 26) in page_ranges, f"期望 p25-p26 段，实际: {page_ranges}"

    def test_no_boundary_returns_original(self):
        """无边界时原样返回。"""
        from app.core.parser.scoring_assembly_scopes import split_sections_by_template_boundaries

        sections = [self._make_section(24, 26)]
        result = split_sections_by_template_boundaries(sections, [])
        assert [(s.page_start, s.page_end) for s in result] == [(24, 26)]

    def test_boundary_outside_section_ignored(self):
        """边界在区段范围外 → 不切分。"""
        from app.core.parser.scoring_assembly_scopes import split_sections_by_template_boundaries

        sections = [self._make_section(24, 26)]
        boundaries = [self._make_boundary(30, "FWJS01")]
        result = split_sections_by_template_boundaries(sections, boundaries)
        assert len(result) == 1

    def test_boundary_on_first_page_of_section_no_empty_slice(self):
        """边界恰在区段首页 → 不产出空片段，原区段保留完整。"""
        from app.core.parser.scoring_assembly_scopes import split_sections_by_template_boundaries

        sections = [self._make_section(24, 26)]
        boundaries = [self._make_boundary(24, "JS-GCKYSJ01")]
        result = split_sections_by_template_boundaries(sections, boundaries)
        assert all(s.page_start <= s.page_end for s in result), "不应产出 start>end 的空片段"


# ===========================================================================
# Part 3: payload scheme_boundary_id — build_scoring_payload 单元测试
# ===========================================================================


class TestPayloadSchemeBoundaryId:
    """scoring_grid_payload.build_scoring_payload 含 scheme_boundary_id 字段。"""

    def test_payload_includes_scheme_boundary_id_when_provided(self):
        """M1 修订: scheme_boundary_id 通过 set_payload_scope_metadata 后置写入。"""
        from app.core.extractor.scoring.scoring_grid_payload import (
            build_scoring_payload,
            set_payload_scope_metadata,
        )

        md = "| 项目 | 分值 |\n| --- | --- |\n| A | 10 |"
        payload = build_scoring_payload(md, page_start=24, page_end=24)
        assert payload is not None
        # 后置写入 scheme_boundary_id（M1 新 API）
        set_payload_scope_metadata(payload, scheme_boundary_id="tb:JS-GCKYSJ01")
        assert payload.get("scheme_boundary_id") == "tb:JS-GCKYSJ01"

    def test_payload_without_scheme_boundary_id_defaults_none(self):
        """未传时 scheme_boundary_id 字段不存在或为 None。"""
        from app.core.extractor.scoring.scoring_grid_payload import build_scoring_payload

        md = "| 项目 | 分值 |\n| --- | --- |\n| A | 10 |"
        payload = build_scoring_payload(md, page_start=1, page_end=1)
        assert payload is not None
        assert payload.get("scheme_boundary_id") is None


# ===========================================================================
# Part 4: SchemeSegmenter 结构边界推进 — 单元测试
# ===========================================================================


def _item(score: float, scheme: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(score_weight=score, scheme=scheme, volume="technical")


class TestSchemeSegmenterStructuralBoundary:
    """assign_scheme_for_chunk 接受 scheme_boundary_id 参数；新边界即推进方案。"""

    def test_new_boundary_id_advances_scheme_even_if_unclosed(self):
        """关键：有可信结构边界时，即使前方案未闭合（总分≠100）也应推进。"""
        from app.core.extractor.scoring.scheme_segmentation import SchemeSegmenter

        seg = SchemeSegmenter()
        # 先注册方案1（未闭合，50分）
        survivors_after_first = [_item(50.0, scheme="scheme_1")]
        seg.assign_scheme_for_chunk(
            chunk_fingerprint="fp-a",
            incoming_item_count=5,
            current_survivors=[],
            volume_defs={},
            scheme_boundary_id="tb:JS-GCKYSJ01@p24",
        )
        # 新的不同边界到来 → 即使前方案未闭合，也推进到 scheme_2
        second = seg.assign_scheme_for_chunk(
            chunk_fingerprint="fp-b",
            incoming_item_count=5,
            current_survivors=survivors_after_first,
            volume_defs={},
            scheme_boundary_id="tb:JS-GCSJ01@p25",
        )
        assert second == "scheme_2", (
            f"有新可信边界时应立即推进到 scheme_2，实际: {second}"
        )

    def test_same_boundary_id_does_not_advance(self):
        """相同边界 ID 不触发新方案（同模板内多个 chunk）。"""
        from app.core.extractor.scoring.scheme_segmentation import SchemeSegmenter

        seg = SchemeSegmenter()
        seg.assign_scheme_for_chunk(
            chunk_fingerprint="fp-a",
            incoming_item_count=5,
            current_survivors=[],
            volume_defs={},
            scheme_boundary_id="tb:JS-GCKYSJ01@p24",
        )
        survivors = [_item(60.0, scheme="scheme_1")]
        second = seg.assign_scheme_for_chunk(
            chunk_fingerprint="fp-b",
            incoming_item_count=5,
            current_survivors=survivors,
            volume_defs={},
            scheme_boundary_id="tb:JS-GCKYSJ01@p24",
        )
        assert second == "scheme_1", f"相同边界不应推进方案，实际: {second}"

    def test_none_boundary_falls_back_to_closure_logic(self):
        """boundary_id=None 时保持旧逻辑（未闭合不切分）。"""
        from app.core.extractor.scoring.scheme_segmentation import SchemeSegmenter

        seg = SchemeSegmenter()
        seg.assign_scheme_for_chunk(
            chunk_fingerprint="fp-1",
            incoming_item_count=5,
            current_survivors=[],
            volume_defs={},
            scheme_boundary_id=None,
        )
        survivors = [_item(20.0, scheme="scheme_1")]
        second = seg.assign_scheme_for_chunk(
            chunk_fingerprint="fp-2",
            incoming_item_count=5,
            current_survivors=survivors,
            volume_defs={},
            scheme_boundary_id=None,
        )
        assert second == "scheme_1", f"无边界时未闭合不应推进，实际: {second}"

    def test_existing_closure_logic_preserved_without_boundary(self):
        """无 scheme_boundary_id 时，旧闭合逻辑仍正确推进（回归）。"""
        from app.core.extractor.scoring.scheme_segmentation import SchemeSegmenter

        seg = SchemeSegmenter()
        seg.assign_scheme_for_chunk(
            chunk_fingerprint="fp-a",
            incoming_item_count=10,
            current_survivors=[],
            volume_defs={},
        )
        survivors = [_item(100.0, scheme="scheme_1")]
        second = seg.assign_scheme_for_chunk(
            chunk_fingerprint="fp-b",
            incoming_item_count=5,
            current_survivors=survivors,
            volume_defs={},
        )
        assert second == "scheme_2", f"旧闭合逻辑：闭合后新表应推进，实际: {second}"


# ===========================================================================
# Part 5: build_scoring_table_chunks 真实重放 — 集成测试
# ===========================================================================


class TestBuildScoringTableChunksP2502:
    """关键集成测试：2502 fixture 重放，验证 p24 与 p25-p26 分属不同 chunk/scope。"""

    def _get_chunks(self):
        data = _load_2502()
        parsed_doc = _build_parsed_doc_from_fixture(data)
        planner = _build_planner()

        from app.core.parser.scoring_chunk_builder import build_scoring_table_chunks

        return build_scoring_table_chunks(
            parsed_doc=parsed_doc,
            planner=planner,
            start_index=0,
        )

    @staticmethod
    def _chunk_page_num(c) -> int | None:
        """从 metadata 取 page_num（TextChunk 不直接暴露此属性）。"""
        return c.metadata.get("page_num")

    def test_produces_at_least_two_chunks(self):
        """GREEN: 修复后产出 >=2 个 chunk（p24 独立，p25-p26 独立）。

        RED: 修复前 stitch_cross_page_tables 将 p24-p26 合并为1个大 chunk。
        """
        chunks = self._get_chunks()
        assert len(chunks) >= 2, (
            f"期望 >=2 个 scoring chunk（p24 与 p25-p26 分离），"
            f"实际: {len(chunks)} 个"
            + (
                f"，唯一 chunk 页范围 p{chunks[0].metadata.get('page_end')})"
                if len(chunks) == 1 else ""
            )
        )

    def test_p24_chunk_does_not_span_p25(self):
        """GREEN: p24 的 chunk 页尾不超过 p24（不跨入 p25）。"""
        chunks = self._get_chunks()
        p24_chunks = [c for c in chunks if self._chunk_page_num(c) == 24]
        if not p24_chunks:
            pytest.skip("没有起始于 p24 的 chunk，无法验证")
        for c in p24_chunks:
            page_end = c.metadata.get("page_end", self._chunk_page_num(c))
            assert page_end <= 24, (
                f"p24 chunk 页尾应 <=24，实际 page_end={page_end}（仍横跨 p25）"
            )

    def test_p25_chunk_exists_and_separated_from_p24(self):
        """GREEN: 存在起始于 p25 的 chunk（p25 内容已从 p24 chunk 分离）。"""
        chunks = self._get_chunks()
        pnums = [self._chunk_page_num(c) for c in chunks]
        p25_chunks = [c for c in chunks if self._chunk_page_num(c) == 25]
        assert p25_chunks, (
            f"期望存在起始于 p25 的 chunk，但未找到 —— chunk 页码列表: {pnums}"
        )

    def test_p24_chunk_payload_has_boundary_id(self):
        """GREEN: p24 chunk 的 payload 含 scheme_boundary_id，区分于 p25 chunk。"""
        from app.core.extractor.scoring.scoring_grid_payload import PAYLOAD_KEY

        chunks = self._get_chunks()
        p24_chunks = [c for c in chunks if self._chunk_page_num(c) == 24]
        p25_chunks = [c for c in chunks if self._chunk_page_num(c) == 25]

        if not (p24_chunks and p25_chunks):
            pytest.skip("chunk 未正确分离，无法验证 boundary_id")

        bid_p24 = p24_chunks[0].metadata.get(PAYLOAD_KEY, {}).get("scheme_boundary_id")
        bid_p25 = p25_chunks[0].metadata.get(PAYLOAD_KEY, {}).get("scheme_boundary_id")

        assert bid_p24 is not None, "p24 chunk payload 缺 scheme_boundary_id"
        assert bid_p25 is not None, "p25 chunk payload 缺 scheme_boundary_id"
        assert bid_p24 != bid_p25, (
            f"p24/p25 的 scheme_boundary_id 应不同，实际相同: {bid_p24}"
        )


# ===========================================================================
# Part 6: C1 — assembled 路径（不只是 stitched fallback）
# ===========================================================================


class TestAssembledPath:
    """C1: 2502 fixture 回放 — 验证 p24 与 p25 的 boundary_id 不同（assembly_spans 未被合回）。

    注: 2502 fixture 表头 ['项目','评审要素','评审内容'] 不含分值列，is_scoring_header_row
    返回 False，assembled 路径零产出为预期行为；assembled=True 的主路径由
    TestAssembledPathSynthetic 用合成可识别表头覆盖。
    """

    def _get_chunks(self):
        data = _load_2502()
        parsed_doc = _build_parsed_doc_from_fixture(data)
        planner = _build_planner()
        from app.core.parser.scoring_chunk_builder import build_scoring_table_chunks
        return build_scoring_table_chunks(parsed_doc=parsed_doc, planner=planner, start_index=0)

    def test_p24_p25_chunks_correctly_split_via_assembly_spans(self):
        """C1 GREEN: 2502 fixture p24/p25 产出 chunk 的 boundary_id 不同，
        证明 assembly_spans 已按模板边界切分，未被 _resolve_assembly_spans 合回。
        """
        from app.core.extractor.scoring.scoring_grid_payload import PAYLOAD_KEY

        chunks = self._get_chunks()
        p24_chunks = [c for c in chunks if c.metadata.get("page_num") == 24]
        p25_chunks = [c for c in chunks if c.metadata.get("page_num") == 25]

        assert len(p24_chunks) >= 1, "p24 chunk 应存在"
        assert len(p25_chunks) >= 1, "p25 chunk 应存在"

        # boundary_id 不同 → 来自不同的 assembly_spans（C1 核心约束）
        bid24 = p24_chunks[0].metadata.get(PAYLOAD_KEY, {}).get("scheme_boundary_id")
        bid25 = p25_chunks[0].metadata.get(PAYLOAD_KEY, {}).get("scheme_boundary_id")
        assert bid24 != bid25, (
            f"p24/p25 boundary_id 应不同（assembly_spans 未被合回），"
            f"bid24={bid24}, bid25={bid25}"
        )


# ===========================================================================
# Part 7: C2 — 稳定 boundary_id（不含页码）
# ===========================================================================


class TestStableBoundaryId:
    """C2: boundary_id = 'tb:{code}'，不含页码；连续页同一 code 不产新边界。"""

    def test_boundary_id_has_no_page_number(self):
        """C2: 边界 ID 格式为 tb:{code}，不含 @p{page} 后缀（使用 boundary_id_for_page 单源，B5）。"""
        from app.core.parser.scoring_template_boundaries import (
            detect_template_boundaries,
            boundary_id_for_page,
        )

        page_texts = [
            (24, "2.JS-GCKYSJ01:输变电工程工程可研设计服务技术详评模板\n评审要素"),
            (25, "3.JS-GCSJ01:输变电工程工程设计服务技术详评模板\n评审要素"),
        ]
        boundaries = detect_template_boundaries(page_texts)
        assert boundaries, "应检出至少1个边界"
        for b in boundaries:
            bid = boundary_id_for_page(boundaries, b.page_num)
            assert bid is not None, f"boundary_id_for_page 不应为 None"
            assert "@p" not in bid, (
                f"boundary_id 不应含页码，实际: {bid}"
            )
            assert bid == f"tb:{b.template_code}", (
                f"期望 tb:{{code}} 格式，实际: {bid}"
            )

    def test_consecutive_same_code_no_extra_boundary(self):
        """C2: 连续页 p24/p25 相同 code 只产 1 个边界（不重复）。"""
        from app.core.parser.scoring_template_boundaries import detect_template_boundaries

        page_texts = [
            (24, "2.JS-GCKYSJ01:输变电工程工程可研设计服务技术详评模板\n评审要素"),
            (25, "2.JS-GCKYSJ01:输变电工程工程可研设计服务技术详评模板（续）\n评审要素"),
            (30, "3.JS-GCSJ01:输变电工程工程设计服务技术详评模板\n评审要素"),
        ]
        boundaries = detect_template_boundaries(page_texts)
        codes = [b.template_code for b in boundaries]
        assert codes.count("JS-GCKYSJ01") == 1, (
            f"连续相同 code 只应记录 1 次，实际: {codes}"
        )

    def test_2502_chunk_boundary_ids_are_stable_no_page(self):
        """C2 集成: 2502 fixture 产出的 chunk boundary_id 不含页码后缀。"""
        from app.core.extractor.scoring.scoring_grid_payload import PAYLOAD_KEY

        data = _load_2502()
        parsed_doc = _build_parsed_doc_from_fixture(data)
        planner = _build_planner()
        from app.core.parser.scoring_chunk_builder import build_scoring_table_chunks
        chunks = build_scoring_table_chunks(parsed_doc=parsed_doc, planner=planner, start_index=0)

        for c in chunks:
            bid = c.metadata.get(PAYLOAD_KEY, {}).get("scheme_boundary_id")
            if bid is not None:
                assert "@p" not in bid, (
                    f"chunk boundary_id 不应含页码，实际: {bid}"
                )


# ===========================================================================
# Part 8: C3 — SchemeSegmenter A→B→A 复用 scheme_1
# ===========================================================================


class TestSchemeSegmenterRevisit:
    """C3: boundary→scheme 映射；A→B→A 回访复用 scheme_1，不新建 scheme_3。"""

    def test_a_b_a_revisit_reuses_scheme_1(self):
        """C3 RED→GREEN: A→B→A 模式中第三次回访 A 应复用 scheme_1。"""
        from app.core.extractor.scoring.scheme_segmentation import SchemeSegmenter

        seg = SchemeSegmenter()
        survivors: list = []

        # A: 第1次
        s1 = seg.assign_scheme_for_chunk(
            chunk_fingerprint="fp-a1", incoming_item_count=5,
            current_survivors=survivors, volume_defs={},
            scheme_boundary_id="tb:JS-GCKYSJ01",
        )
        assert s1 == "scheme_1"
        survivors = [SimpleNamespace(score_weight=50.0, scheme="scheme_1", volume="technical")]

        # B: 新边界 → 切到 scheme_2
        s2 = seg.assign_scheme_for_chunk(
            chunk_fingerprint="fp-b1", incoming_item_count=5,
            current_survivors=survivors, volume_defs={},
            scheme_boundary_id="tb:JS-GCSJ01",
        )
        assert s2 == "scheme_2"
        survivors.append(SimpleNamespace(score_weight=50.0, scheme="scheme_2", volume="technical"))

        # A: 回访相同边界 ID → 应复用 scheme_1，不应新建 scheme_3
        s3 = seg.assign_scheme_for_chunk(
            chunk_fingerprint="fp-a2", incoming_item_count=5,
            current_survivors=survivors, volume_defs={},
            scheme_boundary_id="tb:JS-GCKYSJ01",
        )
        assert s3 == "scheme_1", (
            f"回访 A 应复用 scheme_1，但实际得到 {s3}（可能错误新建了 scheme_3）"
        )
        assert seg.scheme_count == 2, (
            f"A→B→A 整体只应有 2 个方案，实际 scheme_count={seg.scheme_count}"
        )


# ===========================================================================
# Part 9: C4 — 相同指纹不允许靠边界开新 scheme
# ===========================================================================


class TestFingerprintBeforeBoundary:
    """C4: 结构边界分支前先做指纹副本检查；同指纹 A→B 不得开新 scheme。"""

    def test_same_fingerprint_boundary_change_no_advance(self):
        """C4 RED→GREEN: 同一指纹但不同边界 ID → 视为副本，不推进 scheme。"""
        from app.core.extractor.scoring.scheme_segmentation import SchemeSegmenter

        seg = SchemeSegmenter()
        survivors: list = []

        # 第一个 chunk: boundary=A, fingerprint=FP1
        s1 = seg.assign_scheme_for_chunk(
            chunk_fingerprint="fp-dup", incoming_item_count=5,
            current_survivors=survivors, volume_defs={},
            scheme_boundary_id="tb:JS-GCKYSJ01",
        )
        assert s1 == "scheme_1"
        survivors = [SimpleNamespace(score_weight=80.0, scheme="scheme_1", volume="technical")]

        # 第二个 chunk: boundary=B（新边界），但指纹相同 FP1 → 副本，不应开 scheme_2
        s2 = seg.assign_scheme_for_chunk(
            chunk_fingerprint="fp-dup", incoming_item_count=5,
            current_survivors=survivors, volume_defs={},
            scheme_boundary_id="tb:JS-GCSJ01",
        )
        assert s2 == "scheme_1", (
            f"同指纹即使边界变化也应视为副本，不推进，实际: {s2}"
        )


# ===========================================================================
# Part 10: I1/I2 — 连接符结构编码 + 强制分隔符
# ===========================================================================


class TestRegexImprovements:
    """I1/I2: 编码接受连接符结构（JS-FWTY），必须有分隔符，排除型号。"""

    def test_hyphen_code_without_digit_suffix_matches(self):
        """I1 RED→GREEN: JS-FWTY（无数字后缀，纯连接符结构）应被检出。"""
        from app.core.parser.scoring_template_boundaries import detect_template_boundaries

        page_texts = [
            (1, "附件1 JS-FWTY:输变电工程技术评审细则\n评审要素"),
            (2, "附件2 JS-GCZCB:工程造价工作质量评分模板\n评审要素"),
        ]
        boundaries = detect_template_boundaries(page_texts)
        codes = {b.template_code for b in boundaries}
        assert "JS-FWTY" in codes or "JS-GCZCB" in codes, (
            f"纯连接符结构编码应被检出，实际 codes={codes}"
        )

    def test_no_colon_with_template_keyword_should_match(self):
        """A3: 编码后有空格但无冒号，同行含模板关键词 → 仍应匹配（冒号非必需，Round3 A3）。"""
        from app.core.parser.scoring_template_boundaries import detect_template_boundaries

        page_texts = [
            (1, "2.JS-GCKYSJ01 输变电工程工程可研设计服务技术详评模板\n评审要素"),
            (2, "3.JS-GCSJ01 输变电工程工程设计服务技术详评模板\n评审要素"),
        ]
        boundaries = detect_template_boundaries(page_texts)
        codes = {b.template_code for b in boundaries}
        assert len(boundaries) == 2, (
            f"A3: 含模板关键词时无冒号仍应匹配两页，实际 codes={codes}"
        )

    def test_model_number_excluded(self):
        """I2: PLC-S7300/AB12-CD34 型号负例（有冒号但无模板/细则语境）→ 空列表。"""
        from app.core.parser.scoring_template_boundaries import detect_template_boundaries

        page_texts = [
            (1, "PLC-S7300:西门子 S7300 PLC 设备参数\n型号说明"),
            (2, "AB12-CD34:设备技术规格书\n型号说明"),
        ]
        assert detect_template_boundaries(page_texts) == []

    def test_cmmi_iso_excluded_with_colon(self):
        """I2: CMMI/ISO 负例（纯字母，即使有冒号和'细则'也不匹配）。"""
        from app.core.parser.scoring_template_boundaries import detect_template_boundaries

        page_texts = [
            (1, "CMMI:资质要求细则\n企业获得CMMI五级认证"),
            (2, "ISO:质量管理细则\n通过ISO9001认证"),
        ]
        assert detect_template_boundaries(page_texts) == []

    def test_prefix_supported_fuzhi_prefix(self):
        """I2: 支持 '附件2 JS-FWSW01:...' 合理前导（不只行首数字序号）。"""
        from app.core.parser.scoring_template_boundaries import detect_template_boundaries

        page_texts = [
            (1, "附件2 JS-FWSW01:服务类通用商务详评细则\n评审要素"),
            (3, "附件3 JS-GCZKK:工程管理评分细则\n评审要素"),
        ]
        boundaries = detect_template_boundaries(page_texts)
        codes = {b.template_code for b in boundaries}
        assert len(boundaries) >= 1, (
            f"附件前导格式应被识别，实际 codes={codes}"
        )

    def test_ambiguous_page_multiple_codes_fail_visible(self):
        """I2: 同一页多个不同编码锚点 → 该页不作为切分点（fail-visible，不静默取第一个）。"""
        from app.core.parser.scoring_template_boundaries import detect_template_boundaries

        # 同一页同时出现两个不同 code：既是 p1 的 JS-GCKYSJ01 又是 JS-GCSJ01
        ambiguous_text = (
            "2.JS-GCKYSJ01:输变电工程可研技术详评模板\n"
            "3.JS-GCSJ01:输变电工程设计服务技术详评模板\n"
            "评审要素"
        )
        page_texts = [
            (1, ambiguous_text),
            (5, "4.JS-FWSW01:服务类商务技术详评细则\n评审要素"),
        ]
        boundaries = detect_template_boundaries(page_texts)
        ambiguous_pages = {b.page_num for b in boundaries}
        # 同页多锚点页不应作为切分点
        assert 1 not in ambiguous_pages, (
            f"同页多锚点不应纳入边界，但 p1 被纳入了: boundaries={boundaries}"
        )


# ===========================================================================
# Part 11: I3 — SCORING_TEMPLATE_BOUNDARY_ENABLED 开关
# ===========================================================================


class TestFeatureSwitch:
    """I3: SCORING_TEMPLATE_BOUNDARY_ENABLED=False 禁用模板边界检测。"""

    def test_feature_switch_off_returns_empty(self, monkeypatch):
        """I3 RED→GREEN: 开关关闭时 detect_template_boundaries 返回空列表。"""
        import importlib
        from app.core.parser import scoring_template_boundaries as stb

        # 通过 monkeypatch 模拟 settings.SCORING_TEMPLATE_BOUNDARY_ENABLED = False
        # 用对象属性注入替代 Pydantic setattr（避免 ValidationError）
        class _FakeSettings:
            SCORING_TEMPLATE_BOUNDARY_ENABLED = False
        monkeypatch.setattr(stb, "settings", _FakeSettings())

        page_texts = [
            (24, "2.JS-GCKYSJ01:输变电工程工程可研设计服务技术详评模板\n评审要素"),
            (25, "3.JS-GCSJ01:输变电工程工程设计服务技术详评模板\n评审要素"),
        ]
        result = stb.detect_template_boundaries(page_texts)
        assert result == [], f"开关关闭时应返回空列表，实际: {result}"


# ===========================================================================
# Part 12: M1 — set_payload_scope_metadata
# ===========================================================================


class TestSetPayloadScopeMetadata:
    """M1: scoring_grid_payload 新增 set_payload_scope_metadata；build_scoring_payload 恢复 ≤6 参数。"""

    def test_set_payload_scope_metadata_writes_fields(self):
        """M1 RED→GREEN: set_payload_scope_metadata 后置写入 scheme_boundary_id 和 scheme。"""
        from app.core.extractor.scoring.scoring_grid_payload import set_payload_scope_metadata

        payload: dict = {"version": 1, "grid": [["a", "b"]]}
        set_payload_scope_metadata(payload, scheme_boundary_id="tb:JS-GCKYSJ01", scheme="scheme_1")
        assert payload.get("scheme_boundary_id") == "tb:JS-GCKYSJ01"
        assert payload.get("scheme") == "scheme_1"

    def test_build_scoring_payload_at_most_six_positional_params(self):
        """M1: build_scoring_payload 函数签名不超过 6 个参数（不再把 scheme_boundary_id 作内置参数）。"""
        import inspect
        from app.core.extractor.scoring.scoring_grid_payload import build_scoring_payload

        sig = inspect.signature(build_scoring_payload)
        params = list(sig.parameters.keys())
        # 允许的参数: markdown, lot, page_start, page_end, source, row_pages
        assert len(params) <= 6, (
            f"build_scoring_payload 参数超过 6 个: {params}"
        )


# ===========================================================================
# Part 13: I8 — 最终 scheme 写回 chunk payload
# ===========================================================================


class TestSchemeWrittenToPayload:
    """I8: set_payload_scope_metadata 能把 scheme 写回 chunk payload。"""

    def test_set_payload_scope_metadata_scheme_field(self):
        """I8: 调用后 payload['scheme'] 含正确 scheme 值。"""
        from app.core.extractor.scoring.scoring_grid_payload import set_payload_scope_metadata

        payload: dict = {"version": 1, "grid": []}
        set_payload_scope_metadata(payload, scheme="scheme_2")
        assert payload.get("scheme") == "scheme_2", (
            f"scheme 写回失败，payload={payload}"
        )


# ===========================================================================
# Part 14: C1 — 合成主assembled路径集成测试（is_scoring_header_row 可识别表头）
# ===========================================================================


def _build_two_template_doc():
    """构建两页各有不同模板标题 + is_scoring_header_row 可识别表头的合成 ParsedDocument。

    设计:
    - p10: 标题含 "FWJS05:...模板"，表头 ['评分项目','分值','评分标准'] → is_scoring_header_row=True
    - p11: 标题含 "FWJS06:...模板"，表头相同 → 第二个评分表
    - 两页各有评分行（含具体分数），确保 assemble_scoring_segments 能提取 LogicalScoringSegment
    """
    from app.core.parser.pdf_parser import ExtractedTable, ParsedDocument, ParsedPage

    header = ["评分项目", "分值", "评分标准"]
    rows_p10 = [
        header,
        ["技术能力", "30", "优秀得30分，良好得20分，一般得10分"],
        ["管理能力", "20", "有效组织得20分，一般得10分"],
    ]
    rows_p11 = [
        header,
        ["报价合理", "40", "低于基准价得40分，等于得30分"],
        ["服务响应", "10", "24小时响应得10分，48小时得5分"],
    ]
    t10 = ExtractedTable(
        table_index=0, page_num=10,
        data=rows_p10, row_count=3, col_count=3, bbox=(0, 0, 500, 300),
    )
    t11 = ExtractedTable(
        table_index=0, page_num=11,
        data=rows_p11, row_count=3, col_count=3, bbox=(0, 0, 500, 300),
    )
    pages = [
        ParsedPage(
            page_num=10,
            text="第四章 评分标准\n1.FWJS05:技术方案评分模板\n评审要素内容",
            tables=[t10],
        ),
        ParsedPage(
            page_num=11,
            text="2.FWJS06:商务报价评分模板\n评审要素内容",
            tables=[t11],
        ),
    ]
    return ParsedDocument(
        file_path="synthetic_two_templates.pdf",
        filename="synthetic_two_templates.pdf",
        page_count=12,
        pages=pages,
    )


class TestAssembledPathSynthetic:
    """C1: 合成"主assembled可识别表头"集成测试，证明 assembly_spans 按模板边界切分且未被合回。"""

    def test_two_assembled_chunks_with_different_boundary_ids(self):
        """C1 GREEN: 两页各有不同可信模板标题 + 可识别表头 → 两个 assembled=True 且 boundary_id 不同。"""
        from app.core.extractor.scoring.scoring_grid_payload import PAYLOAD_KEY
        from app.core.parser.chunk_planner import ChunkPlanner
        from app.core.parser.scoring_chunk_builder import build_scoring_table_chunks

        doc = _build_two_template_doc()
        planner = ChunkPlanner()
        chunks = build_scoring_table_chunks(parsed_doc=doc, planner=planner, start_index=0)

        assembled = [c for c in chunks if c.metadata.get("assembled") is True]
        assert len(assembled) == 2, (
            f"期望 2 个 assembled=True chunk，实际 {len(assembled)}；"
            f"chunks={[(c.metadata.get('page_num'), c.metadata.get('assembled')) for c in chunks]}"
        )

        bid0 = assembled[0].metadata.get(PAYLOAD_KEY, {}).get("scheme_boundary_id")
        bid1 = assembled[1].metadata.get(PAYLOAD_KEY, {}).get("scheme_boundary_id")
        assert bid0 is not None, "第1个 assembled chunk 缺 scheme_boundary_id"
        assert bid1 is not None, "第2个 assembled chunk 缺 scheme_boundary_id"
        assert bid0 != bid1, (
            f"两个 assembled chunk 的 boundary_id 应不同（assembly_spans 未被合回），"
            f"bid0={bid0}, bid1={bid1}"
        )

    def test_feature_switch_off_single_assembled_span(self, monkeypatch):
        """C1/I3: 开关 off → 无边界检测 → 两页合并为单一 assembled span → 1 个 assembled chunk。"""
        import app.core.parser.scoring_template_boundaries as stb
        from app.core.extractor.scoring.scoring_grid_payload import PAYLOAD_KEY
        from app.core.parser.chunk_planner import ChunkPlanner
        from app.core.parser.scoring_chunk_builder import build_scoring_table_chunks

        class _FakeSettings:
            SCORING_TEMPLATE_BOUNDARY_ENABLED = False

        monkeypatch.setattr(stb, "settings", _FakeSettings())

        doc = _build_two_template_doc()
        planner = ChunkPlanner()
        chunks = build_scoring_table_chunks(parsed_doc=doc, planner=planner, start_index=0)

        assembled = [c for c in chunks if c.metadata.get("assembled") is True]
        # 开关 off → 边界不检测 → assembly_spans 不切分 → 合并为 1 个 assembled span/chunk
        assert len(assembled) == 1, (
            f"开关 off 时期望 1 个 assembled chunk（两页合并），实际 {len(assembled)}"
        )
        bid = assembled[0].metadata.get(PAYLOAD_KEY, {}).get("scheme_boundary_id")
        assert bid is None, f"开关 off 时 boundary_id 应为 None，实际: {bid}"


# ===========================================================================
# Part 15: I6 — has_multi_template_scheme_signal 委托对齐测试
# ===========================================================================


class TestHasMultiTemplateSchemeSignalI6:
    """I6: has_multi_template_scheme_signal 委托 detect_template_boundaries 的行为对齐测试。"""

    def test_hyphenated_codes_detected(self):
        """I6: 连接符结构编码（JS-FWTY/JS-GCZCB，无数字后缀）→ True（新功能，旧 regex 不支持）。"""
        from app.core.parser.scoring_chunk_sources import has_multi_template_scheme_signal

        page_texts = [
            "附件1 JS-FWTY:输变电工程技术评审细则\n评审要素",
            "附件2 JS-GCZCB:工程造价工作质量评分模板\n评审要素",
        ]
        assert has_multi_template_scheme_signal(page_texts) is True

    def test_model_number_negative(self):
        """I6: 型号负例（PLC-S7300/AB12-CD34 无模板/细则上下文）→ False（新旧一致）。"""
        from app.core.parser.scoring_chunk_sources import has_multi_template_scheme_signal

        page_texts = [
            "PLC-S7300:西门子 S7300 PLC 设备参数\n型号说明",
            "AB12-CD34:设备技术规格书\n型号说明",
        ]
        assert has_multi_template_scheme_signal(page_texts) is False

    def test_same_page_multi_codes_count_for_has_multi(self):
        """A1/I6: 同页出现 2+ 个不同编码锚点 → has_multi 仍返回 True（A1 修订）。

        架构说明（A1）: extract_template_anchors 返回全量锚点（含同页多个），不受开关影响。
        has_multi_template_scheme_signal 看全锚点 distinct code >= 2 → True。
        同页多锚点的页级切分由既有 table_stitcher 序号重启机制处理，本模块不假称页级切分。
        detect_template_boundaries 才跳过歧义页（A2）。
        """
        from app.core.parser.scoring_chunk_sources import has_multi_template_scheme_signal

        # 同一页文本中两个不同编码 → extract_template_anchors 计入全量 → has_multi=True
        page_texts = [
            "1.FWJS05:技术评分模板\n2.FWJS06:商务评分细则\n评审要素",
        ]
        assert has_multi_template_scheme_signal(page_texts) is True

    def test_single_code_returns_false(self):
        """I6: 全文只有一个不同模板编码 → False（新旧一致）。"""
        from app.core.parser.scoring_chunk_sources import has_multi_template_scheme_signal

        page_texts = [
            "1.FWJS05:技术评分模板\n评审要素",
            "1.FWJS05:技术评分模板（续）\n评审要素",
        ]
        assert has_multi_template_scheme_signal(page_texts) is False

    def test_two_different_codes_with_context_returns_true(self):
        """I6: 两页各有不同编码 + 模板上下文 → True（新旧均为 True）。"""
        from app.core.parser.scoring_chunk_sources import has_multi_template_scheme_signal

        page_texts = [
            "1.FWJS05:技术评审详评模板\n评审要素",
            "2.FWSW01:商务服务评审细则\n评审要素",
        ]
        assert has_multi_template_scheme_signal(page_texts) is True


# ===========================================================================
# Part 16: A1 — extract_template_anchors 两阶段同源锚点
# ===========================================================================


class TestExtractTemplateAnchors:
    """A1: extract_template_anchors 返回全量锚点，不受开关影响；A3：新正则。"""

    def test_basic_two_pages(self):
        """A1: 基本两页各有锚点 → 返回正确字典。"""
        from app.core.parser.scoring_template_boundaries import extract_template_anchors

        page_texts = [
            (10, "FWJS16服务技术详评模板(通用)\n评审要素"),
            (11, "FWJS17商务技术详评模板\n评审要素"),
        ]
        result = extract_template_anchors(page_texts)
        assert 10 in result and "FWJS16" in result[10], f"p10 应含 FWJS16: {result}"
        assert 11 in result and "FWJS17" in result[11], f"p11 应含 FWJS17: {result}"

    def test_not_affected_by_feature_switch(self, monkeypatch):
        """A1: SCORING_TEMPLATE_BOUNDARY_ENABLED=False 不影响 extract_template_anchors。"""
        import app.core.parser.scoring_template_boundaries as stb

        class _FakeSettings:
            SCORING_TEMPLATE_BOUNDARY_ENABLED = False

        monkeypatch.setattr(stb, "settings", _FakeSettings())
        page_texts = [
            (1, "FWJS05:技术详评模板\n评审要素"),
            (2, "FWJS06:商务评分细则\n评审要素"),
        ]
        result = stb.extract_template_anchors(page_texts)
        assert len(result) == 2, f"开关 off 不影响 extract_template_anchors，实际: {result}"

    def test_same_page_two_codes_both_returned(self):
        """A2: 同页多锚点在 extract_template_anchors 全量返回（检测层不过滤）。"""
        from app.core.parser.scoring_template_boundaries import extract_template_anchors

        page_texts = [
            (1, "1.FWJS05:技术评分模板\n2.FWJS06:商务评分细则\n评审要素"),
        ]
        result = extract_template_anchors(page_texts)
        assert 1 in result
        codes = result[1]
        assert "FWJS05" in codes and "FWJS06" in codes, (
            f"同页两个锚点应均在全量返回: {codes}"
        )

    def test_has_multi_still_works_switch_off(self, monkeypatch):
        """A1: 开关 off 时 has_multi_template_scheme_signal 仍返回 True（旧表内方案切分不受影响）。"""
        import app.core.parser.scoring_template_boundaries as stb
        from app.core.parser.scoring_chunk_sources import has_multi_template_scheme_signal

        class _FakeSettings:
            SCORING_TEMPLATE_BOUNDARY_ENABLED = False

        monkeypatch.setattr(stb, "settings", _FakeSettings())
        texts = [
            "5.FWJS05:技改大修工程施工服务技术详评模板V1.0\n评审要素",
            "FWJS16服务技术详评模板(通用)V1.0\n评审要素",
        ]
        assert has_multi_template_scheme_signal(texts) is True, (
            "开关 off 不应关闭 has_multi_template_scheme_signal（旧表内方案切分信号）"
        )


# ===========================================================================
# Part 17: A3 — 新正则（无硬性冒号、精确模板词、型号负例）
# ===========================================================================


class TestAnchorRegexA3:
    """A3: 去冒号要求；精确模板语境词；型号负向拒绝。"""

    def test_no_colon_fwjs16_detected(self):
        """A3: 无冒号编码 FWJS16服务技术详评模板 能被检测到（恢复 sample09）。"""
        from app.core.parser.scoring_template_boundaries import extract_template_anchors

        page_texts = [(1, "FWJS16服务技术详评模板(通用)V1.0\n评审要素")]
        result = extract_template_anchors(page_texts)
        assert 1 in result and "FWJS16" in result[1], (
            f"无冒号 FWJS16 应被检测到，实际: {result}"
        )

    def test_js_fwty_hyphenated_pure_detected(self):
        """A3: 纯连接符结构编码（JS-FWTY）+ 评审细则 → 检测到。"""
        from app.core.parser.scoring_template_boundaries import extract_template_anchors

        page_texts = [(1, "附件1 JS-FWTY:输变电工程技术评审细则\n评审要素")]
        result = extract_template_anchors(page_texts)
        assert 1 in result and "JS-FWTY" in result[1], f"JS-FWTY 应被检测到: {result}"

    def test_plc_model_number_without_template_term_rejected(self):
        """A3: PLC-S7300 无模板语境 → 不命中（正向模板词未满足）。"""
        from app.core.parser.scoring_template_boundaries import extract_template_anchors

        page_texts = [
            (1, "PLC-S7300:西门子设备参数\n型号说明"),
            (2, "AB12-CD34:产品型号规格\n技术参数"),
        ]
        result = extract_template_anchors(page_texts)
        all_codes = [c for codes in result.values() for c in codes]
        assert len(all_codes) == 0, f"型号负例不应命中，实际: {result}"

    def test_iso_standard_without_template_term_rejected(self):
        """A3: ISO9001 类标准编号无精确模板词 → 不命中。"""
        from app.core.parser.scoring_template_boundaries import extract_template_anchors

        page_texts = [(1, "ISO9001:质量管理体系认证\n认证说明")]
        result = extract_template_anchors(page_texts)
        assert len(result) == 0 or all(len(v) == 0 for v in result.values()), (
            f"ISO9001 无模板词不应命中: {result}"
        )

    def test_ambiguous_page_skipped_by_detect_but_counted_by_extract(self):
        """A2/A3: 同页多锚点 → detect_template_boundaries 跳过该页，但 extract_template_anchors 保留。"""
        from app.core.parser.scoring_template_boundaries import (
            detect_template_boundaries,
            extract_template_anchors,
        )

        page_texts = [
            (1, "1.FWJS05:技术评审细则\n2.FWJS06:商务评分细则\n评审要素"),  # ambiguous
            (2, "3.FWJS07:综合评审模板\n评审要素"),  # safe
        ]
        anchors = extract_template_anchors(page_texts)
        assert 1 in anchors, "extract 全量应保留歧义页锚点"
        boundaries = detect_template_boundaries(page_texts)
        boundary_pages = {b.page_num for b in boundaries}
        assert 1 not in boundary_pages, "detect 应跳过歧义页"

    def test_sample09_three_codes_detected(self):
        """A3: sample09 真实案例——FWJS05(冒号)/FWJS16(无冒号)/FWJS18(前缀) 均检测到。"""
        from app.core.parser.scoring_template_boundaries import extract_template_anchors

        page_texts = [
            (1, "(1)技术评分标准\n5.FWJS05:技改大修、专业分包工程施工服务技术详评模板V1.0\n评审要素"),
            (2, "FWJS16服务技术详评模板(通用)V1.0(综合服务、运维服务)\n评审要素"),
            (3, "18.FWJS18科技技术服务、科技项目技术详评模板V1.0\n评审要素"),
        ]
        result = extract_template_anchors(page_texts)
        assert "FWJS05" in result.get(1, []), f"FWJS05 应检测到: {result}"
        assert "FWJS16" in result.get(2, []), f"FWJS16(无冒号) 应检测到: {result}"
        assert "FWJS18" in result.get(3, []), f"FWJS18 应检测到: {result}"


# ===========================================================================
# Part 18: C6 — per-scheme 指纹集合（_fingerprints_by_scheme）
# ===========================================================================


class TestPerSchemeFingerprints:
    """C6: _fingerprints_by_scheme per-scheme 副本检查；A→B→A 后 B(fpB) 仍 scheme_2。"""

    def _item(self, score, scheme="scheme_1"):
        return SimpleNamespace(scheme=scheme, score_weight=score, volume=None)

    def test_c6_after_aba_revisit_fpb_recognized_as_scheme2_not_scheme1_copy(self):
        """C6: A→B→A→B(fpB) 场景——fpB 仅是 scheme_2 的副本，不被 scheme_1 误识为副本。

        OLD BUG: 单一 _current_fingerprints 在 B→A 回访时混入 fpB，导致在 scheme_1 上下文
                 中 fpB 被误认为副本，返回 scheme_1（不应该）。
        NEW FIX: _fingerprints_by_scheme per-scheme 隔离，副本检查只查当前 scheme 集合。
        """
        from app.core.extractor.scoring.scheme_segmentation import SchemeSegmenter

        seg = SchemeSegmenter()
        survivors = []

        # A: scheme_1, fp=fpA
        s1 = seg.assign_scheme_for_chunk(
            chunk_fingerprint="fp-a", incoming_item_count=5,
            current_survivors=[], volume_defs={},
            scheme_boundary_id="tb:A",
        )
        assert s1 == "scheme_1"
        survivors.append(self._item(50.0, "scheme_1"))

        # B: scheme_2, fp=fpB（推进因为有 survivors + 新边界）
        s2 = seg.assign_scheme_for_chunk(
            chunk_fingerprint="fp-b", incoming_item_count=5,
            current_survivors=survivors, volume_defs={},
            scheme_boundary_id="tb:B",
        )
        assert s2 == "scheme_2"
        survivors.append(self._item(50.0, "scheme_2"))

        # A revisit: 回到 scheme_1（C3）
        s3 = seg.assign_scheme_for_chunk(
            chunk_fingerprint="fp-a2", incoming_item_count=3,
            current_survivors=survivors, volume_defs={},
            scheme_boundary_id="tb:A",
        )
        assert s3 == "scheme_1", f"A 回访应为 scheme_1，实际: {s3}"

        # B再次出现，fp=fpB（与 step2 相同）：
        # OLD: fpB 在 _current_fingerprints（混合集）→ 误判为副本 → scheme_1（WRONG）
        # NEW: fpB 不在 scheme_1 的指纹集 → 非副本 → 继续 C3 回访 → scheme_2（CORRECT）
        s4 = seg.assign_scheme_for_chunk(
            chunk_fingerprint="fp-b", incoming_item_count=5,
            current_survivors=survivors, volume_defs={},
            scheme_boundary_id="tb:B",
        )
        assert s4 == "scheme_2", (
            f"C6: A→B→A→B(fpB) 应归 scheme_2 (per-scheme 副本隔离)，实际: {s4}"
        )
        assert seg.scheme_count == 2, (
            f"C6: 不应产生 scheme_3，实际 scheme_count={seg.scheme_count}"
        )

    def test_c6_copy_within_same_scheme_still_detected(self):
        """C6: 同一 scheme 内的副本仍能正确检测（不因 per-scheme 拆分而漏检）。"""
        from app.core.extractor.scoring.scheme_segmentation import SchemeSegmenter

        seg = SchemeSegmenter()

        s1 = seg.assign_scheme_for_chunk(
            chunk_fingerprint="fp-a", incoming_item_count=5,
            current_survivors=[], volume_defs={}, scheme_boundary_id="tb:A",
        )
        assert s1 == "scheme_1"

        # 同指纹再次出现（副本），应返回 scheme_1（不推进）
        s2 = seg.assign_scheme_for_chunk(
            chunk_fingerprint="fp-a", incoming_item_count=5,
            current_survivors=[self._item(50.0, "scheme_1")], volume_defs={},
            scheme_boundary_id="tb:A",
        )
        assert s2 == "scheme_1", f"同方案副本应仍为 scheme_1: {s2}"
        assert seg.scheme_count == 1, "副本不应推进 scheme_count"


# ===========================================================================
# Part 19: C7 — LLM 0 items 不消费边界
# ===========================================================================


class TestLlmZeroItemsC7:
    """C7: LLM incoming_item_count=0 时不消费边界（strict len）。"""

    def _item(self, score, scheme="scheme_1"):
        return SimpleNamespace(scheme=scheme, score_weight=score, volume=None)

    def test_zero_items_does_not_advance_scheme_or_consume_boundary(self):
        """C7: assign_scheme_for_chunk(incoming_item_count=0) 不推进 scheme，不消费边界。"""
        from app.core.extractor.scoring.scheme_segmentation import SchemeSegmenter

        seg = SchemeSegmenter()
        survivors = []

        s1 = seg.assign_scheme_for_chunk(
            chunk_fingerprint="fp-a", incoming_item_count=5,
            current_survivors=[], volume_defs={}, scheme_boundary_id="tb:A",
        )
        assert s1 == "scheme_1"
        survivors.append(self._item(50.0, "scheme_1"))

        # LLM fallback 返回 0 items（strict len）
        s2 = seg.assign_scheme_for_chunk(
            chunk_fingerprint="fp-b", incoming_item_count=0,  # 0 items
            current_survivors=survivors, volume_defs={},
            scheme_boundary_id="tb:B",  # 新边界，但 0 items
        )
        assert s2 == "scheme_1", f"C7: 0 items 不消费边界，应返回当前 scheme_1，实际: {s2}"
        assert "tb:B" not in seg._boundary_scheme_map, "0 items 不应将 tb:B 写入 boundary_scheme_map"
        assert seg.scheme_count == 1, "0 items 不应推进 scheme_count"


# ===========================================================================
# Part 20: D8 — write_chunk_scope_metadata
# ===========================================================================


class TestWriteChunkScopeMetadata:
    """D8: write_chunk_scope_metadata 对 TextChunk 和 ORM Chunk 均正确写回。"""

    def test_text_chunk_path_writes_scheme(self):
        """D8: TextChunk（metadata dict）路径写回 scheme 和 boundary_id。"""
        from app.core.extractor.scoring.scoring_grid_payload import (
            PAYLOAD_KEY,
            write_chunk_scope_metadata,
        )

        payload: dict = {"version": 1, "grid": [["评审项目", "分值"]]}
        chunk = SimpleNamespace(metadata={PAYLOAD_KEY: payload})
        write_chunk_scope_metadata(chunk, scheme="scheme_1", scheme_boundary_id="tb:FWJS05")
        assert payload.get("scheme") == "scheme_1", f"scheme 写回失败: {payload}"
        assert payload.get("scheme_boundary_id") == "tb:FWJS05"

    def test_orm_chunk_path_replaces_dict_for_sa_tracking(self):
        """D8: ORM Chunk（chunk_metadata dict）浅拷贝后重新赋值以触发 SQLAlchemy 脏跟踪。"""
        from app.core.extractor.scoring.scoring_grid_payload import (
            PAYLOAD_KEY,
            write_chunk_scope_metadata,
        )

        payload: dict = {"version": 1, "grid": [["评审项目", "分值"]]}
        original_meta = {PAYLOAD_KEY: payload}
        chunk = SimpleNamespace(chunk_metadata=original_meta)
        original_id = id(chunk.chunk_metadata)

        write_chunk_scope_metadata(chunk, scheme="scheme_2")

        assert id(chunk.chunk_metadata) != original_id, (
            "ORM 路径应替换 chunk_metadata 对象以触发 SQLAlchemy 脏标记"
        )
        updated_payload = chunk.chunk_metadata.get(PAYLOAD_KEY)
        assert updated_payload is not None
        assert updated_payload.get("scheme") == "scheme_2"

    def test_text_chunk_without_payload_is_safe(self):
        """D8: chunk.metadata 无 PAYLOAD_KEY 时不报错（防御性）。"""
        from app.core.extractor.scoring.scoring_grid_payload import write_chunk_scope_metadata

        chunk = SimpleNamespace(metadata={})
        write_chunk_scope_metadata(chunk, scheme="scheme_1")  # 不应抛出


# ===========================================================================
# Part 21: E9 — assign_and_stamp_scheme（scoring_scheme_assignment）
# ===========================================================================


class TestAssignAndStampScheme:
    """E9: assign_and_stamp_scheme 封装完整的 segmenter→stamp→payload 流程。"""

    def test_basic_assignment_stamps_items_and_writes_payload(self):
        """E9: 推进 segmenter，盖章 items，写回 chunk payload。"""
        from app.core.extractor.scoring.scheme_segmentation import SchemeSegmenter
        from app.core.extractor.scoring.scoring_grid_payload import PAYLOAD_KEY
        from app.core.extractor.scoring.scoring_scheme_assignment import assign_and_stamp_scheme

        seg = SchemeSegmenter()
        payload: dict = {"version": 1, "grid": [["a"]], "scheme_boundary_id": "tb:A"}
        chunk = SimpleNamespace(metadata={PAYLOAD_KEY: payload})
        items = [SimpleNamespace(scheme=None)]

        scheme = assign_and_stamp_scheme(seg, items, chunk, payload, [], {})

        assert scheme == "scheme_1"
        assert items[0].scheme == "scheme_1"
        assert payload.get("scheme") == "scheme_1"

    def test_empty_items_does_not_advance_scheme(self):
        """E9/C7: 空 items 传入不推进 scheme（assign_scheme_for_chunk 中 incoming_item_count=0）。"""
        from app.core.extractor.scoring.scheme_segmentation import SchemeSegmenter
        from app.core.extractor.scoring.scoring_grid_payload import PAYLOAD_KEY
        from app.core.extractor.scoring.scoring_scheme_assignment import assign_and_stamp_scheme

        seg = SchemeSegmenter()
        payload: dict = {"version": 1, "grid": [["a"]], "scheme_boundary_id": "tb:B"}
        chunk = SimpleNamespace(metadata={PAYLOAD_KEY: payload})

        # 先建立 scheme_1
        items1 = [SimpleNamespace(scheme=None)]
        assign_and_stamp_scheme(seg, items1, chunk, {"scheme_boundary_id": None}, [], {})

        # 空 items：不推进
        scheme_empty = assign_and_stamp_scheme(seg, [], chunk, payload, [], {})
        assert scheme_empty == "scheme_1", (
            f"空 items 不应推进到 scheme_2，实际: {scheme_empty}"
        )
        assert seg.scheme_count == 1


# ===========================================================================
# Part 22: B4 — lot×section 真正求交集成测试
# ===========================================================================


def _build_two_lot_doc():
    """构建含两个 lot 范围和相邻 scoring sections 的合成文档（B4 测试用）。

    Lot 1: p1-p5（标题含 "第一包"）
    Lot 2: p6-p10（标题含 "第二包"）
    评分区段: p3-p8（跨 lot，含模板边界）
    模板边界: p4(FWJS05), p7(FWJS06)
    预期装配范围:
      Lot1 × Section = [p3,p5]，再按边界切分 → [p3,p3] + [p4,p5]
      Lot2 × Section = [p6,p8]，再按边界切分 → [p6,p6] + [p7,p8]
    """
    from app.core.parser.pdf_parser import ExtractedTable, ParsedDocument, ParsedPage

    header = ["评分项目", "分值", "评分标准"]

    def _make_page(pnum, title_text, table_rows=None):
        tables = []
        if table_rows:
            tables.append(
                ExtractedTable(
                    table_index=0, page_num=pnum,
                    data=table_rows, row_count=len(table_rows),
                    col_count=3, bbox=(0, 0, 500, 300),
                )
            )
        return ParsedPage(page_num=pnum, text=title_text, tables=tables)

    pages = [
        _make_page(1, "第一包评标办法\n综合评分标准"),
        _make_page(2, "第一包技术标准\n评分要求"),
        _make_page(3, "第一包评分表\n评审要素", [header, ["技术能力", "30", "优秀30分"]]),
        _make_page(4, "1.FWJS05:技术方案评分模板\n评审要素", [header, ["方案", "20", "优20分"]]),
        _make_page(5, "第一包评分延续页\n评审要素", [header, ["资质", "10", "有资质10分"]]),
        _make_page(6, "第二包评标办法\n综合评分标准"),
        _make_page(7, "2.FWJS06:商务报价评分模板\n评审要素", [header, ["报价", "40", "低40分"]]),
        _make_page(8, "第二包评分延续页\n评审要素", [header, ["服务", "10", "好10分"]]),
        _make_page(9, "附录\n其他说明"),
        _make_page(10, "尾页"),
    ]
    return ParsedDocument(
        file_path="two_lot_test.pdf",
        filename="two_lot_test.pdf",
        page_count=10,
        pages=pages,
    )


class TestLotSectionIntersectionB4:
    """B4: sections×lot 真正求交，不跨 lot 合并，不同 lot 独立切分。"""

    # ------------------------------------------------------------------
    # 单元测试：直接测试 _intersect_lots_and_split_by_boundaries 函数
    # ------------------------------------------------------------------

    def _make_lot(self, label: str, start: int, end: int):
        from app.core.parser.lot_boundary_locator import LotBoundary
        return LotBoundary(label=label, page_start=start, page_end=end)

    def _make_section(self, start: int, end: int):
        from app.core.parser.scoring_section_locator import ScoringSection
        return ScoringSection(title="评分表", page_start=start, page_end=end, detection="test")

    def _make_boundary(self, page_num: int, code: str):
        from app.core.parser.scoring_template_boundaries import TemplateBoundary
        return TemplateBoundary(page_num=page_num, template_code=code)

    def test_no_boundaries_lots_returned_unchanged(self):
        """D/F GREEN: template_boundaries 为空时，原样返回 list(lots)，不与 sections 求交/收窄。"""
        from app.core.parser.scoring_chunk_builder import _intersect_lots_and_split_by_boundaries

        lots = [
            self._make_lot("第一包", 1, 5),
            self._make_lot("第二包", 6, 10),
        ]
        # sections 故意比 lots 更窄，若与 sections 求交会丢失范围
        sections = [self._make_section(3, 8)]

        result = _intersect_lots_and_split_by_boundaries(lots, sections, [])

        assert len(result) == 2, f"无边界时 lots 应原样返回 2 个，实际: {len(result)}"
        assert result[0].page_start == 1 and result[0].page_end == 5, (
            f"第一包范围应为 [1,5]，实际: [{result[0].page_start},{result[0].page_end}]"
        )
        assert result[1].page_start == 6 and result[1].page_end == 10, (
            f"第二包范围应为 [6,10]，实际: [{result[1].page_start},{result[1].page_end}]"
        )

    def test_with_boundaries_no_cross_lot_split(self):
        """D/F GREEN: 有边界时逐 lot 求交并切分，结果范围不跨 lot 边界。"""
        from app.core.parser.scoring_chunk_builder import _intersect_lots_and_split_by_boundaries

        lots = [
            self._make_lot("第一包", 1, 5),
            self._make_lot("第二包", 6, 10),
        ]
        # section 横跨两个 lot（p3-p8）
        sections = [self._make_section(3, 8)]
        # 每个 lot 内各有一个模板边界
        boundaries = [
            self._make_boundary(4, "FWJS05"),
            self._make_boundary(7, "FWJS06"),
        ]

        result = _intersect_lots_and_split_by_boundaries(lots, sections, boundaries)

        # 所有 span 不应跨越 lot 分割线 p5↔p6
        for span in result:
            assert not (span.page_start <= 5 and span.page_end >= 6), (
                f"span 跨越 lot 边界: [{span.page_start},{span.page_end}]"
            )
        # lot1 范围内应有至少 1 个 span
        lot1_spans = [s for s in result if s.page_end <= 5]
        assert len(lot1_spans) >= 1, "第一包应有至少 1 个装配范围"
        # lot2 范围内应有至少 1 个 span
        lot2_spans = [s for s in result if s.page_start >= 6]
        assert len(lot2_spans) >= 1, "第二包应有至少 1 个装配范围"

    # ------------------------------------------------------------------
    # 集成测试：通过 build_scoring_table_chunks 端到端验证
    # ------------------------------------------------------------------

    def test_lot_boundaries_respected_no_cross_lot_merge(self):
        """B4: 有 lots 时，assembly_spans 不跨越 lot 边界（逐 lot 独立求交）。"""
        from app.core.parser.scoring_chunk_builder import build_scoring_table_chunks
        from app.core.parser.chunk_planner import ChunkPlanner

        doc = _build_two_lot_doc()
        planner = ChunkPlanner()
        chunks = build_scoring_table_chunks(parsed_doc=doc, planner=planner, start_index=0)

        assert len(chunks) > 0, "应产出至少 1 个 chunk"

        # 所有 chunk 的页范围不应跨越 lot 边界（lot1:p1-p5, lot2:p6-p10）
        from app.core.extractor.scoring.scoring_grid_payload import PAYLOAD_KEY
        for chunk in chunks:
            payload = chunk.metadata.get(PAYLOAD_KEY) or {}
            ps = payload.get("page_start")
            pe = payload.get("page_end")
            if ps and pe:
                # 不允许跨越 lot 边界（p5↔p6 是 lot 分割线）
                assert not (ps <= 5 and pe >= 6), (
                    f"chunk 跨越 lot 边界: page_start={ps}, page_end={pe}"
                )


# ===========================================================================
# Part 23: E10 — scoring_extractor ≤800 行守卫
# ===========================================================================


class TestScoringExtractorLineCount:
    """E10: scoring_extractor.py 行数守卫（E9 移出 scheme 逻辑后恢复 ≤800）。"""

    def test_scoring_extractor_le_800_lines(self):
        """E10: scoring_extractor.py 文件行数 ≤800。"""
        from pathlib import Path

        path = (
            Path(__file__).parent.parent
            / "app"
            / "core"
            / "extractor"
            / "scoring"
            / "scoring_extractor.py"
        )
        assert path.exists(), f"文件不存在: {path}"
        lines = path.read_text(encoding="utf-8").count("\n")
        assert lines <= 800, (
            f"scoring_extractor.py 行数 {lines} 超过 800 守卫（E9 移出 scheme 逻辑后应 ≤800）"
        )
