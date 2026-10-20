""" 防御测试: 逻辑表装配器(表头锚定+表尾封口+接缝修复)。

映射真实风险:
- 1547: 同一评分表被 pdfplumber 抽成 8列/6列两段, 表头在第4行, 接缝续行空名丢 2 分。
- 1546: 同一三列表中间 63-64 页失表退化为段落, 跨页 9 分项(声明在 65 页表首续行)丢失。
锁定: 装配器必须把它们重建为单一逻辑表段, forward-fill 空名、桥接失表页、
显式"满分N分"声明补分, 且不认领无表头签名的表(留给 legacy 路径)。
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
    assemble_scoring_segments,
    render_assembled_segment_markdown,
)

import pytest

pytestmark = pytest.mark.scoring


def _tbl(data: list[list[str]], idx: int = 0):
    return SimpleNamespace(data=data, table_index=idx)


def _doc(pages: list[ParsedPage]) -> ParsedDocument:
    return ParsedDocument(
        file_path="x.pdf",
        filename="x.pdf",
        page_count=max(p.page_num for p in pages),
        pages=pages,
    )


# ---------------------------------------------------------------------------
# 1547 形态: 8列/6列宽度抖动 + 表头第4行 + 接缝续行空名
# ---------------------------------------------------------------------------

_P67_TABLE_8COL = [
    ["", "", "", "", "", "", "", "（9）投标人按招标文件要求提供有效承诺书；（16）提供有效承诺函。"],
    ["", "", "", "", "", "其他", "", "（1）符合招标文件其他实质性要求。"],
    ["2.2.1", "", "", "分值构成（总分100分）", "", "", "", "商务技术评审部分： 20 分； 价格评审部分： 80 分。"],
    ["条款号", "", "", "评审 内容", "评分因 素", "", "分值", "评分标准"],
    ["2 . 2 . 2", "详 细 评 审 标 准", "", "商务 技术 评审", "管理体 系认证", "", "1", "投标人具备有效的质量管理体系认证证书、职业健康安全管理体系认证证书、环境管理体系认证证书，全部具备得1分，缺任意一项不得分。"],
    ["", "", "", "", "企业业 绩", "", "2", "根据投标人2022年以来同类施工业绩合同累计金额进行评分：（1）合同累计金额≥1.4亿元，得2分；（6）合同累计金额≤2000万元不得分。本项满分2分。"],
]

_P68_TABLE_6COL = [
    ["", "", "", "", "", "分； （5）2000万元＜合同累计金额≤5000万元，得0.4分；（6）合同累计金额≤2000万元不得分。本项满分2分。需按招标文件要求提供有效业绩证明材料。"],
    ["", "", "", "", "2", "根据投标人2022年以来同类施工业绩数量进行评分：每提供一个单个合同含税金额不低于2000万元的业绩合同得0.5分，满分2分。"],
    ["", "", "", "团队人 员能力", "2", "根据投标人拟投入项目的人员职称证书情况进行评分，本项最高得1分。"],
    ["", "", "", "财务 状况", "2", "根据2023年财务报表净利润进行评分，前30%有效投标人得满分1分。"],
    ["", "", "", "履约 评价", "3", "根据考核得分由高到低排名，排名前30%（含）得3分。"],
    ["", "", "", "施工质 量保障 措施", "3", "根据施工质量保障措施进行评分，内容全面、合理、针对性强，得[2,3]分。"],
    ["", "", "", "施工安 全保障 措施", "3", "根据施工安全保障措施进行评分，内容全面、合理、针对性强，得[2,3]分。"],
    ["", "", "", "施工进 度保障 措施", "2", "根据施工进度保障措施进行评分，内容全面得[1.3,2]分。"],
    ["", "", "价格 评审", "基准价 法", "80", "所有有效投标人中，去掉一定数量的最高评标价和最低评标价后计算基准价，按公式计算得分。"],
]


def test_assembles_1547_split_width_tables_into_single_segment():
    pages = [
        ParsedPage(page_num=67, text="评标办法前附表", tables=[_tbl(_P67_TABLE_8COL)]),
        ParsedPage(page_num=68, text="评标办法续", tables=[_tbl(_P68_TABLE_6COL)]),
    ]
    section = ScoringSection("评标办法前附表", 67, 68, "structure")

    segments, claimed = assemble_scoring_segments(_doc(pages), section)

    scored = [s for s in segments if s.scored_row_count > 0]
    assert len(scored) == 1
    seg = scored[0]
    assert seg.page_start == 67 and seg.page_end == 68
    # 两个物理表都被认领, legacy 路径不得重复处理
    assert len(claimed) == 2

    factors = [r.factor.replace(" ", "") for r in seg.rows if r.score_text]
    scores = [r.score for r in seg.rows if r.score_text]
    # 接缝续行空名 forward-fill: 第二条企业业绩(按数量计分)不得丢失,
    # 且同名兄弟子行须序号消歧防止身份键碰撞被表内去重误并
    business_rows = [f for f in factors if f.startswith("企业业绩")]
    assert len(business_rows) == 2
    assert len(set(business_rows)) == 2
    assert sum(scores) == 100.0
    # 分组继承: 基准价法归价格评审组
    price_row = next(r for r in seg.rows if r.factor.replace(" ", "") == "基准价法")
    assert "价格" in price_row.group.replace(" ", "")
    # 分值构成行进入 prelude, 不作为评分行
    assert any("分值构成" in t for t in seg.prelude_texts)
    assert all("分值构成" not in r.factor for r in seg.rows)


def test_renders_canonical_markdown_for_assembled_segment():
    pages = [
        ParsedPage(page_num=67, text="评标办法前附表", tables=[_tbl(_P67_TABLE_8COL)]),
        ParsedPage(page_num=68, text="评标办法续", tables=[_tbl(_P68_TABLE_6COL)]),
    ]
    section = ScoringSection("评标办法前附表", 67, 68, "structure")
    segments, _ = assemble_scoring_segments(_doc(pages), section)
    seg = next(s for s in segments if s.scored_row_count > 0)

    md = render_assembled_segment_markdown(seg)

    assert "[评分表 第67-68页]" in md
    assert "| 分组 | 评分因素 | 评分内容与标准 | 分值 |" in md
    assert "分值构成" in md  # prelude 行保留供组基准解析
    assert md.count("企业业") >= 2


# ---------------------------------------------------------------------------
# 1546 形态: 中间页失表退化为段落, 跨页声明 9 分
# ---------------------------------------------------------------------------

_P59_TABLE_3COL = [
    ["项目", "评 定 内 容 及 标 准", "得分"],
    ["一、价格部分", "", "20"],
    ["价格", "以满足招标文件要求的最低投标报价为评标基准价，得满分 20 分，其他投标报价得分=（评标基准价/其他投标报价）×20。", "20"],
    ["二、商务部分", "", "13"],
    ["合同案例", "投标人提供投标截止时间前三年完成的类似业绩，每提供一个签署的合同案例得 2 分，最高得 4 分。", "4"],
    ["投标人能力", "每提供一个云安全或服务能力相关认证得 0.5 分，最高得 3 分。", "3"],
    ["团队人员", "项目负责人证书得 2 分；团队成员每有一个证书得 1 分，满分 4 分。", "6"],
    ["三、技术部分", "", "42"],
    ["项目需求理解", "对项目现状、网络组网、存储和业务系统需求逐条阐述，满分 8 分。", "8"],
    ["项目规划设计", "提供整体架构、网络架构、计算存储规划和应用系统规划，满分 8 分。", "8"],
    ["数据中心及网络设计", "提供数据中心、网络、数据及存储设计方案，满分 6 分。", "6"],
    ["应用系统设计", "提供应用系统功能详细方案，满分 10 分。", "10"],
    ["性能与配置", "根据投标产品资料完整性、性能稳定性等综合打分，满分 10 分。", "10"],
]

_P63_TEXT = (
    "投标人使用已有案例或真实系统进行演示，系统功能演示时间为 15 分钟，"
    "投标单位需通过现场真实系统操作的方式完成以下系统功能内容演示。"
    "一、大数据管理平台：数据采集流程设计提供可视化流程设计器。"
)
_P64_TEXT = (
    "注：每项内容演示详尽，功能链路能够形成闭环，支撑充分，功能演示流畅，每项得 3 分；"
    "功能演示基本流畅，系统响应略有卡顿，功能模块有缺失的，每项得 2 分；功能演示不流畅，系统操作繁琐，系"
)

_P65_TABLE_3COL = [
    ["", "统响应卡顿，功能模块缺失的，得每项 1 分, 未提供的不得分，共三项满分 9 分。 注：（1）建议采用实际上线的系统录制演示视频。", ""],
    ["项目实施方案", "投标人根据项目实际情况提供项目实施方案，内容至少包括项目实施计划、开发流程管理、测试与验收方案，满分 6 分。", "6"],
    ["售后服务方案", "投标人根据项目实际情况提供售后服务方案，内容至少包括售后服务体系、服务内容和管理制度，满分 6 分。", "6"],
    ["风险应对措施", "投标人根据项目实际情况提供风险应对措施方案，内容至少包括风险评估和突发事件处置方案，满分 4 分。", "4"],
]


def test_bridges_table_loss_pages_and_synthesizes_declared_score():
    pages = [
        ParsedPage(page_num=59, text="第三章 评标标准和评标方法", tables=[_tbl(_P59_TABLE_3COL)]),
        ParsedPage(page_num=63, text=_P63_TEXT, tables=[]),
        ParsedPage(page_num=64, text=_P64_TEXT, tables=[]),
        ParsedPage(page_num=65, text="评分表续页", tables=[_tbl(_P65_TABLE_3COL)]),
    ]
    section = ScoringSection("第三章 评标标准和评标方法", 59, 66, "structure")

    segments, claimed = assemble_scoring_segments(_doc(pages), section)

    scored = [s for s in segments if s.scored_row_count > 0]
    assert len(scored) == 1
    seg = scored[0]
    assert seg.page_start == 59 and seg.page_end == 65
    assert len(claimed) == 2  # 两张物理表同属一个逻辑表

    scores = [r.score for r in seg.rows if r.score_text]
    # 9 分项经显式"共三项满分9分"声明恢复, 全表合计 100
    assert 9.0 in scores
    assert sum(scores) == 100.0
    synthesized = next(r for r in seg.rows if r.score == 9.0)
    assert "满分 9 分" in synthesized.rule or "满分9分" in synthesized.rule.replace(" ", "")
    # 小计行(一、价格部分/二、商务部分/三、技术部分)不产评分行, 仅作分组
    factors = [r.factor for r in seg.rows]
    assert all("价格部分" not in f and "商务部分" not in f and "技术部分" not in f for f in factors)
    price_row = next(r for r in seg.rows if r.factor == "价格")
    assert "价格部分" in price_row.group


def test_gap_text_without_explicit_declaration_is_dropped_not_glued():
    """失表页文本无显式满分声明时整块丢弃并记录, 不得把整页文本粘进上一行描述。"""
    pages = [
        ParsedPage(page_num=59, text="第三章 评标标准和评标方法", tables=[_tbl(_P59_TABLE_3COL)]),
        ParsedPage(page_num=63, text="本页为系统功能清单说明，不含任何评分规则。", tables=[]),
        ParsedPage(
            page_num=65,
            text="评分表续页",
            tables=[_tbl(_P65_TABLE_3COL[1:])],  # 无声明续行, 直接数据行
        ),
    ]
    section = ScoringSection("第三章 评标标准和评标方法", 59, 66, "structure")

    segments, _ = assemble_scoring_segments(_doc(pages), section)
    seg = next(s for s in segments if s.scored_row_count > 0)

    last_row_before_gap = next(r for r in seg.rows if r.factor == "性能与配置")
    assert "系统功能清单" not in last_row_before_gap.rule
    assert all(r.score != 9.0 for r in seg.rows)


def test_trailing_gap_prose_after_table_never_synthesizes():
    """段尾散文页(其后无表格行跟随)即使含"共N项满分M分"也不得合成评分行。"""
    trailing_prose = (
        "评标办法说明：演示环节共三项满分 9 分，由评标委员会按现场演示情况打分。"
        "本节其余内容为程序性说明。"
    )
    pages = [
        ParsedPage(page_num=59, text="第三章 评标标准和评标方法", tables=[_tbl(_P59_TABLE_3COL)]),
        ParsedPage(page_num=63, text=trailing_prose, tables=[]),
    ]
    section = ScoringSection("第三章 评标标准和评标方法", 59, 66, "structure")

    segments, _ = assemble_scoring_segments(_doc(pages), section)
    seg = next(s for s in segments if s.scored_row_count > 0)

    assert all(r.score != 9.0 for r in seg.rows)
    assert sum(r.score for r in seg.rows if r.score is not None) == 75.0


# ---------------------------------------------------------------------------
# 负例: 不认领 / 无分段
# ---------------------------------------------------------------------------

def test_headerless_table_left_unclaimed_for_legacy():
    """无表头签名(elid 乱码首行)的表不认领, 交 legacy 路径(保 C 救回)。"""
    rows = [
        ["elid= 2740 45&s earch word", "", "", "", ""],
        ["维保 要求 （2% ）", "维保期 限", "在1年免费维保期的基础上，每增加1年得1分，满分2分。", "客观", "2.0"],
        ["售后 承诺 (4%)", "维保费 用", "根据承诺出保后的年维保费用占应答报价的比例进行评分。", "客观", "4.0"],
    ]
    pages = [ParsedPage(page_num=34, text="评分表", tables=[_tbl(rows)])]
    section = ScoringSection("评分表", 34, 34, "structure")

    segments, claimed = assemble_scoring_segments(_doc(pages), section)

    assert segments == []
    assert claimed == set()


def test_compliance_segment_has_zero_scored_rows():
    """符合性表段(有表头签名但无分值列)应产出零分值行段, 由 builder 经 detector 拒绝。"""
    rows = [
        ["序号", "评审大类", "二级评审子类", "主要内容"],
        ["1", "响应性评审", "应答人名称", "与营业执照、资质证书或其他组织证明一致。"],
        ["2", "", "应答函", "按询比文件要求提供。"],
    ]
    pages = [ParsedPage(page_num=26, text="初步评审", tables=[_tbl(rows)])]
    section = ScoringSection("初步评审", 26, 27, "structure")

    segments, claimed = assemble_scoring_segments(_doc(pages), section)

    assert len(claimed) == 1
    assert all(s.scored_row_count == 0 for s in segments)
