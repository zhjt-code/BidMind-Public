"""装配前声明样表行通道防御测试（U7 证据来源，signals v2）。

护栏对象是"取材粒度"这一决策本身:
- 捕获侧: 逐字保留整行（含单元格切分），不得回退为行骨架/截断
- 消费侧: 必须按精简网格喂入 capture，不得改走 supplementary_pages
- 多 scope: 无 scope 归属证据不广播，且必须留诊断（不静默丢弃）

背景: 这类声明形态是"标题格 + 内容格"两格一行，如
`分值构成（总分100分）` ‖ `商务部分：25分技术服务部分：45分投标报价：30分`。
装配层会把它拆散，chunk 网格取不到；行骨架把整行压成"首格前20字+末个分值格"，
内容格整体丢失；正文声明通道按 120 字截断，尾部若干"标签:分值"对被切掉。
"""
from __future__ import annotations

import types

import pytest

from app.core.extractor.scoring.ledger.contracts import LedgerItemEntry
from app.core.extractor.scoring.ledger.declaration_capture import (
    capture_scoring_declarations,
    declaration_grids_from_signals,
)
from app.core.extractor.scoring.ledger.scoped_reconciliation import (
    ScopedLedgerRequest,
    coordinate_scoped_ledger,
)
from app.core.parser.scoring_structure_signals import (
    SIGNALS_VERSION,
    build_scoring_structure_signals,
)

pytestmark = pytest.mark.scoring

# 实测形态（sample_corpus/03 p1）：标题格 + 内容格两格一行
DECL_CELLS = [
    "2.2.1",
    "分值构成（总分100分）",
    "商务部分：25分技术服务部分：45分投标报价：30分",
]


def _page(page_num: int, text: str, tables: list):
    return types.SimpleNamespace(page_num=page_num, text=text, tables=tables)


def _table(rows: list[list[str]], table_index: int = 0):
    return types.SimpleNamespace(
        data=rows, table_index=table_index, row_count=len(rows), col_count=0
    )


def _section(page_start: int = 1, page_end: int = 1):
    return types.SimpleNamespace(
        title="评标办法", page_start=page_start, page_end=page_end
    )


class TestCaptureSide:
    def test_declaration_row_kept_verbatim_with_cell_splits(self):
        """整行逐字保留，单元格切分不得合并——拼行解析依赖原切分。"""
        doc = types.SimpleNamespace(
            pages=[_page(1, "评标办法", [_table([["表头"], DECL_CELLS])])]
        )
        signals = build_scoring_structure_signals(doc, [_section()])

        rows = (signals or {}).get("declaration_table_rows") or []
        assert len(rows) == 1
        assert rows[0]["cells"] == DECL_CELLS
        assert rows[0]["page"] == 1
        assert rows[0]["row_index"] == 1

    def test_content_cell_is_not_truncated(self):
        """内容格不得截断——尾部"标签:分值"对被切掉正是本通道要修的缺陷。

        行骨架通道把首格压到 20 字，正文声明通道按 120 字截断；
        本通道若沿用任一上限，最后几个组声明就会静默丢失。
        """
        long_cells = ["分值构成", "甲：10分乙：10分丙：10分丁：10分戊：10分"
                      "己：10分庚：10分辛：10分壬：10分癸：10分"]
        doc = types.SimpleNamespace(
            pages=[_page(1, "评标办法", [_table([long_cells])])]
        )
        signals = build_scoring_structure_signals(doc, [_section()])

        kept = ((signals or {}).get("declaration_table_rows") or [])[0]["cells"][1]
        assert kept == long_cells[1]
        assert "癸：10分" in kept

    def test_rows_without_trigger_term_are_not_captured(self):
        """无构成触发词的行不进本通道——判据用既有生产常量，不新增调参面。"""
        doc = types.SimpleNamespace(
            pages=[_page(1, "评标办法", [_table([["评分项", "分值"], ["技术方案", "30"]])])]
        )
        signals = build_scoring_structure_signals(doc, [_section()])
        assert not ((signals or {}).get("declaration_table_rows") or [])

    def test_signals_version_bumped(self):
        """新增通道必须伴随版本号推进，供存量快照识别。"""
        assert SIGNALS_VERSION >= 2


class TestConsumeSide:
    def test_grids_from_signals_yield_one_grid_per_row(self):
        """每行独立成单行网格——行间无结构关系，合成多行会误导列角色推断。"""
        signals = {"declaration_table_rows": [
            {"page": 2, "cells": DECL_CELLS},
            {"page": 5, "cells": ["分值构成", "甲：50分乙：50分"]},
        ]}
        grids = declaration_grids_from_signals(signals)

        assert [p for p, _ in grids] == [2, 5]
        assert grids[0][1] == [DECL_CELLS]
        assert len(grids[0][1]) == 1

    @pytest.mark.parametrize(
        "signals",
        [None, {}, {"declaration_table_rows": None}, {"declaration_table_rows": "x"},
         {"declaration_table_rows": [{"cells": []}, {"cells": ["  "]}, "bad"]}],
    )
    def test_malformed_signals_yield_empty(self, signals):
        """快照缺失/畸形一律返回空，确认页主链不受影响。"""
        assert declaration_grids_from_signals(signals) == []

    def test_declaration_grid_produces_group_pairs(self):
        """精简网格喂入后必须解析出逐个组声明对（决策依据的核心断言）。

        实测按 supplementary_pages 喂入时挽回为零——该通道只跑
        `_weighted_share_pairs`，不跑平铺解析；故来源决策连带定死了消费形态。
        """
        grids = declaration_grids_from_signals(
            {"declaration_table_rows": [{"page": 1, "cells": DECL_CELLS}]}
        )
        capture = capture_scoring_declarations([(1, "")], grids)

        got = {(p.norm_label, p.score) for p in capture.pairs}
        assert ("商务", 25.0) in got
        assert ("投标报价", 30.0) in got
        assert sum(p.score for p in capture.pairs) == 100.0


class TestMultiScopePolicy:
    def _request(self, entries, declaration_grids):
        return ScopedLedgerRequest(
            pages=((1, ""),), chunk_scopes=((None, None),), grids=(), grid_scopes=(),
            entries_with_rules=tuple((e, "") for e in entries),
            declaration_grids=tuple(declaration_grids),
        )

    def test_single_scope_consumes_declaration_grids(self):
        entries = [
            LedgerItemEntry(name="商务响应", score=25.0, volume_label="商务部分"),
            LedgerItemEntry(name="技术响应", score=45.0, volume_label="技术服务部分"),
            LedgerItemEntry(name="报价", score=30.0, volume_label="投标报价"),
        ]
        grids = declaration_grids_from_signals(
            {"declaration_table_rows": [{"page": 1, "cells": DECL_CELLS}]}
        )
        report = coordinate_scoped_ledger(self._request(entries, grids))
        assert report.group_rows, "单 scope 下声明表行必须进入组行"

    def test_multi_scope_discards_but_diagnoses(self):
        """无 scope 归属证据在多 scope 下不广播，但必须留诊断（禁止静默丢弃）。

        广播会让某一 scope 的声明去和另一 scope 的提取项对账，
        与 declaration_pages 同源政策。
        """
        entries = [
            LedgerItemEntry(name="甲", score=50.0, volume_label="", lot="lot_A"),
            LedgerItemEntry(name="乙", score=50.0, volume_label="", lot="lot_B"),
        ]
        grids = declaration_grids_from_signals(
            {"declaration_table_rows": [{"page": 1, "cells": DECL_CELLS}]}
        )
        report = coordinate_scoped_ledger(self._request(entries, grids))

        assert any("已丢弃 1 行声明表" in d for d in report.diagnostics)
        assert not report.group_rows
