""" 防御测试: 装配段 source_keys 追踪 + chunk 验收后才提交 claim。

映射真实风险:
- 逻辑装配段产出后 build_segment_chunk 返回 None（段被拒），但物理表键已被
 全量 claim，导致 collect_unclaimed_table_scoring_chunks fallback 跳过了
 该物理表，评分表不可恢复地丢失。

RED 失败预期:
- test_source_keys_*: AttributeError / 断言失败（source_keys 字段尚不存在）
- test_apply_segment_claim_*: ImportError（scoring_segment_acceptance 尚不存在）
- test_one_table_two_segments_*: ImportError
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.parser.pdf_parser import ParsedDocument, ParsedPage  # noqa: E402
from app.core.parser.scoring_section_locator import ScoringSection  # noqa: E402
from app.core.parser.scoring_table_assembler import (  # noqa: E402
    LogicalScoringSegment,
    assemble_scoring_segments,
)

import pytest

pytestmark = pytest.mark.scoring


# ---------------------------------------------------------------------------
# source_keys 字段测试（RED: 字段不存在则断言失败）
# ---------------------------------------------------------------------------


def test_source_keys_field_exists_on_new_segment():
    """LogicalScoringSegment 必须包含 source_keys 字段。

    RED: 字段未添加时 hasattr 返回 False，断言失败。
    """
    seg = LogicalScoringSegment(title="t", header_row=["a", "b"])
    assert hasattr(seg, "source_keys"), (
        "LogicalScoringSegment 必须有 source_keys 字段"
    )


def test_source_keys_default_empty_set():
    """source_keys 默认为空集合，不应与其他实例共享。"""
    seg1 = LogicalScoringSegment(title="A", header_row=[])
    seg2 = LogicalScoringSegment(title="B", header_row=[])
    assert hasattr(seg1, "source_keys")
    assert seg1.source_keys == set()
    seg1.source_keys.add((1, 0))
    assert (1, 0) not in seg2.source_keys, "source_keys 不得跨实例共享"


def test_assembler_populates_source_keys():
    """装配后的段必须记录贡献物理表键 (page, table_index)。

    RED: source_keys 字段不存在或 feed_table 未写入时断言失败。
    """
    header = ["评分项目", "评分标准", "分值"]
    data = [["技术方案", "好得10分", "10"]]
    table = SimpleNamespace(data=[header, *data], table_index=0)
    page = ParsedPage(page_num=1, text="", tables=[table])
    doc = ParsedDocument(
        file_path="x.pdf", filename="x.pdf", page_count=1, pages=[page]
    )
    sec = ScoringSection("评分表", 1, 1, "structure")

    segments, claimed = assemble_scoring_segments(doc, sec)

    assert len(segments) > 0, "必须产出至少一个装配段"
    seg = segments[0]
    assert hasattr(seg, "source_keys"), "段必须有 source_keys"
    assert (1, 0) in seg.source_keys, (
        "段必须记录贡献的 (page=1, table_index=0) 来源键"
    )
    assert claimed == {(1, 0)}, "assembler.claimed 应包含同一来源"


def test_assembler_two_pages_source_keys_per_segment():
    """跨页续表: 两个物理表合并为一个段，source_keys 含两张表的键。"""
    header = ["评分项目", "评分标准", "分值"]
    data_p1 = [["技术方案", "好得10分", "10"]]
    data_p2 = [["商务方案", "提供得5分", "5"]]
    tbl_p1 = SimpleNamespace(data=[header, *data_p1], table_index=0)
    tbl_p2 = SimpleNamespace(data=data_p2, table_index=0)
    pages = [
        ParsedPage(page_num=1, text="评分表", tables=[tbl_p1]),
        ParsedPage(page_num=2, text="续表", tables=[tbl_p2]),
    ]
    doc = ParsedDocument(file_path="x.pdf", filename="x.pdf", page_count=2, pages=pages)
    sec = ScoringSection("评分表", 1, 2, "structure")

    segments, claimed = assemble_scoring_segments(doc, sec)

    scored = [s for s in segments if s.scored_row_count > 0]
    assert len(scored) >= 1
    # 两个物理表都被认领
    assert len(claimed) == 2
    # 段的 source_keys 涵盖贡献该段的物理表键
    seg = scored[0]
    assert hasattr(seg, "source_keys")
    assert seg.source_keys.issuperset({(1, 0)}), (
        "段来源键至少包含首页物理表"
    )


# ---------------------------------------------------------------------------
# apply_segment_claim 单元测试（RED: 模块不存在 → ImportError）
# ---------------------------------------------------------------------------


def test_apply_segment_claim_rejected_no_update():
    """chunk=None 时 claimed_global 不得更新（被拒段不 claim）。"""
    from app.core.parser.scoring_segment_acceptance import (  # type: ignore[import]
        apply_segment_claim,
    )

    claimed: set = set()
    source_keys: frozenset = frozenset({(1, 0), (2, 0)})
    result = apply_segment_claim(None, source_keys, claimed)

    assert result is False
    assert len(claimed) == 0, "被拒段不得 claim 任何物理表"


def test_apply_segment_claim_accepted_updates_claimed():
    """chunk 非 None 时 claimed_global 必须包含 source_keys。"""
    from app.core.parser.scoring_segment_acceptance import (  # type: ignore[import]
        apply_segment_claim,
    )

    claimed: set = set()
    source_keys: frozenset = frozenset({(3, 0)})
    chunk = object()
    result = apply_segment_claim(chunk, source_keys, claimed)

    assert result is True
    assert (3, 0) in claimed, "验收后来源键必须进入 claimed"


def test_one_table_two_segments_span_claim_shared_table_claimed():
    """ 核心场景（修订）: 同一物理表 (1,0) 被两段引用，一段拒绝。

 compute_span_claim 正确的 span 级别语义:
 - (1,0) 应被 claim（接受段 seg_b 已处理，不需要 fallback 重复处理）
 - (2,0) 应被 claim（仅被接受段独占）
 - 仅被拒绝段独占的表才不应被 claim（此场景不存在这样的表）

 修订原因: 旧逻辑 accepted_keys - rejected_keys 导致共享表不被 claim，
 unclaimed fallback 重新处理已接受段的数据，产生重复评分项（sample06 回归）。
 """
    from app.core.parser.scoring_segment_acceptance import (  # type: ignore[import]
        compute_span_claim,
    )

    # 两段均引用共享物理表 (1,0)；seg_a 被拒，seg_b 被接受
    seg_a_keys: set[tuple[int, int]] = {(1, 0)}
    seg_b_keys: set[tuple[int, int]] = {(1, 0), (2, 0)}

    results = [
        (seg_a_keys, None),       # seg_a 被拒
        (seg_b_keys, object()),   # seg_b 被接受
    ]
    safe = compute_span_claim(results)  # type: ignore[attr-defined]

    assert (1, 0) in safe, (
        "共享表已被接受段 seg_b 处理，应被 claim，防止 fallback 重复处理产生重复项"
    )
    assert (2, 0) in safe, "仅被接受段独占的物理表应正常 claim"


# ---------------------------------------------------------------------------
# C2: compute_span_claim（每次 span 级别的安全 claim 计算）
# ---------------------------------------------------------------------------


def test_C2_compute_span_claim_exists():
    """C2: scoring_segment_acceptance 必须导出 compute_span_claim 函数。

    RED: 函数尚不存在 → ImportError/AttributeError。
    """
    from app.core.parser.scoring_segment_acceptance import (  # type: ignore[import]
        compute_span_claim,
    )

    assert callable(compute_span_claim)


def test_C2_only_rejected_table_not_claimed():
    """C2: 仅被拒绝段引用的物理表不得 claim（fallback 可恢复）；
    接受段引用的表（含共享表）均应被 claim。

    修订自 test_C2_shared_table_one_seg_rejected_not_claimed:
    共享表被接受段引用时应 claim（防重复），仅被拒绝段独占时才不 claim。
    """
    from app.core.parser.scoring_segment_acceptance import (  # type: ignore[import]
        compute_span_claim,
    )

    shared_key = (1, 0)   # 被接受段和拒绝段共同引用
    accepted_only = (2, 0)  # 仅被接受段引用
    rejected_only = (3, 0)  # 仅被拒绝段引用

    results = [
        ({shared_key, accepted_only}, object()),   # accepted
        ({shared_key, rejected_only}, None),        # rejected
    ]
    safe = compute_span_claim(results)

    assert shared_key in safe, "共享表已被接受段处理，应被 claim，防 fallback 重复"
    assert accepted_only in safe, "仅被接受段引用的表应可 claim"
    assert rejected_only not in safe, "仅被拒绝段引用的表不得 claim，留给 fallback 恢复"


def test_C2_all_accepted_all_safe():
    """C2: 所有段均接受时全量 claim。"""
    from app.core.parser.scoring_segment_acceptance import (  # type: ignore[import]
        compute_span_claim,
    )

    results = [
        ({(1, 0)}, object()),
        ({(2, 0)}, object()),
    ]
    safe = compute_span_claim(results)
    assert (1, 0) in safe
    assert (2, 0) in safe


def test_C2_all_rejected_nothing_safe():
    """C2: 所有段均拒绝时 safe_keys 为空。"""
    from app.core.parser.scoring_segment_acceptance import (  # type: ignore[import]
        compute_span_claim,
    )

    results: list = [
        ({(1, 0)}, None),
        ({(2, 0)}, None),
    ]
    safe = compute_span_claim(results)
    assert len(safe) == 0


# ---------------------------------------------------------------------------
# I1: stub recovery provisional claimed（不污染 global 直至 chunk 验收）
# ---------------------------------------------------------------------------


def test_I1_stub_keys_excluded_from_global_on_rejection():
    """I1: 被拒段的 stub 续表键不得进入 claimed_global。

    通过 collect_assembled_scoring_chunks 的 stub 参与渲染但不提前写 global 来验证:
    build_segment_chunk 返回 None 时，stub 恢复的表键也不应 claim。
    """
    from types import SimpleNamespace
    from app.core.parser import ChunkPlanner  # type: ignore[import]
    from app.core.parser.pdf_parser import ParsedDocument, ParsedPage  # type: ignore[import]
    from app.core.parser.scoring_section_locator import ScoringSection  # type: ignore[import]
    from app.core.parser.scoring_chunk_sources import (  # type: ignore[import]
        ScoringChunkBuildState,
        collect_assembled_scoring_chunks,
    )

    header = ["评分项目", "评分标准", "分值"]
    data = [["技术方案", "内容全面得10分，一般得5分", "10"]]
    tbl = SimpleNamespace(data=[header, *data], table_index=0)
    page = ParsedPage(page_num=1, text="评分区段", tables=[tbl])
    doc = ParsedDocument(file_path="x.pdf", filename="x.pdf", page_count=1, pages=[page])
    sec = ScoringSection("评分表", 1, 1, "structure")
    pages_by_num = {1: page}

    state = ScoringChunkBuildState(
        idx=0, claimed_global=set(), seen_segments=set(),
        seen_tables=set(), rejected_with_score_signal=[],
    )

    def always_reject(seg, span, planner_, idx, seen, **kw):
        return None

    collect_assembled_scoring_chunks(
        parsed_doc=doc,
        assembly_spans=[sec],
        planner=ChunkPlanner(),
        state=state,
        lots=[],
        volume_prelude=None,
        pages_by_num=pages_by_num,
        build_segment_chunk=always_reject,
        lot_label_for_page=lambda _lots, _p: None,
        volume_title_for_page=lambda _pbp, _p: None,
    )

    assert len(state.claimed_global) == 0, (
        "所有段均被拒绝（包括 stub 续表），claimed_global 必须为空"
    )


# ---------------------------------------------------------------------------
# I2: 完整链路集成测试
# ---------------------------------------------------------------------------


def test_I2_rejected_segment_table_enters_fallback_full_chain():
    """I2: 被拒装配段的物理表必须可进入 collect_unclaimed_table_scoring_chunks fallback。

    证明: collect_assembled_scoring_chunks（段拒绝）→ claimed_global 为空
         → collect_unclaimed_table_scoring_chunks 能产出 chunk。

    RED: 若 compute_span_claim 不存在或逻辑有误，claimed_global 含 (1,0)，fallback 返回空。
    """
    from types import SimpleNamespace
    from app.core.parser import ChunkPlanner  # type: ignore[import]
    from app.core.parser.pdf_parser import ParsedDocument, ParsedPage  # type: ignore[import]
    from app.core.parser.scoring_section_locator import ScoringSection  # type: ignore[import]
    from app.core.parser.scoring_chunk_sources import (  # type: ignore[import]
        ScoringChunkBuildState,
        collect_assembled_scoring_chunks,
        collect_unclaimed_table_scoring_chunks,
    )

    header = ["评分项目", "评分标准", "分值"]
    data = [
        ["技术方案", "内容全面得10分，一般得5分", "10"],
        ["商务方案", "材料完整得5分，欠缺不得分", "5"],
    ]
    tbl = SimpleNamespace(data=[header, *data], table_index=0)
    page = ParsedPage(page_num=1, text="评分区段详细规则", tables=[tbl])
    doc = ParsedDocument(
        file_path="x.pdf", filename="x.pdf", page_count=1, pages=[page]
    )
    sec = ScoringSection("评分表", 1, 1, "structure")
    pages_by_num = {1: page}

    state = ScoringChunkBuildState(
        idx=0, claimed_global=set(), seen_segments=set(),
        seen_tables=set(), rejected_with_score_signal=[],
    )
    planner = ChunkPlanner()

    def always_reject(seg, span, pl, idx, seen, **kw):
        return None

    assembled = collect_assembled_scoring_chunks(
        parsed_doc=doc,
        assembly_spans=[sec],
        planner=planner,
        state=state,
        lots=[],
        volume_prelude=None,
        pages_by_num=pages_by_num,
        build_segment_chunk=always_reject,
        lot_label_for_page=lambda _l, _p: None,
        volume_title_for_page=lambda _pbp, _p: None,
    )

    assert len(assembled) == 0
    assert (1, 0) not in state.claimed_global, (
        "被拒段的物理表(1,0)不得被 claim"
    )

    fallback = collect_unclaimed_table_scoring_chunks(
        parsed_doc=doc,
        located_sections=[sec],
        planner=planner,
        state=state,
        lots=[],
        volume_prelude=None,
        pages_by_num=pages_by_num,
        inject_volume_prelude=lambda md, p: md,
        inject_lot_label=lambda md, l_: md,
        join_section_path=lambda t, v: t,
        lot_label_for_page=lambda _l, _p: None,
        volume_title_for_page=lambda _pbp, _p: None,
        attach_scoring_payload=lambda *a, **kw: None,
    )

    assert len(fallback) > 0, (
        "被拒装配段的物理表(1,0)未被 claim，必须可进入 unclaimed fallback 并产出 chunk"
    )


# ---------------------------------------------------------------------------
# C1/I1 Warning 测试: patch 模块 logger，验证共享 key 触发 warning 行为
# ---------------------------------------------------------------------------


def test_compute_span_claim_warns_on_shared_key_and_still_claims():
    """共享物理表（接受段 + 拒绝段同时引用）: 触发 logger.warning 且仍被 claim。

    warning 信号: '物理表部分段验收失败'（稳定中文标识符，供日志过滤）。
    claim 目的: 防止 unclaimed fallback 重复产出已接受段的评分项。
    """
    from unittest.mock import patch

    from app.core.parser.scoring_segment_acceptance import (  # type: ignore[import]
        compute_span_claim,
    )

    shared_key: tuple[int, int] = (1, 0)
    results = [
        ({shared_key, (2, 0)}, object()),  # accepted，含共享表
        ({shared_key}, None),               # rejected，也含共享表
    ]

    with patch("app.core.parser.scoring_segment_acceptance.logger") as mock_log:
        safe = compute_span_claim(results)

    mock_log.warning.assert_called_once()
    warning_msg = str(mock_log.warning.call_args)
    assert "物理表部分段验收失败" in warning_msg, (
        f"warning 必须包含稳定中文信号'物理表部分段验收失败'，实际: {warning_msg}"
    )
    assert shared_key in safe, "共享表已被接受段处理，必须 claim 防 fallback 重复"
    assert (2, 0) in safe


def test_compute_span_claim_no_warning_for_rejected_only_key():
    """仅被拒绝段独占的表: 不 claim 且不触发 partial warning。

    拒绝段独占表的正常路径: 静默不 claim，由 fallback 恢复——不需要 warning。
    """
    from unittest.mock import patch

    from app.core.parser.scoring_segment_acceptance import (  # type: ignore[import]
        compute_span_claim,
    )

    rejected_only: tuple[int, int] = (3, 0)
    results = [
        ({(1, 0)}, object()),            # accepted，无重叠
        ({rejected_only}, None),           # rejected 独占
    ]

    with patch("app.core.parser.scoring_segment_acceptance.logger") as mock_log:
        safe = compute_span_claim(results)

    mock_log.warning.assert_not_called()
    assert rejected_only not in safe, "仅被拒绝段独占的表不得 claim，留给 fallback"
    assert (1, 0) in safe


# ---------------------------------------------------------------------------
# 诊断管线测试: build_partial_rejection_diagnostics + write_parse_diagnostics
# + parse_scoring_chunk_candidate 合并诊断
# ---------------------------------------------------------------------------


def test_build_partial_rejection_diagnostics_shared_scored_produces_signal():
    """共享物理表被拒段 scored_row_count > 0：产生稳定信号 '物理表部分段验收失败'。"""
    from app.core.parser.scoring_segment_acceptance import build_partial_rejection_diagnostics
    from app.core.parser.scoring_table_assembler import CanonicalScoringRow, LogicalScoringSegment

    # 构造有 1 条 25分行的被拒段
    rejected_seg = LogicalScoringSegment(title="技术评分", header_row=["评分项目", "评分标准", "分值"])
    rejected_seg.source_keys = {(1, 0)}
    rejected_seg.page_start = 1
    rejected_seg.page_end = 1
    rejected_seg.rows.append(
        CanonicalScoringRow(
            group="",
            factor="技术方案",
            rule="内容全面得25分",
            score_text="25",
            score=25.0,
            page=1,
        )
    )
    assert rejected_seg.scored_row_count == 1, "前置条件: 段有 1 条计分行"

    accepted_seg = SimpleNamespace(source_keys={(1, 0), (2, 0)}, scored_row_count=3,
                                   page_start=1, page_end=2)

    span_results = [
        ({(1, 0), (2, 0)}, object()),  # accepted
        ({(1, 0)}, None),              # rejected（共享 (1,0)）
    ]
    seg_chunks = [
        (accepted_seg, object()),
        (rejected_seg, None),
    ]

    diags = build_partial_rejection_diagnostics(span_results, seg_chunks)
    assert diags, "共享表被拒段有计分行，应产生诊断"
    combined = " ".join(diags)
    assert "物理表部分段验收失败" in combined, f"诊断必须含稳定信号，实际: {diags}"
    assert "scored_rows=1" in combined, f"诊断应记录 scored_rows，实际: {diags}"


def test_build_partial_rejection_diagnostics_empty_rejected_no_signal():
    """被拒段 scored_row_count == 0（空段/dedup）：不产生诊断。"""
    from app.core.parser.scoring_segment_acceptance import build_partial_rejection_diagnostics

    empty_rejected = SimpleNamespace(source_keys={(1, 0)}, scored_row_count=0,
                                     page_start=1, page_end=1)
    span_results = [
        ({(1, 0), (2, 0)}, object()),
        ({(1, 0)}, None),
    ]
    seg_chunks = [
        (SimpleNamespace(source_keys={(1, 0), (2, 0)}, scored_row_count=2,
                         page_start=1, page_end=2), object()),
        (empty_rejected, None),
    ]

    diags = build_partial_rejection_diagnostics(span_results, seg_chunks)
    assert len(diags) == 0, "空被拒段不得产生诊断（防假阳）"


def test_build_partial_rejection_diagnostics_no_shared_no_signal():
    """被拒段与接受段无共享物理表：不产生诊断。"""
    from app.core.parser.scoring_segment_acceptance import build_partial_rejection_diagnostics

    span_results = [
        ({(1, 0)}, object()),   # accepted，key=(1,0)
        ({(2, 0)}, None),        # rejected，key=(2,0)，无重叠
    ]
    seg_chunks = [
        (SimpleNamespace(source_keys={(1, 0)}, scored_row_count=3, page_start=1, page_end=1), object()),
        (SimpleNamespace(source_keys={(2, 0)}, scored_row_count=2, page_start=2, page_end=2), None),
    ]

    diags = build_partial_rejection_diagnostics(span_results, seg_chunks)
    assert len(diags) == 0, "无共享物理表不得产生诊断"


def test_parse_scoring_chunk_candidate_merges_parse_diagnostics():
    """parse_scoring_chunk_candidate 将 payload.parse_diagnostics 合并进 parsed.diagnostics。"""
    from app.core.extractor.scoring.scoring_candidate_pipeline import parse_scoring_chunk_candidate

    DIAG_MSG = "物理表部分段验收失败: pages=1-2 scored_rows=3"
    markdown = (
        "| 评分项目 | 评分标准 | 分值 |\n"
        "| --- | --- | --- |\n"
        "| 技术方案 | 内容全面得10分 | 10 |\n"
    )
    chunk = SimpleNamespace(
        content=markdown,
        section_path="技术评分",
        metadata={
            "scoring_payload": {
                "version": 1,
                "grid": [
                    ["评分项目", "评分标准", "分值"],
                    ["技术方案", "内容全面得10分", "10"],
                ],
                "table_fingerprint": "abc123",
                "parse_diagnostics": [DIAG_MSG],
            }
        },
    )

    candidate = parse_scoring_chunk_candidate(chunk)
    assert DIAG_MSG in candidate.parsed.diagnostics, (
        f"parse_diagnostics 必须合并到 parsed.diagnostics，实际: {candidate.parsed.diagnostics}"
    )


def test_parse_scoring_chunk_candidate_no_diag_when_payload_empty():
    """payload 无 parse_diagnostics：parsed.diagnostics 保持原状（不新增诊断条目）。"""
    from app.core.extractor.scoring.scoring_candidate_pipeline import parse_scoring_chunk_candidate

    markdown = (
        "| 评分项目 | 评分标准 | 分值 |\n"
        "| --- | --- | --- |\n"
        "| 报价合理性 | 评审价合理得30分 | 30 |\n"
    )
    chunk = SimpleNamespace(
        content=markdown,
        section_path="价格评分",
        metadata={
            "scoring_payload": {
                "version": 1,
                "grid": [
                    ["评分项目", "评分标准", "分值"],
                    ["报价合理性", "评审价合理得30分", "30"],
                ],
                "table_fingerprint": "def456",
                # 无 parse_diagnostics 字段
            }
        },
    )

    candidate = parse_scoring_chunk_candidate(chunk)
    has_partial_rejection = any("物理表部分段验收失败" in d for d in candidate.parsed.diagnostics)
    assert not has_partial_rejection, "无 parse_diagnostics 时不得出现部分拒绝信号"


def test_write_parse_diagnostics_memory_chunk():
    """write_parse_diagnostics 向内存 TextChunk 的 scoring_payload 追加诊断。"""
    from app.core.extractor.scoring.scoring_grid_payload import PAYLOAD_KEY, write_parse_diagnostics

    mock_chunk = SimpleNamespace(
        metadata={
            PAYLOAD_KEY: {
                "version": 1,
                "grid": [],
                "table_fingerprint": "x",
            }
        }
    )
    write_parse_diagnostics(mock_chunk, ["物理表部分段验收失败: pages=3-4 scored_rows=2"])

    result = mock_chunk.metadata[PAYLOAD_KEY].get("parse_diagnostics", [])
    assert len(result) == 1
    assert "物理表部分段验收失败" in result[0]


def test_write_parse_diagnostics_appends_to_existing():
    """write_parse_diagnostics 多次调用正确追加（不覆盖已有条目）。"""
    from app.core.extractor.scoring.scoring_grid_payload import PAYLOAD_KEY, write_parse_diagnostics

    mock_chunk = SimpleNamespace(
        metadata={
            PAYLOAD_KEY: {
                "version": 1,
                "grid": [],
                "table_fingerprint": "y",
                "parse_diagnostics": ["先前诊断A"],
            }
        }
    )
    write_parse_diagnostics(mock_chunk, ["物理表部分段验收失败: pages=1-1 scored_rows=1"])

    result = mock_chunk.metadata[PAYLOAD_KEY]["parse_diagnostics"]
    assert len(result) == 2
    assert result[0] == "先前诊断A"
    assert "物理表部分段验收失败" in result[1]


def test_write_parse_diagnostics_empty_list_noop():
    """diagnostics 为空时不修改 chunk payload。"""
    from app.core.extractor.scoring.scoring_grid_payload import PAYLOAD_KEY, write_parse_diagnostics

    payload_before = {"version": 1, "grid": [], "table_fingerprint": "z"}
    mock_chunk = SimpleNamespace(metadata={PAYLOAD_KEY: payload_before.copy()})
    write_parse_diagnostics(mock_chunk, [])

    assert "parse_diagnostics" not in mock_chunk.metadata[PAYLOAD_KEY], "空列表不得写入 payload"


def test_25pt_synthetic_rejected_seg_diagnostics_in_parsed():
    """25分合成反例: 被拒段有25分计分行 → parsed.diagnostics 含稳定信号（端到端）。

    流程: build_partial_rejection_diagnostics → write_parse_diagnostics → parse_scoring_chunk_candidate
    验证诊断从 scoring_payload 正确流入最终 parsed.diagnostics。
    """
    from app.core.extractor.scoring.scoring_candidate_pipeline import parse_scoring_chunk_candidate
    from app.core.extractor.scoring.scoring_grid_payload import PAYLOAD_KEY, write_parse_diagnostics
    from app.core.parser.scoring_segment_acceptance import build_partial_rejection_diagnostics
    from app.core.parser.scoring_table_assembler import CanonicalScoringRow, LogicalScoringSegment

    # 构造被拒段（25分行）
    rejected_seg = LogicalScoringSegment(title="T", header_row=["评分项目", "评分标准", "分值"])
    rejected_seg.source_keys = {(1, 0)}
    rejected_seg.page_start = 1
    rejected_seg.page_end = 1
    rejected_seg.rows.append(
        CanonicalScoringRow(group="", factor="技术优化", rule="优秀得25分",
                            score_text="25", score=25.0, page=1)
    )

    accepted_seg = SimpleNamespace(source_keys={(1, 0), (2, 0)}, scored_row_count=5,
                                   page_start=2, page_end=3)
    span_results = [({(1, 0), (2, 0)}, object()), ({(1, 0)}, None)]
    seg_chunks = [(accepted_seg, object()), (rejected_seg, None)]

    # 计算诊断
    diags = build_partial_rejection_diagnostics(span_results, seg_chunks)
    assert diags, "前置条件: 诊断不为空"

    # 写入接受 chunk 的 payload
    accepted_chunk = SimpleNamespace(
        content=(
            "| 评分项目 | 评分标准 | 分值 |\n"
            "| --- | --- | --- |\n"
            "| 施工方案 | 方案可行得20分 | 20 |\n"
        ),
        section_path="技术评分",
        metadata={
            PAYLOAD_KEY: {
                "version": 1,
                "grid": [
                    ["评分项目", "评分标准", "分值"],
                    ["施工方案", "方案可行得20分", "20"],
                ],
                "table_fingerprint": "synth01",
            }
        },
    )
    write_parse_diagnostics(accepted_chunk, diags)

    # 解析并断言
    candidate = parse_scoring_chunk_candidate(accepted_chunk)
    combined = " ".join(candidate.parsed.diagnostics)
    assert "物理表部分段验收失败" in combined, (
        f"25分被拒段的诊断信号必须出现在 parsed.diagnostics，实际: {candidate.parsed.diagnostics}"
    )
