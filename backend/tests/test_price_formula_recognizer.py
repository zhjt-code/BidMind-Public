""" 防御测试: 价格公式段落识别器。

映射真实问题: 项目1550的价格评分是纯段落公式(29页), 不在 scoring_table 中;
必须用"标题引导 + 得分公式语义 + 卷定义权威满分"三元判据识别, 严禁从公式自由猜满分。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.scoring.scoring_table_parser import parse_scoring_table  # noqa: E402
from app.core.parser.lot_boundary_locator import LotBoundary  # noqa: E402
from app.core.parser.pdf_parser import ParsedDocument, ParsedPage  # noqa: E402
from app.core.parser.price_formula_recognizer import recognize_price_formula_sections  # noqa: E402
from app.core.parser.scoring_section_locator import ScoringSection  # noqa: E402


VOLUME_CONTEXT_1550 = (
    "四、分值构成(权重)(详见公告中附件) "
    "分技术、商务、价格三个部分分别进行量化评审,每个部分满分均为100分,并按分值合计计算最终量化评审得分。"
)

PRICE_FORMULA_TEXT_1550 = (
    "(3)报价评分标准报价评分标准1.区间复合一次平均价法计算进入详评的投标人评标价的算术平均值 A1;"
    "再剔除评标价与算术平均值 A1 偏差超过区间的投标人报价,计算一次平均价 A2,以 A2×(1+c)为基准价;"
    "价格部分得分=100-100×n×|投标人的评标总价-基准价|/基准价;"
    "当投标人的评标总价>=基准价,n=n1;当投标人的评标总价<基准价,n=n2;"
    "当计算出的价格部分得分<0,按0分计。"
)


def _doc(pages: list[ParsedPage]) -> ParsedDocument:
    return ParsedDocument(
        file_path="x.pdf",
        filename="x.pdf",
        page_count=max(p.page_num for p in pages),
        pages=pages,
    )


def test_recognizes_1550_price_formula_text_as_price_scoring_chunk():
    pages = [
        ParsedPage(page_num=21, text=VOLUME_CONTEXT_1550),
        ParsedPage(page_num=29, text=PRICE_FORMULA_TEXT_1550),
    ]
    section = ScoringSection("应答人须知前附表附件一", 21, 28, "structure")

    recognized = recognize_price_formula_sections(_doc(pages), [section], VOLUME_CONTEXT_1550)

    assert len(recognized) == 1
    item = recognized[0]
    assert item.page_num == 29
    assert item.score == 100.0
    assert "价格部分得分" in item.markdown
    parsed = parse_scoring_table(item.markdown, section_path="应答人须知前附表附件一")
    assert len(parsed.items) == 1
    assert parsed.items[0].volume == "price"
    assert parsed.items[0].score_weight == 100.0
    assert "基准价" in parsed.items[0].description


def test_price_formula_markdown_uses_strict_score_cell() -> None:
    pages = [
        ParsedPage(
            page_num=34,
            text="采购包1:综合评分法。价格项(F1×A1)满分为10.0000分。价格得分=(评审基准价/报价)×标准分值。",
        )
    ]
    lots = [LotBoundary("采购包1", 34, 39)]

    recognized = recognize_price_formula_sections(_doc(pages), lots, "")

    assert "|  | 报价评分 |" in recognized[0].markdown
    assert "| 10分 |" in recognized[0].markdown
    parsed = parse_scoring_table(recognized[0].markdown)
    assert parsed.items[0].score_weight == 10.0


def test_price_formula_requires_authoritative_price_volume_definition():
    pages = [ParsedPage(page_num=29, text=PRICE_FORMULA_TEXT_1550)]
    section = ScoringSection("应答人须知前附表附件一", 21, 28, "structure")

    recognized = recognize_price_formula_sections(_doc(pages), [section], "分值构成详见附件")

    assert recognized == []


def test_fee_rate_table_not_recognized_as_price_formula():
    fee_text = (
        "代理服务收费标准中标金额 货物招标 服务招标费 工程招标(万元) 费率(%) 率(%) 费率(%) "
        "100以下 1.50 1.50 1.00 100~500 1.10 0.80 0.70"
    )
    pages = [ParsedPage(page_num=17, text=fee_text)]
    section = ScoringSection("应答人须知前附表附件一", 15, 18, "structure")

    recognized = recognize_price_formula_sections(_doc(pages), [section], VOLUME_CONTEXT_1550)

    assert recognized == []


def test_negotiation_flow_page_not_recognized_as_price_formula():
    text = "应答人在收到报价邀请后,登陆ECP2.0进行查看并上传报价。谈判和再报价流程在ECP2.0开展。"
    pages = [ParsedPage(page_num=30, text=text)]
    section = ScoringSection("应答人须知前附表附件一", 21, 28, "structure")

    recognized = recognize_price_formula_sections(_doc(pages), [section], VOLUME_CONTEXT_1550)

    assert recognized == []


def test_recognizes_price_formula_per_lot_1552():
    """: 多采购包价格公式按 lot 分别识别, 每包至多一项。"""
    pages = [
        ParsedPage(
            page_num=34,
            text=(
                "采购包1:综合评分法。价格项(F1×A1)满分为30.0000分。"
                "价格得分=(评审基准价/报价)×标准分值。"
            ),
        ),
        ParsedPage(
            page_num=40,
            text=(
                "采购包2:综合评分法。价格项(F1×A1)满分为10.0000分。"
                "价格得分=(评审基准价/报价)×标准分值。"
            ),
        ),
    ]
    lots = [
        LotBoundary("采购包1", 34, 39),
        LotBoundary("采购包2", 40, 45),
    ]

    recognized = recognize_price_formula_sections(_doc(pages), lots, "")

    assert len(recognized) == 2
    assert [(item.page_num, item.score) for item in recognized] == [(34, 30.0), (40, 10.0)]
    assert "· 采购包1" in recognized[0].markdown.splitlines()[0]
    assert "· 采购包2" in recognized[1].markdown.splitlines()[0]
    parsed = [parse_scoring_table(item.markdown).items[0] for item in recognized]
    assert [(item.lot, item.volume, item.score_weight) for item in parsed] == [
        ("采购包1", "price", 30.0),
        ("采购包2", "price", 10.0),
    ]


def test_price_formula_per_lot_requires_explicit_full_score():
    """护栏: 多包公式若无“价格项满分为N分”声明, 不从公式猜分。"""
    pages = [
        ParsedPage(page_num=34, text="采购包1:综合评分法。价格得分=(评审基准价/报价)×标准分值。")
    ]
    lots = [LotBoundary("采购包1", 34, 39)]

    assert recognize_price_formula_sections(_doc(pages), lots, "") == []


# ---------------------------------------------------------------------------
# R6 回归防御: _resolve_price_score_strict 按证据可信度分级
#
# 根因: inline/vol_def 冲突时全量 fail-closed 会抑制整个 chunk 生成;
# 在多文档/多包场景中 vol_def 可能被跨范围值污染(如 merged corpus 中
# sample10 inline=10 但先前样本累积 vol_def=40), 导致合法评分事实静默丢失。
# 分级规则: 冲突仅来自 vol_def → inline 优先; inline vs axis(同文档双显式
# 证据)矛盾或无 inline → 维持 fail-closed(见 task4b I2 护栏)。
# ---------------------------------------------------------------------------

from app.core.parser.price_formula_recognizer import _resolve_price_score_strict  # noqa: E402

import pytest

pytestmark = pytest.mark.scoring


def test_resolve_strict_single_source_inline():
    """单一来源: 直接使用 inline。"""
    assert _resolve_price_score_strict(10.0, None, None, None, 1) == 10.0


def test_resolve_strict_single_source_vol_def():
    """单一来源: 使用 vol_def。"""
    assert _resolve_price_score_strict(None, None, 40.0, None, 1) == 40.0


def test_resolve_strict_consistent_sources():
    """一致来源: 去重后使用。"""
    assert _resolve_price_score_strict(40.0, None, 40.0, None, 1) == 40.0


def test_resolve_strict_inline_wins_over_vol_def_conflict():
    """核心防御: inline(10) vs vol_def(40) 冲突时返回 inline=10, 不 fail-closed。

 复现场景: merged corpus 中 sample10 inline=10, 先前样本累积 vol_def=40;
 旧 fail-closed 行为返回 None 导致 chunk 消失(Task4B 回归)。
 """
    result = _resolve_price_score_strict(10.0, None, 40.0, None, 43)
    assert result == 10.0, (
        f"期望 inline=10.0 优先, 实际得到 {result}; "
        "fail-closed 在跨文档 vol_def 污染场景会静默丢失合法评分事实"
    )


def test_resolve_strict_inline_vs_axis_conflict_stays_fail_closed():
    """inline(30) vs axis(40) 冲突 → fail-closed(同文档双显式证据矛盾, 见 I2 护栏)。"""
    assert _resolve_price_score_strict(30.0, 40.0, None, None, 8) is None


def test_resolve_strict_inline_axis_agree_vol_def_conflict_uses_inline():
    """inline==axis(10) 且 vol_def(40) 冲突 → 双显式证据一致, vol_def 视为污染, 返回 10。"""
    assert _resolve_price_score_strict(10.0, 10.0, 40.0, None, 43) == 10.0


def test_resolve_strict_no_inline_axis_vol_conflict_stays_fail_closed():
    """无 inline 时 axis vs vol_def 冲突 → fail-closed(两来源均为上下文推断, 可信度低)。"""
    assert _resolve_price_score_strict(None, 10.0, 40.0, None, 43) is None


def test_resolve_strict_all_none_returns_none():
    """无任何来源: 返回 None。"""
    assert _resolve_price_score_strict(None, None, None, None, 1) is None


def test_resolve_strict_inline_zero_treated_as_no_source():
    """inline=0 不视为有效来源, 退回 vol_def。"""
    assert _resolve_price_score_strict(0.0, None, 40.0, None, 1) == 40.0
