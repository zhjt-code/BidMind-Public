# -*- coding: utf-8 -*-
"""评分结构信号快照测试（SchemeReader 阶段0）。

防御目标(映射真实风险):
-: 同构多方案表在装配层被拼接后"合计行/表间章节标题"丢失——快照必须
 在装配前按原始表逐张捕获, 合计行与标题样行逐字在案。
-: 表内卷头行("商务评分（总分32分）")漏识别导致继承污染——快照必须
 把该行逐字保留(表头/合计/骨架任一通道), 供判读器识别卷边界。
"""

from types import SimpleNamespace

import pytest

from app.core.parser.pdf_parser import ParsedDocument, ParsedPage
from app.core.parser.scoring_section_locator import ScoringSection
from app.core.parser.scoring_structure_signals import (
    SIGNALS_VERSION,
    build_scoring_structure_signals,
    summarize_signals_for_log,
)

pytestmark = pytest.mark.scoring


def _tbl(rows, idx=0, page=1):
    return SimpleNamespace(
        table_index=idx,
        page_num=page,
        data=rows,
        row_count=len(rows),
        col_count=max((len(r) for r in rows), default=0),
    )


def _doc(pages):
    return ParsedDocument(
        file_path="test.pdf",
        filename="test.pdf",
        page_count=max((p.page_num for p in pages), default=0),
        pages=pages,
    )


# 形态: 同构多方案表, 合计行与表间标题必须逐张在案 ----------

_COMMERCIAL_ROWS = [
    ["序号", "评分项目", "评分标准", "分值"],
    ["1", "企业资质", "具备相关资质得满分。", "40"],
    ["2", "类似业绩", "每提供一项得10分。", "60"],
    ["", "合计", "", "100"],
]

_TECHNICAL_ROWS = [
    ["序号", "评分项目", "评分标准", "分值"],
    ["1", "技术方案", "方案完整合理得满分。", "50"],
    ["2", "实施计划", "计划可行得满分。", "50"],
    ["", "合计", "", "100"],
]


def _multi_scheme_doc():
    pages = [
        ParsedPage(
            page_num=10,
            text="5.2.1 商务评审\n商务评审满分100分。",
            tables=[_tbl(_COMMERCIAL_ROWS, idx=0, page=10)],
        ),
        ParsedPage(
            page_num=11,
            text="5.2.2 技术评审\n技术评审满分100分。",
            tables=[_tbl(_TECHNICAL_ROWS, idx=0, page=11)],
        ),
    ]
    return _doc(pages)


def test_captures_each_raw_table_separately_with_total_rows():
    """ 核心: 两张同构表必须作为两个独立信号捕获, 各自合计行带 row_index 锚点。"""
    signals = build_scoring_structure_signals(
        _multi_scheme_doc(),
        [ScoringSection("第五章 评标办法", 10, 11, "structure")],
    )

    assert signals is not None
    assert signals["version"] == SIGNALS_VERSION
    tables = signals["tables"]
    assert len(tables) == 2
    assert [t["page"] for t in tables] == [10, 11]
    for t in tables:
        assert len(t["total_rows"]) == 1
        assert "合计100" in t["total_rows"][0]["text"]
        assert t["total_rows"][0]["row_index"] == 3
        assert len(t["header_rows"]) == 1
        assert t["header_rows"][0]["row_index"] == 0


def test_captures_inter_table_section_headings_verbatim():
    """ 核心: 表间章节标题(5.2.1商务评审/5.2.2技术评审)必须逐字捕获。"""
    signals = build_scoring_structure_signals(
        _multi_scheme_doc(),
        [ScoringSection("第五章 评标办法", 10, 11, "structure")],
    )

    heading_texts = [h["text"] for h in signals["headings"]]
    assert "5.2.1 商务评审" in heading_texts
    assert "5.2.2 技术评审" in heading_texts
    pages_of = {h["text"]: h["page"] for h in signals["headings"]}
    assert pages_of["5.2.1 商务评审"] == 10
    assert pages_of["5.2.2 技术评审"] == 11


# 形态: 表内卷头行与分值构成声明必须在案 ----------

_VOLUME_HEADER_ROWS = [
    ["评分因素", "评分标准", "分值"],
    ["技术评分（总分53分）", "", ""],
    ["技术方案", "方案完整得满分。", "53"],
    ["商务评分（总分32分）", "", ""],
    ["综合能力", "综合实力强得满分。", "32"],
]


def test_captures_in_table_volume_header_row_verbatim():
    """ 核心: 卷头行"商务评分（总分32分）"必须逐字保留在快照中(骨架通道)。"""
    doc = _doc(
        [
            ParsedPage(
                page_num=20,
                text="第四章 评标办法\n分值构成：技术标53分，商务标32分，价格标15分。",
                tables=[_tbl(_VOLUME_HEADER_ROWS, page=20)],
            )
        ]
    )
    signals = build_scoring_structure_signals(
        doc, [ScoringSection("第四章 评标办法", 20, 20, "structure")]
    )

    table = signals["tables"][0]
    factors = [r["factor"] for r in table["rows"]]
    assert any("商务评分（总分32分）" in f for f in factors)
    assert any("技术评分（总分53分）" in f for f in factors)
    # 卷头行锚点可回查(row_index 指向原始表行)
    volume_row = next(r for r in table["rows"] if "商务评分" in r["factor"])
    assert volume_row["row_index"] == 3


def test_captures_composition_declaration_lines():
    """分值构成声明句必须捕获(关键词通道兜底, 不依赖可解析性)。"""
    doc = _doc(
        [
            ParsedPage(
                page_num=20,
                text="第四章 评标办法\n分值构成：技术标53分，商务标32分，价格标15分。",
                tables=[_tbl(_VOLUME_HEADER_ROWS, page=20)],
            )
        ]
    )
    signals = build_scoring_structure_signals(
        doc, [ScoringSection("第四章 评标办法", 20, 20, "structure")]
    )

    decl_texts = [d["text"] for d in signals["declarations"]]
    assert any("分值构成" in t for t in decl_texts)
    assert all(d["page"] == 20 for d in signals["declarations"])


# ---------- 边界与降级 ----------

def test_returns_none_without_sections():
    signals = build_scoring_structure_signals(_multi_scheme_doc(), [])
    assert signals is None
    assert build_scoring_structure_signals(_multi_scheme_doc(), None) is None


def test_returns_none_when_scope_has_no_tables_or_declarations():
    doc = _doc([ParsedPage(page_num=5, text="普通章节内容，无评分要素。", tables=[])])
    signals = build_scoring_structure_signals(
        doc, [ScoringSection("普通章节", 5, 5, "structure")]
    )
    assert signals is None


def test_marks_sparse_text_pages():
    """文本稀疏页(疑似扫描件)必须打标, 供判读器降置信。"""
    doc = _doc(
        [
            ParsedPage(
                page_num=10,
                text="短",
                tables=[_tbl(_COMMERCIAL_ROWS, page=10)],
            )
        ]
    )
    signals = build_scoring_structure_signals(
        doc, [ScoringSection("评标办法", 10, 10, "structure")]
    )
    flags = {p["page"]: p["text_sparse"] for p in signals["pages"]}
    assert flags[10] is True


def test_row_skeleton_captures_factor_and_score():
    signals = build_scoring_structure_signals(
        _multi_scheme_doc(),
        [ScoringSection("第五章 评标办法", 10, 11, "structure")],
    )
    first_table = signals["tables"][0]
    detail_rows = first_table["rows"]
    by_factor = {r["factor"]: r for r in detail_rows}
    assert "1" in by_factor or any("企业资质" in f for f in by_factor)
    scored = [r for r in detail_rows if r["score"]]
    assert scored, "行骨架必须捕获到分值格"


def test_skeleton_row_cap_enforced():
    """行骨架数量封顶, 防止超大表撑爆快照。"""
    big_rows = [["序号", "评分项目", "评分标准", "分值"]] + [
        [str(i), f"评分项{i}", "规则文本。", "1"] for i in range(1, 200)
    ]
    doc = _doc(
        [ParsedPage(page_num=10, text="评标办法", tables=[_tbl(big_rows, page=10)])]
    )
    signals = build_scoring_structure_signals(
        doc, [ScoringSection("评标办法", 10, 10, "structure")]
    )
    assert len(signals["tables"][0]["rows"]) <= 80


# Task9(anchor诊断): 拆行标题确定性合并 ----------


def _single_page_doc(page_num: int, text: str, rows=None):
    return _doc(
        [
            ParsedPage(
                page_num=page_num,
                text=text,
                tables=[_tbl(rows or _COMMERCIAL_ROWS, page=page_num)],
            )
        ]
    )


def test_2308_p3_split_technical_heading_is_merged():
    """真实诊断(bug-721-anchor-diagnostic.md)复现: p3"2.2.4 项目管理 (3分)"
    与紧邻下一行"大纲评分 控制措施不力,..."必须合并为一条完整标题。"""
    doc = _single_page_doc(
        3,
        "2.2.4 项目管理 (3分)\n大纲评分 控制措施不力,管理流程不当,可操作性差 1.80-2.10",
    )
    signals = build_scoring_structure_signals(
        doc, [ScoringSection("评标办法", 3, 3, "structure")]
    )
    heading_texts = [h["text"] for h in signals["headings"]]
    assert "2.2.4 项目管理 (3分) 大纲评分" in heading_texts


def test_merged_heading_excludes_following_rule_text():
    """合并结果只取标题词结束前的前缀, 不得带上"控制措施不力"等规则正文,
    且长度不超过40字符。"""
    doc = _single_page_doc(
        3,
        "2.2.4 项目管理 (3分)\n大纲评分 控制措施不力,管理流程不当,可操作性差 1.80-2.10",
    )
    signals = build_scoring_structure_signals(
        doc, [ScoringSection("评标办法", 3, 3, "structure")]
    )
    merged = next(h["text"] for h in signals["headings"] if h["text"].startswith("2.2.4"))
    assert "控制措施不力" not in merged
    assert len(merged) <= 40
    # 原始两行各自的完整语义不应重复出现在headings里
    assert "大纲评分 控制措施不力,管理流程不当,可操作性差 1.80-2.10" not in heading_texts_of(signals)


def heading_texts_of(signals):
    return [h["text"] for h in signals["headings"]]


def test_numbering_line_with_distant_term_in_long_body_not_merged():
    """普通编号行+下一行远端(>20字符)才出现"评分"的长正文不得合并。"""
    far_text = "占位占位占位占位占位占位占位占位占位占位评分补充说明"
    doc = _single_page_doc(3, f"2.2.5 我的标题\n{far_text}")
    signals = build_scoring_structure_signals(
        doc, [ScoringSection("评标办法", 3, 3, "structure")]
    )
    heading_texts = heading_texts_of(signals)
    assert "2.2.5 我的标题" in heading_texts
    assert not any(t.startswith("2.2.5 我的标题 ") for t in heading_texts)


def test_numbering_and_term_already_on_same_line_not_affected_by_merge():
    """当前行本身已含标题词时不触发合并逻辑(既有"编号+评审词"一级证据保持不变)。"""
    doc = _single_page_doc(3, "5.2.1 商务评审\n技术评审满分100分。")
    signals = build_scoring_structure_signals(
        doc, [ScoringSection("评标办法", 3, 3, "structure")]
    )
    heading_texts = heading_texts_of(signals)
    assert "5.2.1 商务评审" in heading_texts


def test_summarize_signals_for_log():
    assert summarize_signals_for_log(None) == "无信号"
    signals = build_scoring_structure_signals(
        _multi_scheme_doc(),
        [ScoringSection("第五章 评标办法", 10, 11, "structure")],
    )
    summary = summarize_signals_for_log(signals)
    assert "tables=2" in summary
