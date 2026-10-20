"""P2 防御测试: 跨页拼表 + 分值合计校验 + 评分区段定位。

映射真实问题三:
- 跨十几页评分表被切成多个表 -> table_stitcher 按列数/页连续/表头一致拼成整表。
- 漏行/串行导致评分项缺失 -> verify_score_totals 用满分合计兜底标待复核。
- 需先定位"评标办法/评分表"区段 -> scoring_section_locator 关键词配对(非子串误判)。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.parser.table_stitcher import (  # noqa: E402
    render_stitched_table_markdown,
    stitch_cross_page_tables,
)
from app.core.parser.scoring_section_locator import locate_scoring_sections  # noqa: E402
from app.core.parser.pdf_parser import ParsedDocument, ParsedPage  # noqa: E402
from app.core.extractor.scoring.score_totals import verify_score_totals  # noqa: E402
from app.core.extractor.scoring.scoring_table_parser import parse_scoring_table  # noqa: E402
from app.core.extractor.scoring.scoring_extractor import (  # noqa: E402
    backfill_source_pages,
    normalize_score_weight,
)
from app.core.extractor.scoring.scoring_identity import build_scoring_identity  # noqa: E402
from app.core.parser import ChunkPlanner  # noqa: E402
from app.core.parser.scoring_chunk_builder import build_scoring_table_chunks  # noqa: E402
from app.core.parser.scoring_section_locator import ScoringSection  # noqa: E402

import pytest

pytestmark = pytest.mark.scoring


def _tbl(data: list[list[str]], idx: int = 0):
    return SimpleNamespace(data=data, table_index=idx)


_HEADER = ["评分项", "分值", "评分标准"]


# ---------------------------------------------------------------------------
# normalize_score_weight (无分值归一,)
# ---------------------------------------------------------------------------


def test_normalize_score_weight_zero_and_invalid_to_none():
    # 无有效正分值(资格性/符合性审查说明项)归一为 None, 避免前端"0分·重点"
    assert normalize_score_weight(0) is None
    assert normalize_score_weight(0.0) is None
    assert normalize_score_weight(-3) is None
    assert normalize_score_weight(None) is None
    assert normalize_score_weight("") is None
    assert normalize_score_weight("abc") is None
    assert normalize_score_weight(10) == 10.0
    assert normalize_score_weight("12.5") == 12.5


# ---------------------------------------------------------------------------
# backfill_source_pages (: 确定性解析路径页码无关, 在提取器回填 chunk 页范围)
# ---------------------------------------------------------------------------


def test_backfill_source_pages_from_chunk_header_range():
    # 评分表整表 chunk 内容头 "[评分表 第47-48页]" → 缺页评分项回填 [47,48]
    items = [SimpleNamespace(source_pages=[]), SimpleNamespace(source_pages=None)]
    chunk = SimpleNamespace(content="[评分表 第47-48页]\n| 评分项 | 分值 |", page_num=47)
    backfill_source_pages(items, chunk)
    assert items[0].source_pages == [47, 48]
    assert items[1].source_pages == [47, 48]


def test_backfill_source_pages_preserves_existing():
    # 已有来源页码(如 LLM 兜底已带 [49])不被覆盖
    items = [SimpleNamespace(source_pages=[49])]
    chunk = SimpleNamespace(content="[评分表 第47-48页]", page_num=47)
    backfill_source_pages(items, chunk)
    assert items[0].source_pages == [49]


def test_backfill_source_pages_fallback_to_page_num():
    # 无范围头时回退到 chunk.page_num
    items = [SimpleNamespace(source_pages=[])]
    chunk = SimpleNamespace(content="| 评分项 | 分值 |", page_num=44)
    backfill_source_pages(items, chunk)
    assert items[0].source_pages == [44]


def test_scoring_identity_rejects_single_character_name():
    """: 单字名(如“是/否”)不可信, 不得构成精确去重身份键。"""
    from app.core.extractor.scoring.scoring_identity import build_scoring_identity
    from app.schemas.requirement import ExtractedScoringItem

    item = ExtractedScoringItem(
        category="TECH",
        score_item_name="是",
        description="错误从是否客观项列取到的属性值",
        score_weight=2.0,
        volume="technical",
        source_pages=[36],
    )
    assert build_scoring_identity(item) is None


# 阶段3(2026-08-31): _dedupe/_dedupe_exact/_absorb 去重助手用例随确定性链删除;
# 身份键/装配/检测器用例保留如下。


def test_scoring_identity_builds_stable_structure_key():
    """W1-2: 结构身份由方案、卷、来源页、满分和归一评分对象组成，供去重优先使用。"""
    from app.schemas.requirement import ExtractedScoringItem

    item = ExtractedScoringItem(
        category="TECH",
        score_item_name=" 项目机构 设置 ",
        description="从机构设置是否合理、职责是否明确等方面综合评审。",
        score_weight=10.0,
        volume="technical",
        source_pages=[96, 97, 98],
    )

    # scheme 分量默认 __single__(未切分文档), 多方案文档禁止跨方案精确去重命中
    assert build_scoring_identity(item) == (
        "__single__",
        "technical",
        "96-98",
        10.0,
        "项目机构设置",
    )


def test_scoring_detector_import_does_not_trigger_extractor_cycle():
    """W1-1: parser 侧导入评分表 detector 不应触发 extractor 包循环导入。"""
    from app.core.parser.scoring_table_detector import analyze_scoring_table_rows

    result = analyze_scoring_table_rows(
        [
            ["序号", "评分项目", "评分标准", "标准分（分）"],
            ["1", "技术方案", "好8-10分，较好6-8分。", "10分"],
        ]
    )

    assert result.is_scoring_candidate is True


# ---------------------------------------------------------------------------
# table_stitcher (跨页拼表)
# ---------------------------------------------------------------------------


def test_stitch_merges_adjacent_same_columns_and_dedupes_header():
    t1 = _tbl([_HEADER, ["技术方案", "15", "完整得满分"]])
    t2 = _tbl([_HEADER, ["施工业绩", "12", "每项3分"]])  # 续页重复表头
    stitched = stitch_cross_page_tables([(1, t1), (2, t2)])
    assert len(stitched) == 1
    st = stitched[0]
    assert st.page_start == 1 and st.page_end == 2
    assert st.source_count == 2
    # 表头只出现一次 + 两条数据行 = 3 行
    assert len(st.rows) == 3
    flat = [c for row in st.rows for c in row]
    assert "施工业绩" in flat and "技术方案" in flat
    assert flat.count("评分项") == 1


def test_stitch_does_not_merge_different_column_count():
    t1 = _tbl([_HEADER, ["技术方案", "15", "x"]])  # 3 列
    t2 = _tbl([["项目", "金额"], ["a", "b"]], idx=1)  # 2 列, 同/邻页
    stitched = stitch_cross_page_tables([(1, t1), (2, t2)])
    assert len(stitched) == 2


def test_stitch_respects_page_gap():
    t1 = _tbl([_HEADER, ["技术方案", "15", "x"]])
    t2 = _tbl([_HEADER, ["业绩", "12", "y"]])
    # 页间隔 2 > max_page_gap(1) -> 不拼接
    stitched = stitch_cross_page_tables([(1, t1), (3, t2)])
    assert len(stitched) == 2


def test_render_markdown_contains_all_rows():
    t1 = _tbl([_HEADER, ["技术方案", "15", "x"]])
    t2 = _tbl([_HEADER, ["业绩", "12", "y"]])
    st = stitch_cross_page_tables([(1, t1), (2, t2)])[0]
    md = render_stitched_table_markdown(st)
    assert "技术方案" in md and "业绩" in md
    assert "评分表 第1-2页" in md


def test_build_scoring_chunks_scans_internal_table_signals_without_section_hit():
    """: 即使章节定位丢失,也应从表内"评分项目+评分标准+标准分"强信号找回评分表。"""
    pages = [
        ParsedPage(
            page_num=1,
            text="普通章节\n本页没有评分表。",
            tables=[
                _tbl(
                    [
                        ["序号", "评分项目", "评分标准", "标准分（分）", "投标人名称及评审结论", "", ""],
                        ["1", "投标报价", "等于评标基准价得满分，每高1%扣3分。", "100.0 分", "", "", ""],
                        ["标准分合计", "", "", "100.0", "", "", ""],
                    ]
                )
            ],
        ),
        ParsedPage(
            page_num=2,
            text="投标文件格式\n技术投标文件目录 对应页码 编制说明",
            tables=[
                _tbl(
                    [
                        ["技术投标文件目录", "对应页码", "编制说明"],
                        ["1.xxxx", "", "按照第四章评标办法附件3-1的序号及评分项目编制目录"],
                    ],
                    idx=1,
                )
            ],
        ),
    ]
    chunks = build_scoring_table_chunks(_doc(pages), ChunkPlanner(), 0, sections=[])

    assert len(chunks) == 1
    assert chunks[0].metadata["chunk_type"] == "scoring_table"
    assert chunks[0].metadata["page_num"] == 1
    assert "投标报价" in chunks[0].content
    assert "技术投标文件目录" not in chunks[0].content


def test_build_scoring_chunks_prefers_pre_denoise_sections():
    """: 预降噪定位出的真实评分区段应可直接传入,不被后续错误章节重算覆盖。"""
    pages = [
        ParsedPage(page_num=1, text="附件3-1 详细评审标准及计分表", tables=[]),
        ParsedPage(
            page_num=2,
            text="评分明细",
            tables=[
                _tbl(
                    [
                        ["序号", "评分项目", "评分标准", "标准分（分）"],
                        ["1", "项目机构设置", "好8-10分，较好6-8分。", "10.0 分"],
                    ]
                )
            ],
        ),
        ParsedPage(
            page_num=3,
            text="技术投标文件目录",
            tables=[
                _tbl(
                    [
                        ["技术投标文件目录", "对应页码", "编制说明"],
                        ["1.xxxx", "", "按照评分项目编制目录"],
                    ],
                    idx=1,
                )
            ],
        ),
    ]
    sections = [ScoringSection("附件3-1 详细评审标准及计分表", 1, 2, "structure")]
    chunks = build_scoring_table_chunks(_doc(pages), ChunkPlanner(), 0, sections=sections)

    assert len(chunks) == 1
    assert chunks[0].metadata["page_num"] == 2
    assert "项目机构设置" in chunks[0].content
    assert "技术投标文件目录" not in chunks[0].content


def test_detector_accepts_embedded_bracket_score_table():
    """: 分值以`（N分）`嵌在评审因素单元格的层级表, 候选闸门应识别为评分表。"""
    from app.core.parser.scoring_table_detector import analyze_scoring_table_rows

    rows = [
        ["条款号", "", "评审因素", "评审标准"],
        ["2.2.3（1）", "商务评分标准 （25分）", "实力及信誉 （5分）", "根据企业资信综合评价，酌情赋0-5分。"],
        ["", "", "售后服务方案 （10分）", "根据售后服务方案综合评分，酌情赋0-10分。"],
        ["2.2.3（2）", "技术服务评分标准 （45分）", "产品参数与技术方案 （20分）", "根据技术方案先进性，酌情赋0-20分。"],
    ]
    analysis = analyze_scoring_table_rows(rows)

    assert analysis.is_scoring_candidate is True
    assert "embedded_score" in analysis.reasons


def test_detector_rejects_compliance_table_without_bracket_score():
    """ 护栏: 纯符合性表(无括号分值)不能被嵌入式识别误收。"""
    from app.core.parser.scoring_table_detector import analyze_scoring_table_rows

    rows = [
        ["序号", "评审因素", "评审标准"],
        ["1", "营业执照", "符合招标文件要求"],
        ["2", "投标有效期", "符合招标文件要求"],
        ["3", "质量标准", "符合招标文件要求"],
    ]
    analysis = analyze_scoring_table_rows(rows)

    assert analysis.is_scoring_candidate is False


_MIXED_REVIEW_SCORING_ROWS = [
    # 真实形态(项目1490系"招标.pdf" p17-20): 首表头为"评议"措辞,
    # 真正评分子表头"序号|内容|分值|评分标准"混排在表中部(前4行扫描不可见)。
    ["内容", "评议内容", "评议标准", "是否合格"],
    ["形式 评审", "投标人名称", "投标文件、印章、事业单位法人证书及各种证册上的名称是否一致", ""],
    ["资格 评审", "事业单位法人证书 （或营业执照）", "持有有效的事业单位法人证书或营业执照", ""],
    ["", "投标报价", "（1）投标降点率≥0%（2）不低于成本", ""],
    ["", "结 论", "", ""],
    ["技术部分，权重 60%", "", "", ""],
    ["序号", "内容", "分值", "评分标准"],
    ["1", "人员素质", "10", "团队主要人员6人及以上，得4分，每少1人扣1分。"],
    ["2", "施工业绩", "12", "有井筒腐蚀类或相关项目业绩，每份合同4分。"],
    ["3", "实验方案", "15", "每个测试项目有详细介绍，每缺1个扣1分。"],
    ["4", "工期保障", "6", "90天内完成得6分；90-120天得3分。"],
]


def test_detector_accepts_mixed_review_scoring_table_with_mid_table_subheader():
    """: 混排评分表(评议措辞首表头 + 表中部评分子表头)必须识别为候选。"""
    from app.core.parser.scoring_table_detector import analyze_scoring_table_rows

    analysis = analyze_scoring_table_rows(_MIXED_REVIEW_SCORING_ROWS)

    assert analysis.is_scoring_candidate is True
    assert "score_column" in analysis.reasons


def test_detector_accepts_scoring_rows_without_any_item_header_term():
    """: item 信号须与表头词表解耦——score 列左侧短中文对象名即行级结构信号。"""
    from app.core.parser.scoring_table_detector import analyze_scoring_table_rows

    rows = [
        ["商务部分，权重", "", ""],
        ["项目", "40% 评分标准", "分值"],
        ["投标报价", "采用降点率报价。商务评分标准采用降幅最高的价格作为基准价。", "40"],
        ["履约能力", "根据同类项目履约情况酌情打分。", "10"],
    ]
    analysis = analyze_scoring_table_rows(rows)

    assert analysis.is_scoring_candidate is True
    assert "item_structure" in analysis.reasons


def test_detector_still_rejects_price_list_with_small_numeric_column():
    """ 护栏: 单价表虽有短数值列+左侧短中文, 但无评分规则信号, 不得误收。"""
    from app.core.parser.scoring_table_detector import analyze_scoring_table_rows

    rows = [
        ["序号", "项目", "单位", "单价（元）"],
        ["1", "地层水离子特征分析测试", "组", "91"],
        ["2", "地层水细菌分布特征分析测试", "组", "35"],
        ["3", "腐蚀产物演化特征分析测试", "组", "111"],
    ]
    analysis = analyze_scoring_table_rows(rows)

    assert analysis.is_scoring_candidate is False


_CHAPTER_INDEX_ROWS = [
    # 真实形态(项目1539 p40): "对应章节"列的章节号 3/6/7/8 不是分值,
    # 且"评审因素/评审标准"表头词命中不代表是评分表(全表无任何评分规则语义)。
    ["评审因素", "评审标准", "技术规范书 对应章节"],
    ["技术规范书", "3.3 软件系统要求3.3.6★兼容性及升级3.3.7.★部署方式", "3"],
    ["", "6.2 ★工程进度要求", "6"],
    ["", "7.1 ★验收标准", "7"],
    ["", "8 ★技术支持、培训和服务", "8"],
]


def test_detector_rejects_chapter_index_table():
    """: 章节索引表(负向表头"对应章节"+全表无评分语义)不得判为评分候选。"""
    from app.core.parser.scoring_table_detector import analyze_scoring_table_rows

    analysis = analyze_scoring_table_rows(_CHAPTER_INDEX_ROWS)

    assert analysis.is_scoring_candidate is False
    assert analysis.score_columns == []


def test_detector_rejects_stitched_contract_and_chapter_index_table():
    """: ★合同条款表与章节索引表跨页拼接后(首表头无负向词), 行级三元仍应拦截。"""
    from app.core.parser.scoring_table_detector import analyze_scoring_table_rows

    rows = [
        ["序号", "条款名称", "编列内容"],
        ["1", "★合同期限", "合同生效之日起，直至卖方履行完毕本合同项下义务之日为止。"],
        ["2", "★履约约束方式", "卖方应提供合同金额5%的履约保证金。买方有权直接扣除违约金或赔偿款。"],
        *_CHAPTER_INDEX_ROWS,
    ]
    analysis = analyze_scoring_table_rows(rows)

    assert analysis.is_scoring_candidate is False


def test_detector_rejects_after_sales_assessment_form_1552():
    """: 售后服务履约考核表不是投标评分表, 不得因总分/分值列误收。"""
    from app.core.parser.scoring_table_detector import analyze_scoring_table_rows

    rows = [
        ["号", "序 考 核项 目", "项 目 总 分", "单 项 细 分", "单 项 分 数", "单 项 得 分", "考核项目说明", "扣 分 事 由"],
        ["", "1 故障修复基础管理", "70", "故障响应时限", "10", "", "接到故障申告0.5小时内响应，电话响应不及时每次扣5分", ""],
        ["", "", "", "重复故障", "30", "", "六个月内发生同模块重复故障，每次扣5分。", ""],
        ["", "2 检查评价", "30", "工作态度方面", "20", "", "发生有理由申告的，每次扣除10分。", ""],
        ["总分", "", "", "", "100", "", "考核人：", ""],
    ]
    analysis = analyze_scoring_table_rows(rows)

    assert analysis.is_scoring_candidate is False
    assert "assessment_form" in analysis.reasons


def test_detector_does_not_reject_regular_scoring_with_assessment_word_only():
    """护栏: 普通评分表规则中出现“考核得分”等词, 无考核表头组合时不误杀。"""
    from app.core.parser.scoring_table_detector import analyze_scoring_table_rows

    rows = [
        ["项目", "分值", "评分标准"],
        ["履约评价", "3", "根据考核得分由高到低进行排名，排名前30%得3分。"],
        ["施工质量", "3", "根据施工质量保障措施进行评分，内容全面得3分。"],
    ]
    analysis = analyze_scoring_table_rows(rows)

    assert analysis.is_scoring_candidate is True


def test_is_scoring_header_row_signatures():
    """: 表头签名谓词——全行扫描锚定的判据, 与词表位置/行号解耦。"""
    from app.core.extractor.scoring.score_column_semantics import is_scoring_header_row

    # 1547 真实表头(位于表中部第4行, 含空占位列)
    assert is_scoring_header_row(
        ["条款号", "", "", "评审 内容", "评分因 素", "", "分值", "评分标准"]
    ) is True
    # 1545/1546 三列表表头("得分"须可作表头分值词)
    assert is_scoring_header_row(["项目", "评 定 内 容 及 标 准", "得分"]) is True
    # 1490 表中部评分子表头
    assert is_scoring_header_row(["序号", "内容", "分值", "评分标准"]) is True
    # 1523 真实表头(埋在数据区第3行)应可锚定; 前导伪表头(条款内容+编列内容)无对象/标准语义不锚定
    assert is_scoring_header_row(["评分因素", "", "", "评分标准", "分值"]) is True
    assert is_scoring_header_row(["条款内容", "", "", "编列内容", "分值"]) is False
    # 单价表表头: 无分值/标准语义, 不得锚定
    assert is_scoring_header_row(["序号", "项目", "单位", "单价（元）"]) is False
    # 普通数据行(长文本规则+分值)不是表头: 词命中单元格须为短表头单元格
    assert is_scoring_header_row(
        ["价格", "以满足招标文件要求的最低投标报价为评标基准价，得满分 20 分，其他投标报价得分=（评标基准价/其他投标报价）×20。", "20"]
    ) is False
    # 卷说明行(分值构成)只有分值语义、无对象列词, 不是表头
    assert is_scoring_header_row(
        ["2.2.1", "", "分值构成（总分100分）", "", "商务技术评审部分： 20 分； 价格评审部分： 80 分。"]
    ) is False


def test_infer_column_roles_on_headerless_width_group():
    """: 1547 六列续表(无表头)按行分布推断列角色, 供装配器双向验证。"""
    from app.core.extractor.scoring.column_role_inference import infer_column_roles

    rows = [
        ["", "", "", "团队人 员能力", "2", "根据投标人拟投入项目的人员职称证书情况进行评分，本项最高得1分。"],
        ["", "", "", "财务 状况", "2", "根据2023年经第三方审计的财务报表进行评分，前30%有效投标人得满分1分。"],
        ["", "", "", "履约 评价", "3", "根据考核得分由高到低进行排名，排名前30%（含）得3分。"],
        ["", "", "", "施工质 量保障 措施", "3", "根据施工质量保障措施进行评分，内容全面、合理、针对性强，得[2,3]分。"],
    ]
    roles = infer_column_roles(rows)
    assert roles is not None
    assert roles.score == 4
    assert roles.rule == 5
    assert roles.name == 3


def test_infer_column_roles_rejects_pure_numeric_table():
    """ 护栏: 资源测算表数字列无评分规则语义, 不得被推断为分值列。"""
    from app.core.extractor.scoring.column_role_inference import infer_column_roles

    rows = [
        ["应用服务器", "8", "16", "500"],
        ["数据库", "16", "64", "2000"],
        ["中间件", "8", "32", "500"],
    ]
    assert infer_column_roles(rows) is None


def test_non_score_header_and_rule_semantics_predicates():
    """: 负向表头与行级评分规则语义谓词边界。"""
    from app.core.extractor.scoring.score_column_semantics import (
        has_scoring_rule_semantics,
        is_non_score_header,
    )

    assert is_non_score_header("技术规范书 对应章节") is True
    assert is_non_score_header("对应页码") is True
    # 含正向分值词时正向优先(如"评分因素权重分值"父级列)
    assert is_non_score_header("权重分值") is False
    assert is_non_score_header("满分 分值") is False
    assert is_non_score_header("评分标准") is False

    assert has_scoring_rule_semantics("团队主要人员6人及以上，得4分，每少1人扣1分") is True
    assert has_scoring_rule_semantics("根据同类项目履约情况酌情打分") is True
    assert has_scoring_rule_semantics("投标报价得分=商务总分-(基准价-有效报价降点率)") is True
    assert has_scoring_rule_semantics("3.3 软件系统要求3.3.6★兼容性及升级") is False
    assert has_scoring_rule_semantics("合同生效之日起90天内完成") is False


def test_build_scoring_chunks_accepts_embedded_bracket_score_section():
    """: 嵌入式括号分值表应生成 scoring_table chunk, 不被静默丢弃。"""
    pages = [
        ParsedPage(
            page_num=1,
            text="第四章 评标办法",
            tables=[
                _tbl(
                    [
                        ["条款号", "", "评审因素", "评审标准"],
                        ["2.2.3（1）", "商务评分标准 （25分）", "实力及信誉 （5分）", "根据企业资信综合评价，酌情赋0-5分。"],
                        ["", "", "售后服务方案 （10分）", "根据售后服务方案综合评分，酌情赋0-10分。"],
                        ["2.2.3（2）", "技术服务评分标准 （45分）", "产品参数与技术方案 （20分）", "根据技术方案先进性，酌情赋0-20分。"],
                    ]
                )
            ],
        )
    ]
    sections = [ScoringSection("第四章 评标办法", 1, 1, "structure")]
    chunks = build_scoring_table_chunks(_doc(pages), ChunkPlanner(), 0, sections=sections)

    assert len(chunks) == 1
    assert chunks[0].metadata["chunk_type"] == "scoring_table"
    assert "实力及信誉" in chunks[0].content


def test_build_scoring_chunks_accepts_mixed_review_scoring_table():
    """: 混排评分表在评分区段内必须生成 scoring_table chunk, 不得静默归零。"""
    pages = [
        ParsedPage(
            page_num=1,
            text="评标办法",
            tables=[_tbl(_MIXED_REVIEW_SCORING_ROWS)],
        )
    ]
    sections = [ScoringSection("评标办法", 1, 1, "structure")]
    chunks = build_scoring_table_chunks(_doc(pages), ChunkPlanner(), 0, sections=sections)

    assert len(chunks) == 1
    assert chunks[0].metadata["chunk_type"] == "scoring_table"
    assert "人员素质" in chunks[0].content


def test_build_scoring_chunks_bridges_table_loss_pages_1546():
    """: 同一逻辑表中间页失表退化为段落时, builder 应产出桥接后的单一 chunk。

 真实形态(项目1546): 前段表(59页)与后段表(65页)之间的63页为纯段落且不含"满分N分",
 "共三项满分9分"声明在65页表首续行单元格——单页恢复(旧机制)对此无效,
 必须由逻辑表装配桥接后按显式声明合成 9 分行。
 """
    front_table = _tbl(
        [
            ["项目", "评 定 内 容 及 标 准", "得分"],
            ["一、价格部分", "", "20"],
            ["价格", "以最低投标报价为评标基准价，得满分 20 分，其他按公式计算得分。", "20"],
            ["性能与配置", "根据投标产品资料完整性、性能稳定性等综合打分，满分 10 分。", "10"],
        ]
    )
    gap_text = (
        "投标人使用已有案例或真实系统进行演示，投标单位需通过现场真实系统操作的方式"
        "完成以下系统功能内容演示。每项内容演示详尽，功能演示流畅，每项得 3 分；"
        "功能演示基本流畅，每项得 2 分；功能演示不流畅，系"
    )
    back_table = _tbl(
        [
            ["", "统响应卡顿，功能模块缺失的，得每项 1 分, 未提供的不得分，共三项满分 9 分。", ""],
            ["项目实施方案", "投标人提供项目实施方案，内容包括实施计划与验收方案，满分 6 分。", "6"],
        ]
    )
    pages = [
        ParsedPage(page_num=59, text="第三章 评标标准和评标方法", tables=[front_table]),
        ParsedPage(page_num=63, text=gap_text, tables=[]),
        ParsedPage(page_num=65, text="评分表续页", tables=[back_table]),
    ]
    sections = [ScoringSection("第三章 评标标准和评标方法", 59, 66, "structure")]

    chunks = build_scoring_table_chunks(_doc(pages), ChunkPlanner(), 0, sections=sections)

    assert len(chunks) == 1
    assert chunks[0].metadata["chunk_type"] == "scoring_table"
    assert chunks[0].metadata.get("assembled") is True
    content = chunks[0].content
    assert "[评分表 第59-65页]" in content
    assert "共三项满分 9 分" in content.replace("，", "，")
    assert "| 9 |" in content
    assert "项目实施方案" in content


def test_build_scoring_chunks_gap_prose_without_tables_yields_nothing():
    """ 护栏: 区段内只有散文页(无任何表格)时不得凭空产出评分 chunk。"""
    pages = [
        ParsedPage(
            page_num=63,
            text=(
                "关键系统模块演示：投标单位需现场演示。每项得3分；"
                "功能演示不流畅得每项1分，未提供的不得分，共三项满分9分。"
            ),
            tables=[],
        )
    ]
    sections = [ScoringSection("第三章 评标标准和评标方法", 63, 63, "structure")]

    chunks = build_scoring_table_chunks(_doc(pages), ChunkPlanner(), 0, sections=sections)

    assert chunks == []


def test_build_scoring_chunks_injects_volume_composition_sentence():
    """: 评分区段页正文中的三卷满分声明应注入首个 scoring_table chunk 供 parser 消费。"""
    from app.core.extractor.scoring.scoring_table_parser import parse_scoring_table

    page_text = (
        "四、分值构成(权重)(详见公告中附件)\n"
        "分技术、商务、价格三个部分分别进行量化评审,每个部分满分均为100分,并按分值合计计算最终量化评审得分。"
    )
    table = _tbl(
        [
            ["项目", "评审要素", "评审内容"],
            ["技术方案（10分）", "", "根据技术方案进行评分，酌情赋0-10分。"],
            ["服务能力（10分）", "", "根据服务能力进行评分，酌情赋0-10分。"],
        ]
    )
    pages = [ParsedPage(page_num=21, text=page_text, tables=[table])]
    sections = [ScoringSection("应答人须知前附表附件一", 21, 21, "structure")]

    chunks = build_scoring_table_chunks(_doc(pages), ChunkPlanner(), 0, sections=sections)

    assert len(chunks) == 1
    assert "每个部分满分均为100分" in chunks[0].content
    parsed = parse_scoring_table(chunks[0].content, section_path=chunks[0].metadata["section_path"])
    assert set(parsed.volume_defs.keys()) == {"technical", "commercial", "price"}
    assert parsed.volume_defs["technical"].volume_max == 100.0
    assert parsed.volume_defs["price"].volume_max == 100.0


def test_build_scoring_chunks_recovers_trailing_text_score_row_1571():
    """: 无表头续表后的段落化尾页评分行应恢复进评分表 chunk。"""
    price_table = _tbl(
        [
            ["序号", "评审项目", "分值", "评审内容"],
            [
                "1",
                "报价部分 （70分）",
                "70分",
                "低价优先法，价格分=(Pmin/P)×A，价格分最高得满分。",
            ],
        ]
    )
    continuation_table = _tbl(
        [
            [
                "2",
                "商务部分 （16分）",
                "业绩要求",
                "10分",
                "类似蛋糕券销售合同，每有一个得2分，本项累计最高得10分。",
            ],
            ["3", "", "服务团队", "2分", "每增加1人得1分，满分2分。"],
            ["4", "", "提供增值服务项目", "4分", "优得4分，良得2-3分，一般得1分。"],
            [
                "5",
                "技术部分 （14分）",
                "蛋糕品牌覆盖",
                "8分",
                "品牌数量排名第一得6分；对接品牌券至少6家得2分。",
            ],
        ]
    )
    tail_text = (
        "根据供应商提供的质量保证措施和售后服务方案在0~6分之间合理打分。"
        "供应商应提供对所售商品质量把控的方案；售后服务方案应该包括所提供的生日蛋糕券"
        "的售后服务方案和所售商品的售后服务方案。"
        "6 质量保证措施和售后服务方案 6分 投标人需提供具体方案描述，仅提供承诺不得分。"
        "三、响应文件的澄清 评审委员会可以要求供应商澄清。"
    )
    pages = [
        ParsedPage(page_num=11, text="报价评分", tables=[price_table]),
        ParsedPage(page_num=12, text="商务技术评分续表", tables=[continuation_table]),
        ParsedPage(page_num=13, text=tail_text, tables=[]),
    ]
    sections = [ScoringSection("第三章 评选办法", 11, 13, "structure")]

    chunks = build_scoring_table_chunks(_doc(pages), ChunkPlanner(), 0, sections=sections)

    assert any("质量保证措施和售后服务方案" in chunk.content for chunk in chunks)
    items = []
    for chunk in chunks:
        parsed = parse_scoring_table(chunk.content, section_path=chunk.metadata["section_path"])
        assert parsed.confident
        items.extend(parsed.items)
    report = verify_score_totals(items)
    assert report.total == 100.0
    assert report.by_volume["price"] == 70.0
    assert report.by_volume["commercial"] == 16.0
    assert report.by_volume["technical"] == 14.0


def test_build_scoring_chunks_does_not_recover_unrelated_trailing_prose():
    """ 护栏: 紧邻评分表后的程序说明页即使含数字也不得产出评分尾行。"""
    table = _tbl(
        [
            ["序号", "评审项目", "分值", "评审内容"],
            ["1", "报价部分 （70分）", "70分", "低价优先法，价格分最高得满分。"],
        ]
    )
    pages = [
        ParsedPage(page_num=11, text="报价评分", tables=[table]),
        ParsedPage(
            page_num=12,
            text="三、响应文件的澄清 评审委员会可要求澄清，供应商应在6小时内回复。",
            tables=[],
        ),
    ]
    sections = [ScoringSection("第三章 评选办法", 11, 12, "structure")]

    chunks = build_scoring_table_chunks(_doc(pages), ChunkPlanner(), 0, sections=sections)

    assert len(chunks) == 1
    assert "6小时" not in chunks[0].content


def test_build_scoring_chunks_adds_lot_label_to_header_when_multi_package():
    """: 多采购包文档的评分 chunk 页头应带 lot 标签, 供后续解析/去重/核计分组。"""
    from app.core.extractor.scoring.scoring_table_parser import parse_scoring_table

    table1 = _tbl([
        ["项目", "分值", "评分标准"],
        ["技术方案", "70", "根据技术方案评分，满分70分。"],
        ["服务方案", "20", "根据服务方案评分，满分20分。"],
        ["进度方案", "10", "根据进度方案评分，满分10分。"],
    ])
    table2 = _tbl([
        ["项目", "分值", "评分标准"],
        ["监理方案", "90", "根据监理方案评分，满分90分。"],
        ["服务保障", "5", "根据服务保障评分，满分5分。"],
        ["资料管理", "5", "根据资料管理评分，满分5分。"],
    ])
    pages = [
        ParsedPage(page_num=34, text="采购包1:综合评分法 每个投标人的评标总得分FA=100分。", tables=[]),
        ParsedPage(page_num=36, text="评分表", tables=[table1]),
        ParsedPage(page_num=40, text="采购包2:综合评分法 每个投标人的评标总得分FA=100分。", tables=[]),
        ParsedPage(page_num=41, text="评分表", tables=[table2]),
    ]
    sections = [ScoringSection("评标标准", 34, 45, "structure")]

    chunks = build_scoring_table_chunks(_doc(pages), ChunkPlanner(), 0, sections=sections)

    assert len(chunks) == 2
    assert "· 采购包1" in chunks[0].content.splitlines()[0]
    assert "· 采购包2" in chunks[1].content.splitlines()[0]
    assert {item.lot for item in parse_scoring_table(chunks[0].content).items} == {"采购包1"}
    assert {item.lot for item in parse_scoring_table(chunks[1].content).items} == {"采购包2"}


def test_build_scoring_chunks_rejects_compliance_table_with_ordinal_numbers():
    """: 初步评审表的序号列不是分值列,不能因 1/2/3 序号被误收为评分表。"""
    pages = [
        ParsedPage(
            page_num=1,
            text="附件2-2 初步评审标准及记录表",
            tables=[
                _tbl(
                    [
                        ["序号", "评审因素", "评审标准", "投标人名称及评审结论", "", ""],
                        ["1", "独立法人资格", "不是招标人的附属机构", "", "", ""],
                        ["2", "与招标人关系", "与招标人不存在利害关系", "", "", ""],
                        ["3", "投标资格", "没有被暂停或者取消投标资格", "", "", ""],
                    ]
                )
            ],
        )
    ]
    sections = [ScoringSection("附件2-2 初步评审标准及记录表", 1, 1, "structure")]
    chunks = build_scoring_table_chunks(_doc(pages), ChunkPlanner(), 0, sections=sections)

    assert chunks == []


# ---------------------------------------------------------------------------
# score_totals (分值合计校验)
# ---------------------------------------------------------------------------


def _item(weight: float, volume: str | None = None):
    return SimpleNamespace(score_weight=weight, volume=volume)


def test_totals_pass_when_sum_100():
    report = verify_score_totals(
        [_item(60, "technical"), _item(40, "commercial")]
    )
    assert report.total == 100
    assert report.within_tolerance is True
    assert report.needs_review is False
    assert report.by_volume["technical"] == 60


def test_totals_flag_when_sum_deviates():
    report = verify_score_totals([_item(60, "technical"), _item(30, "commercial")])
    assert report.total == 90
    assert report.needs_review is True


def test_totals_note_direction_aware_for_under_and_over():
    """: 全局口径(无卷定义)的复核归因必须区分"低于基准=漏提"与"高于基准=重复提取",
 不得对超出 100 的场景机械沿用"漏提"措辞(超出不可能是漏)。
 """
    under = verify_score_totals([_item(60, "technical"), _item(30, "commercial")])
    assert "漏提" in under.note
    assert "重复提取" not in under.note

    over = verify_score_totals([_item(60, "technical"), _item(43, "commercial")])
    assert "重复提取" in over.note
    assert "漏提" not in over.note


def test_totals_flag_when_missing_score():
    report = verify_score_totals(
        [_item(60, "technical"), _item(40, "commercial"), _item(0, "technical")]
    )
    # 合计仍 100, 但有 1 项分值缺失 -> 需复核
    assert report.missing_score_count == 1
    assert report.needs_review is True


def test_totals_empty_needs_review():
    report = verify_score_totals([])
    assert report.needs_review is True
    assert "未提取到评分项" in report.note


# ---------------------------------------------------------------------------
# 第3层: 按卷满分校验(每卷满分=卷基准, 权重和=100%)
# ---------------------------------------------------------------------------


def test_totals_by_volume_pass():
    from app.core.extractor.scoring.volume_parser import VolumeDefinition
    defs = {
        "technical": VolumeDefinition("technical", 0.4, 100),
        "price": VolumeDefinition("price", 0.6, 100),
    }
    report = verify_score_totals([_item(100, "technical"), _item(100, "price")], volume_defs=defs)
    assert report.needs_review is False
    assert report.weight_sum_ok is True
    assert report.volume_checks["technical"]["within"] is True


def test_totals_by_volume_flags_gap():
    # 技术卷子项合计94 ≠ 基准100 → 标记复核(疑似漏提)
    from app.core.extractor.scoring.volume_parser import VolumeDefinition
    defs = {
        "technical": VolumeDefinition("technical", 0.4, 100),
        "price": VolumeDefinition("price", 0.6, 100),
    }
    report = verify_score_totals([_item(94, "technical"), _item(100, "price")], volume_defs=defs)
    assert report.needs_review is True
    assert report.volume_checks["technical"]["within"] is False


def test_totals_by_volume_flags_weight_sum():
    # 卷权重和=40% ≠ 100% → 标记复核
    from app.core.extractor.scoring.volume_parser import VolumeDefinition
    defs = {"technical": VolumeDefinition("technical", 0.4, 100)}
    report = verify_score_totals([_item(100, "technical")], volume_defs=defs)
    assert report.weight_sum_ok is False
    assert report.needs_review is True


def test_requirement_totals_report_passes_when_scored_volume_complete_without_price_items():
    """: 价格卷按公式计分无评分子项时,确认页不应因权重和40%误报评分项缺口。"""
    from app.services.requirement_service import _build_scoring_totals_report

    rows = [
        SimpleNamespace(
            requirement_kind="scoring_item",
            is_key_review=1,
            score_weight=100.0,
            scoring_metadata={
                "volume": "technical",
                "volume_weight": 0.4,
                "volume_max": 100.0,
            },
        )
    ]

    report = _build_scoring_totals_report(rows)

    assert report is not None
    assert report["by_volume"] == {"技术标": 100.0}
    assert report["volume_checks"]["技术标"]["within"] is True
    assert report["needs_review"] is False
    assert report["diagnostics"] == []


def test_requirement_totals_report_includes_diagnostics_when_needs_review():
    """W1-4: 合计异常时报告应给确认页可展示的人工复核原因。"""
    from app.services.requirement_service import _build_scoring_totals_report

    rows = [
        SimpleNamespace(
            requirement_kind="scoring_item",
            is_key_review=1,
            score_weight=90.0,
            scoring_metadata={
                "volume": "technical",
                "volume_weight": 1.0,
                "volume_max": 100.0,
            },
        )
    ]

    report = _build_scoring_totals_report(rows)

    assert report is not None
    assert report["needs_review"] is True
    # 确认页展示文案不得泄露内部英文卷标识"technical", 须为中文卷名"技术标"。
    assert any("技术标" in item for item in report["diagnostics"])
    assert not any("technical" in item for item in report["diagnostics"])


def test_requirement_totals_report_flags_global_sum_95_without_volume_defs():
    """: 单一100分制(无卷定义)合计95≠100 必须标记复核, 不得因 volume_checks 空误判通过。"""
    from app.services.requirement_service import _build_scoring_totals_report

    rows = [
        SimpleNamespace(
            requirement_kind="scoring_item",
            is_key_review=1,
            score_weight=65.0,
            scoring_metadata={"volume": "technical"},
        ),
        SimpleNamespace(
            requirement_kind="scoring_item",
            is_key_review=1,
            score_weight=30.0,
            scoring_metadata={"volume": "price"},
        ),
    ]
    report = _build_scoring_totals_report(rows)

    assert report is not None
    assert report["volume_checks"] == {}
    assert report["by_volume"] == {"技术标": 65.0, "价格": 30.0}
    assert report["needs_review"] is True
    assert report["diagnostics"]


def test_requirement_totals_report_passes_global_sum_100_without_volume_defs():
    """: 无卷定义但合计=100 时正常通过, 避免误报打扰用户。"""
    from app.services.requirement_service import _build_scoring_totals_report

    rows = [
        SimpleNamespace(
            requirement_kind="scoring_item",
            is_key_review=1,
            score_weight=70.0,
            scoring_metadata={"volume": "technical"},
        ),
        SimpleNamespace(
            requirement_kind="scoring_item",
            is_key_review=1,
            score_weight=30.0,
            scoring_metadata={"volume": "price"},
        ),
    ]
    report = _build_scoring_totals_report(rows)

    assert report is not None
    assert report["volume_checks"] == {}
    assert report["needs_review"] is False


# ---------------------------------------------------------------------------
# scoring_section_locator (评分区段定位)
# ---------------------------------------------------------------------------


def _doc(pages: list[ParsedPage]) -> ParsedDocument:
    return ParsedDocument(
        file_path="x.pdf",
        filename="x.pdf",
        page_count=max((p.page_num for p in pages), default=0),
        pages=pages,
    )


def test_locate_scoring_section_by_chapter_heading():
    pages = [
        ParsedPage(
            page_num=1,
            text="第二章 投标人须知\n" + "投标人须知的相关内容说明。" * 12,
        ),
        ParsedPage(
            page_num=2,
            text="第五章 评标办法\n" + "本项目采用综合评分法，技术标60分商务标40分。" * 8,
        ),
        ParsedPage(
            page_num=3,
            text="评分细则续表，详细评审标准如下所列。" * 12,
        ),
    ]
    sections = locate_scoring_sections(_doc(pages))
    assert len(sections) >= 1
    titles = " ".join(s.title for s in sections)
    assert "评标办法" in titles or any("评" in s.title for s in sections)
    # 命中区段应覆盖第2页
    assert any(s.page_start <= 2 <= s.page_end for s in sections)


def test_locate_returns_empty_without_scoring_section():
    pages = [
        ParsedPage(page_num=1, text="第一章 投标人须知\n" + "一般说明内容。" * 20),
        ParsedPage(page_num=2, text="第二章 项目概况\n" + "项目背景说明内容。" * 20),
    ]
    sections = locate_scoring_sections(_doc(pages))
    assert sections == []
