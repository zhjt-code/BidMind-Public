# -*- coding: utf-8 -*-
"""评分结构判读器测试（SchemeReader 阶段1）。

防御目标(映射真实风险):
-: 判读产物必须经锚点核验, 无法锚定的归属拒收——LLM 幻觉边界不得静默生效。
- fail-open 红线: 信号缺失/LLM失败/校验失败时 ok=False, 消费方回退现行管线,
 不得让判读器成为新的单点故障。
"""

import pytest
from pydantic import ValidationError

from app.core.extractor.scoring.scheme_reader.anchor_verifier import (
    verify_anchor,
    verify_scheme_map_anchors,
)
from app.core.extractor.scoring.scheme_reader.contracts import (
    SchemeMap,
    SignalAnchor,
    SpanAssignment,
)
from app.core.extractor.scoring.scheme_reader.reader import (
    format_signals_block,
    read_scheme_map,
)

pytestmark = pytest.mark.scoring

# 夹具: 形态信号快照(两套同构百分制方案) ----------

SIGNALS_2250 = {
    "version": 1,
    "sections": [{"title": "第五章 评标办法", "page_start": 10, "page_end": 11}],
    "declarations": [],
    "headings": [
        {"page": 10, "text": "5.2.1 商务评审"},
        {"page": 11, "text": "5.2.2 技术评审"},
    ],
    "tables": [
        {
            "page": 10,
            "table_index": 0,
            "row_count": 4,
            "col_count": 4,
            "header_rows": [{"row_index": 0, "cells": ["序号", "评分项目", "评分标准", "分值"]}],
            "total_rows": [{"row_index": 3, "text": "合计100"}],
            "rows": [
                {"row_index": 1, "factor": "1", "score": "40"},
                {"row_index": 2, "factor": "2", "score": "60"},
            ],
        },
        {
            "page": 11,
            "table_index": 0,
            "row_count": 4,
            "col_count": 4,
            "header_rows": [{"row_index": 0, "cells": ["序号", "评分项目", "评分标准", "分值"]}],
            "total_rows": [{"row_index": 3, "text": "合计100"}],
            "rows": [
                {"row_index": 1, "factor": "1", "score": "50"},
                {"row_index": 2, "factor": "2", "score": "50"},
            ],
        },
    ],
    "pages": [
        {"page": 10, "text_sparse": False},
        {"page": 11, "text_sparse": False},
    ],
}


def _scheme_map_2250(**overrides):
    payload = {
        "schemes": [
            {
                "scheme_index": 1,
                "title": "5.2.1 商务评审",
                "total_score": 100,
                "volumes": [
                    {
                        "volume": "commercial",
                        "label": "商务评审",
                        "max_score": 100,
                        "anchor": {
                            "kind": "heading",
                            "page": 10,
                            "quote": "5.2.1 商务评审",
                        },
                    }
                ],
            },
            {
                "scheme_index": 2,
                "title": "5.2.2 技术评审",
                "total_score": 100,
                "volumes": [
                    {
                        "volume": "technical",
                        "label": "技术评审",
                        "max_score": 100,
                        "anchor": {
                            "kind": "heading",
                            "page": 11,
                            "quote": "5.2.2 技术评审",
                        },
                    }
                ],
            },
        ],
        "assignments": [
            {
                "page": 10,
                "table_index": 0,
                "row_start": 1,
                "row_end": 2,
                "scheme_index": 1,
                "volume": "commercial",
                "volume_label": "商务评审",
                "boundary_anchor": {
                    "kind": "heading",
                    "page": 10,
                    "quote": "5.2.1 商务评审",
                },
            },
            {
                "page": 11,
                "table_index": 0,
                "row_start": 1,
                "row_end": 2,
                "scheme_index": 2,
                "volume": "technical",
                "volume_label": "技术评审",
                "boundary_anchor": {
                    "kind": "heading",
                    "page": 11,
                    "quote": "5.2.2 技术评审",
                },
            },
        ],
        "confidence": 0.9,
        "notes": "",
    }
    payload.update(overrides)
    return payload


class _FakeLLM:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    async def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


# ---------- 契约 ----------

def test_scheme_map_contract_rejects_invalid_volume():
    with pytest.raises(ValidationError):
        SpanAssignment(
            page=1, table_index=0, row_start=0, row_end=1,
            scheme_index=1, volume="unknown_volume",
        )


def test_scheme_map_contract_rejects_confidence_out_of_range():
    with pytest.raises(ValidationError):
        SchemeMap(confidence=1.5)


# 锚点核验(: 幻觉边界必须拒收) ----------

def test_verify_anchor_heading_exact_match():
    anchor = SignalAnchor(kind="heading", page=10, quote="5.2.1 商务评审")
    assert verify_anchor(anchor, SIGNALS_2250) is True


def test_verify_anchor_rejects_fabricated_quote():
    anchor = SignalAnchor(kind="heading", page=10, quote="5.9.9 编造的评审标题")
    assert verify_anchor(anchor, SIGNALS_2250) is False


def test_verify_anchor_rejects_wrong_page():
    anchor = SignalAnchor(kind="heading", page=99, quote="5.2.1 商务评审")
    assert verify_anchor(anchor, SIGNALS_2250) is False


def test_verify_anchor_table_row_total():
    anchor = SignalAnchor(
        kind="table_row", page=10, table_index=0, row_index=3, quote="合计100"
    )
    assert verify_anchor(anchor, SIGNALS_2250) is True


def test_verify_anchor_table_row_rejects_missing_table():
    anchor = SignalAnchor(
        kind="table_row", page=10, table_index=7, row_index=3, quote="合计100"
    )
    assert verify_anchor(anchor, SIGNALS_2250) is False


def test_verify_scheme_map_keeps_anchored_assignments():
    scheme_map = SchemeMap(**_scheme_map_2250())
    result = verify_scheme_map_anchors(scheme_map, SIGNALS_2250)
    assert len(result.verified_map.assignments) == 2
    assert result.rejected_assignments == 0
    assert result.effective_confidence == pytest.approx(0.9)


def test_verify_scheme_map_rejects_out_of_range_span():
    payload = _scheme_map_2250()
    payload["assignments"][0]["row_end"] = 99
    result = verify_scheme_map_anchors(SchemeMap(**payload), SIGNALS_2250)
    assert result.rejected_assignments == 1
    assert len(result.verified_map.assignments) == 1
    assert result.effective_confidence < 0.9


def test_verify_scheme_map_rejects_undeclared_scheme_index():
    payload = _scheme_map_2250()
    payload["assignments"][1]["scheme_index"] = 9
    result = verify_scheme_map_anchors(SchemeMap(**payload), SIGNALS_2250)
    assert result.rejected_assignments == 1


def test_quote_mismatch_downgrades_confidence_without_rejection():
    """②(2305形态): 引用非逐字但位置结构有效 → 降置信保留, 不拒收。"""
    payload = _scheme_map_2250()
    payload["assignments"][0]["boundary_anchor"]["quote"] = "编造的边界引用"
    result = verify_scheme_map_anchors(SchemeMap(**payload), SIGNALS_2250)
    assert result.rejected_assignments == 0
    assert len(result.verified_map.assignments) == 2
    assert result.effective_confidence < 0.9
    assert any("降置信" in d for d in result.diagnostics)


def test_volume_anchor_failure_downgrades_confidence_without_rejection():
    payload = _scheme_map_2250()
    payload["schemes"][0]["volumes"][0]["anchor"]["quote"] = "编造的卷声明"
    result = verify_scheme_map_anchors(SchemeMap(**payload), SIGNALS_2250)
    assert result.rejected_assignments == 0
    assert result.effective_confidence < 0.9
    # 失锚卷不得进入 verified_volume_keys(基准注入准入依据)
    assert (1, "commercial") not in result.verified_volume_keys
    assert (2, "technical") in result.verified_volume_keys


def test_verified_volume_keys_cover_anchored_volumes():
    """: 锚点核验通过的卷声明进入 verified_volume_keys 供基准注入准入。"""
    result = verify_scheme_map_anchors(SchemeMap(**_scheme_map_2250()), SIGNALS_2250)
    assert result.verified_volume_keys == {(1, "commercial"), (2, "technical")}


# ---------- 判读器(fail-open 红线) ----------

@pytest.mark.asyncio
async def test_read_scheme_map_happy_path():
    llm = _FakeLLM(response=_scheme_map_2250())
    reading = await read_scheme_map(SIGNALS_2250, llm)
    assert reading.ok is True
    assert len(reading.verified_map.schemes) == 2
    assert len(reading.verified_map.assignments) == 2
    assert reading.effective_confidence == pytest.approx(0.9)
    # 判读调用必须走 chat_json 且温度为0
    assert llm.calls[0]["temperature"] == 0.0


@pytest.mark.asyncio
async def test_read_scheme_map_fail_open_without_signals():
    llm = _FakeLLM(response=_scheme_map_2250())
    assert (await read_scheme_map(None, llm)).ok is False
    assert (await read_scheme_map({}, llm)).ok is False
    assert llm.calls == []


@pytest.mark.asyncio
async def test_read_scheme_map_fail_open_on_llm_error():
    llm = _FakeLLM(error=RuntimeError("503"))
    reading = await read_scheme_map(SIGNALS_2250, llm)
    assert reading.ok is False
    assert any("调用失败" in d for d in reading.diagnostics)


@pytest.mark.asyncio
async def test_read_scheme_map_fail_open_on_invalid_payload():
    llm = _FakeLLM(response={"schemes": "not-a-list"})
    reading = await read_scheme_map(SIGNALS_2250, llm)
    assert reading.ok is False


@pytest.mark.asyncio
async def test_read_scheme_map_fail_open_when_all_assignments_structurally_invalid():
    """位置结构伪造(表不在快照)仍硬拒收; 全部拒收时 fail-open 红线不变。"""
    payload = _scheme_map_2250()
    for a in payload["assignments"]:
        a["page"] = 99
    llm = _FakeLLM(response=payload)
    reading = await read_scheme_map(SIGNALS_2250, llm)
    assert reading.ok is False
    assert reading.rejected_assignments == 2


@pytest.mark.asyncio
async def test_read_scheme_map_passes_feedback_to_prompt():
    llm = _FakeLLM(response=_scheme_map_2250())
    await read_scheme_map(SIGNALS_2250, llm, feedback="commercial 卷差额 32 分")
    user_msg = llm.calls[0]["user_message"]
    assert "commercial 卷差额 32 分" in user_msg
    assert "对账差异" in user_msg


# ---------- 信号压缩 ----------

def test_format_signals_block_contains_boundary_evidence():
    block = format_signals_block(SIGNALS_2250)
    assert "5.2.1 商务评审" in block
    assert "5.2.2 技术评审" in block
    assert "合计行 r3: 合计100" in block
    assert "表 p10-t0" in block
    assert "表 p11-t0" in block


def test_format_signals_block_truncates_long_skeleton():
    signals = dict(SIGNALS_2250)
    signals["tables"] = [
        {
            "page": 10,
            "table_index": 0,
            "row_count": 100,
            "col_count": 4,
            "header_rows": [],
            "total_rows": [],
            "rows": [
                {"row_index": i, "factor": f"评分项{i}", "score": "1"}
                for i in range(1, 100)
            ],
        }
    ]
    block = format_signals_block(signals)
    assert "中段省略" in block
