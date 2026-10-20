"""项目2121/2122/2123评分项提取防回归。

这些用例固定真实样本中暴露的泛化风险:
- 2121: 逻辑表装配漏掉首部评分行, 且 `技术/商务/价格 | 0.x` 权重小表未建模。
- 2122: 价格公式 `M＝50` 被忽略, 扣分步长 `0.2分` 被误作满分; 括号分值跨行断裂漏项。
- 2123: 合并单元格把长汇总规则灌入评分因素列, 真实子项名在规则尾部。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.scoring.score_totals import verify_score_totals  # noqa: E402
from app.core.extractor.scoring.scoring_table_parser import parse_scoring_table  # noqa: E402
from app.core.extractor.scoring.volume_parser import apply_volume_definitions  # noqa: E402
from app.core.parser import ChunkPlanner  # noqa: E402
from app.core.parser.pdf_parser import ParsedDocument, ParsedPage  # noqa: E402
from app.core.parser.scoring_chunk_builder import build_scoring_table_chunks  # noqa: E402
from app.core.parser.scoring_section_locator import ScoringSection  # noqa: E402
from app.core.parser.scoring_table_assembler import (  # noqa: E402
    assemble_scoring_segments,
    segment_to_grid,
)

import pytest

pytestmark = pytest.mark.scoring


PROJECT2121_RAW_ROWS = [
    ["综合评分法"],
    ["技术部分", "0.5"],
    ["商务部分", "0.3"],
    ["价格部分", "0.2"],
    ["综合评分表"],
    ["评审因素", "计分因素", "分值", "计分标准"],
    [
        "技术部分 (F1 、满分为 100 分)",
        "采购需求的响应程度",
        "2 0",
        "根据所投产品对招标文件第五章中采购需求的各项要求响应程度情况进行评审。全部满足计20分。",
    ],
    [
        "",
        "需求理解",
        "6",
        "结合项目采购需求阐述对建设背景、建设需求、建设目标的理解，完整详实计6分。",
    ],
    [
        "",
        "总体技术方案",
        "2 0",
        "提供总体技术方案，内容覆盖全面、针对性强计20分。",
    ],
    [
        "",
        "软件功能技术方案",
        "2 8",
        "围绕14项功能模块提供详细软件功能技术方案，计28分。",
    ],
    [
        "",
        "重难点分析及应对",
        "2 0",
        "分析核心关系、升级改造设计理念和实施路径，计20分。",
    ],
    [
        "",
        "组织实施方案",
        "6",
        "提供人员组织、进度、安装调试、质量保障方案，计6分。",
    ],
    ["商务部分(F2、满分为100分)", "", "", ""],
    ["", "类似业绩", "4 0", "每提供一个类似业绩计20分，最高计40分。"],
    ["", "综合实力1", "2 4", "相关证书每个计8分，最多计24分。"],
    ["", "综合实力2", "1 6", "团队成员证书每类最多8分，本项最多16分。"],
    ["", "售后服务方案", "2 0", "售后服务方案完整详实计20分。"],
    ["价格部分(F3)", "", "", ""],
    ["", "价格评审", "1 0 0", "价格分采用低价优先法，价格分为满分100分。"],
]

PROJECT2121_REAL_FIVE_COLUMN_ROWS = [
    ["综合评分法", "", "", "", ""],
    ["适用范围", "", "", "", "常德市公安局智慧监所实战平台建设项目"],
    ["评审因素及权值", "", "", "", ""],
    ["评审因素", "", "", "", "权值范围"],
    ["技术部分", "", "", "", "0.5"],
    ["商务部分", "", "", "", "0.3"],
    ["价格部分", "", "", "", "0.2"],
    ["综合评分表", "", "", "", ""],
    ["评审因素", "计分因素", "分值", "计分标准", ""],
    [
        "技术部分 (F1 、满分为 100 分)",
        "采购需求的响应程度",
        "2 0",
        "根据采购需求响应程度评审，全部满足计20分。",
        "",
    ],
    [
        "",
        "需求理解",
        "6",
        "阐述建设背景、建设需求和建设目标，完整详实计6分。",
        "",
    ],
    [
        "",
        "总体技术方案",
        "2 0",
        "提供总体技术方案；不满足是指与采购需求不一致等任意一种情形。",
        "",
    ],
    [
        "",
        "软件功能技术方案",
        "2 8",
        "提供软件功能技术方案；不满足是指与采购需求不一致等任意一种情形。",
        "",
    ],
]


PROJECT2122_BROKEN_EMBEDDED_TABLE = """[评分表 第68-75页]
| 条款号 |  | 评分因素（偏差率） | 评审标准 |  |  |
| --- | --- | --- | --- | --- | --- |
| 2.2.4（1） 和（3） | 承包人建议书和承包人实施方案 | 承包人建议书 ——设计优化（2 | 根据初步设计方案及发包人要求，结合本项目实际情况，在合法合规的前提下对本项目设计 |  |  |
|  |  | 分） | 提出建议性修改意见和优化方案，内容全面、科学合理得2分；内容基本全面、整体合理得1分；未提供不得分。 |  |  |
|  |  | 承包人实施方案 ——质量控制及质量保证措施 | 质量控制方法合理、重点突出、内容齐全、有措施且具体完整的得1 |  |  |
|  |  | （1.5分） | 分；一般或较差得0.5分；未提供不得分。 |  |  |
| 2.2.4（4） | 投标报价评分标准 | 投标报价 | 投标人的投标报价得分M的计算公式：如果投标人的投标报价高于评标基准价的，每高1.0%扣0.2分，扣完为止。即M＝50－(q-Q)/Q×100×0.2；如果低于评标基准价，每低1.0%扣0.1分；如果等于评标基准价，则M＝50。 |  |  |
"""

PROJECT2121_CANONICAL_TECHNICAL_ROWS = """[评分表 第40-43页]
| 分组 | 评分因素 | 评分内容与标准 | 分值 |
| --- | --- | --- | --- |
| 技术部分(F1、满分为100分) |  |  |  |
|  | 总体技术方案 | 提供总体技术方案；不满足是指与采购需求不一致等任意一种情形。 | 20 |
|  | 软件功能技术方案 | 提供软件功能技术方案；不满足是指与采购需求不一致等任意一种情形。 | 28 |
"""


PROJECT2123_POLLUTED_NAME_TABLE = """[评分表 第35-40页]
| 分组 | 评分因素 | 评分内容与标准 | 分值 |
| --- | --- | --- | --- |
|  | 技术标(汇总规则:当专家数量小于等于4位，取去掉0个最高分、0个最低分后的算术平均值;当专家数量大于4位，取去掉1个最高分、1个最低分后的算术平均值;) | 设计任务书优化：根据招标人提供的设计任务书进行优化，评委根据投标文件情况分为一般、良、优，分别酌情得10-14分、14-17分、17-20分，内容不全酌情扣分，若此条缺项不得分。 设计任务书优化 | 20 |
|  | 技术标(汇总规则:当专家数量小于等于4位，取去掉0个最高分、0个最低分后的算术平均值;当专家数量大于4位，取去掉1个最高分、1个最低分后的算术平均值;)(2) | 进度管理方案，包括施工进度和设计进度等各方面；评委根据投标文件情况分为一般、良、优，分别酌情得1-2分、2-3分、3-4分，内容不全酌情扣分，若此条缺项不得分； 进度管理方案 | 4 |
|  | 资信标 | 根据《关于停止济南市房屋建筑市场信用评价管理工作的通知》，济南市房屋建筑市场信用评价评分和评级工作停止，信誉评价结果均按满分计取。 信誉评价结果 | 67 |
|  | 商务标 | 投标总报价超出招标控制价范围为废标。总价评标基准值计算，低于基准价以上再，每高1%在前面得分的基础上扣1.5分扣完为止; 投标总报价 | 83 |
"""

PROJECT2123_RESIDUAL_NAME_TABLE = """[评分表 第35-40页]
| 分组 | 评分因素 | 评分内容与标准 | 分值 |
| --- | --- | --- | --- |
|  | 技术标(汇总规则:当专家数量小于等于4位，取去掉0个最高分、0个最低分后的算术平均值;当专家数量大于4位，取去掉1个最高分、1个最低分后的算术平均值;)(4) | 质量管理方案；对工程整体质量及进场材料设备的管控措施合理可行。评委根据投标文件情况酌情得1-4分。 | 4 |
|  | 技术标(汇总规则:当专家数量小于等于4位，取去掉0个最高分、0个最低分后的算术平均值;当专家数量大于4位，取去掉1个最高分、1个最低分后的算术平均值;)(12) | 安全施工、文明施工和绿色施工措施；评委根据投标文件情况酌情得1-8分。 | 8 |
|  | 资信标(5) | 1、项目经理近5年以项目负责人身份承担过公共建筑施工总承包或工程总承包（EPC）业绩，每个得2分，最多得2分。 2、设计负责人近5年以设计负责人身份承担过公共建筑设计业绩，每项加2分，最多加2分。 企业业绩与项目经理业绩、设计负责人业绩不可以重复计分。 | 4 |
|  | 商务标 | 总价评标基准值按有效投标报价计算。投标报价低于或高于基准值时按比例扣分，每高1%扣1.5分扣完为止。 | 83 |
"""


def test_project2121_assembler_keeps_initial_score_rows_and_weight_prelude():
    """2121: scoring_table chunk 不得漏掉表头后首部20+6分评分行。"""
    doc = ParsedDocument(
        file_path="评分测试22.pdf",
        filename="评分测试22.pdf",
        page_count=43,
        pages=[
            ParsedPage(
                page_num=40,
                text="第四章 评标方法及标准",
                tables=[SimpleNamespace(data=PROJECT2121_RAW_ROWS)],
            )
        ],
    )
    section = ScoringSection("第四章 评标方法及标准", 40, 40, "test")

    chunks = build_scoring_table_chunks(doc, ChunkPlanner(), 0, sections=[section])

    assert len(chunks) == 1
    assert "采购需求的响应程度" in chunks[0].content
    assert "需求理解" in chunks[0].content
    parsed = parse_scoring_table(chunks[0].content)
    names = {item.score_item_name for item in parsed.items}
    assert {"采购需求的响应程度", "需求理解"} <= names
    assert set(parsed.volume_defs) == {"technical", "commercial", "price"}


def test_project2121_real_five_column_header_keeps_initial_score_rows():
    """2121真实布局: 五列表内的计分因素/计分标准列不得错绑到首列。"""
    doc = ParsedDocument(
        file_path="评分测试22.pdf",
        filename="评分测试22.pdf",
        page_count=40,
        pages=[
            ParsedPage(
                page_num=40,
                text="第四章 评标方法及标准",
                tables=[SimpleNamespace(data=PROJECT2121_REAL_FIVE_COLUMN_ROWS)],
            )
        ],
    )
    section = ScoringSection("第四章 评标方法及标准", 40, 40, "test")

    segments, _claimed_tables = assemble_scoring_segments(doc, section)

    assert len(segments) == 1
    factors = {row.factor for row in segments[0].rows}
    assert {"采购需求的响应程度", "需求理解"} <= factors
    assert segments[0].resolved_roles is not None
    assert segments[0].resolved_roles.name == 1
    assert segments[0].resolved_roles.rule == 3

    parsed = parse_scoring_table("", grid=segment_to_grid(segments[0]))
    by_name = {item.score_item_name: item for item in parsed.items}
    assert by_name["采购需求的响应程度"].score_weight == 20.0
    assert by_name["需求理解"].score_weight == 6.0
    assert by_name["总体技术方案"].score_weight == 20.0
    assert by_name["软件功能技术方案"].score_weight == 28.0
    assert "根据采购需求响应程度评审" in by_name["采购需求的响应程度"].description
    assert set(parsed.volume_defs) == {"technical", "commercial", "price"}


def test_project2121_decimal_volume_weights_make_three_100_volumes_complete():
    """2121: `技术/商务/价格部分 0.x` 应转为三卷100分折算定义。"""
    markdown = "\n".join(
        [
            "[评分表 第40-43页]",
            "| 分组 | 评分因素 | 评分内容与标准 | 分值 |",
            "| --- | --- | --- | --- |",
            "|  |  | 技术部分 0.5 |  |",
            "|  |  | 商务部分 0.3 |  |",
            "|  |  | 价格部分 0.2 |  |",
            *(
                "| " + " | ".join(row) + " |"
                for row in PROJECT2121_RAW_ROWS[6:]
            ),
        ]
    )

    parsed = parse_scoring_table(markdown)
    apply_volume_definitions(parsed.items, parsed.volume_defs)
    report = verify_score_totals(parsed.items, volume_defs=parsed.volume_defs)

    assert len(parsed.items) == 11
    assert parsed.volume_defs["technical"].weight == 0.5
    assert parsed.volume_defs["commercial"].weight == 0.3
    assert parsed.volume_defs["price"].weight == 0.2
    assert parsed.volume_defs["technical"].volume_max == 100.0
    assert report.by_volume == {"technical": 100.0, "commercial": 100.0, "price": 100.0}
    assert report.needs_review is False


def test_project2121_canonical_technical_items_are_not_volume_headings():
    """2121 canonical布局: 含“技术”的评分项名不能被当成技术卷标题。"""
    parsed = parse_scoring_table(PROJECT2121_CANONICAL_TECHNICAL_ROWS)
    by_name = {item.score_item_name: item for item in parsed.items}

    assert by_name["总体技术方案"].score_weight == 20.0
    assert by_name["软件功能技术方案"].score_weight == 28.0
    assert "等任意一种情形" not in by_name


def test_project2122_joins_split_embedded_scores_and_uses_formula_total():
    """2122: 跨行`（N分）`要复原，报价公式应取M=50而非扣分步长0.2。"""
    parsed = parse_scoring_table(PROJECT2122_BROKEN_EMBEDDED_TABLE)
    by_name = {item.score_item_name: item for item in parsed.items}

    assert by_name["承包人建议书——设计优化"].score_weight == 2.0
    assert by_name["承包人实施方案——质量控制及质量保证措施"].score_weight == 1.5
    assert by_name["投标报价"].score_weight == 50.0
    assert by_name["投标报价"].volume == "price"


def test_project2123_polluted_group_names_fall_back_to_rule_tail_labels():
    """2123: 长汇总规则是分组污染，评分项名称应取规则尾部短标签。"""
    parsed = parse_scoring_table(PROJECT2123_POLLUTED_NAME_TABLE)
    by_name = {item.score_item_name: item for item in parsed.items}

    assert "设计任务书优化" in by_name
    assert "进度管理方案" in by_name
    assert "信誉评价结果" in by_name
    assert "投标总报价" in by_name
    assert not any("汇总规则" in item.score_item_name for item in parsed.items)
    assert by_name["信誉评价结果"].score_weight == 67.0
    assert by_name["投标总报价"].score_weight == 83.0


def test_project2123_residual_group_names_use_semantic_rule_labels():
    """2123真实残留: 无尾部重复标签时仍应恢复技术、资信和报价项名称。"""
    parsed = parse_scoring_table(PROJECT2123_RESIDUAL_NAME_TABLE)
    by_name = {item.score_item_name: item for item in parsed.items}

    assert by_name["质量管理方案"].score_weight == 4.0
    assert by_name["安全施工、文明施工和绿色施工措施"].score_weight == 8.0
    assert by_name["项目经理及设计负责人业绩"].score_weight == 4.0
    assert by_name["投标报价"].score_weight == 83.0
    assert not any("汇总规则" in item.score_item_name for item in parsed.items)


def test_project2123_three_independent_100_volumes_are_complete_without_weights():
    """2123: 无权重声明但三个明确卷各100分时，应按独立百分制闭环。"""
    items = [
        SimpleNamespace(score_weight=100.0, volume="technical", fact_type="base_score"),
        SimpleNamespace(score_weight=100.0, volume="qualification", fact_type="base_score"),
        SimpleNamespace(score_weight=100.0, volume="commercial", fact_type="base_score"),
    ]

    report = verify_score_totals(items)

    assert report.total == 300.0
    assert report.by_volume == {
        "technical": 100.0,
        "qualification": 100.0,
        "commercial": 100.0,
    }
    assert report.needs_review is False
    assert report.within_tolerance is True
    assert report.expected_total == 300.0
    assert all(check["within"] for check in report.volume_checks.values())
    assert all(check["weight"] == 0.0 for check in report.volume_checks.values())


def test_independent_100_volume_inference_rejects_incomplete_volume():
    """无权重多卷中任一卷不闭合时不得误判为独立百分制。"""
    items = [
        SimpleNamespace(score_weight=100.0, volume="technical", fact_type="base_score"),
        SimpleNamespace(score_weight=97.0, volume="qualification", fact_type="base_score"),
        SimpleNamespace(score_weight=100.0, volume="commercial", fact_type="base_score"),
    ]

    report = verify_score_totals(items)

    assert report.needs_review is True
    assert report.within_tolerance is False
