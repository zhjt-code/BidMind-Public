""" 防御测试: 表头驱动评分表解析(score_weight 只取满分列)。

fixture 取自 project 1497 真实 scoring_table chunk(数据库实测), 覆盖三类真实表:
- 框架表(权重列 + "X总分N分按M%折算") → 卷定义, 不产子项
- 明细评分表(满分分值列) → 子项, score_weight 取满分列(硬件=5 而非内容里的"低于60分")
- 响应性/资格审查表(无满分无权重) → 无分值符合性项
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.extractor.scoring.score_column_semantics import (  # noqa: E402
    is_ordinal_column,
    is_structural_score_item_name,
    is_total_row,
    parse_short_score,
    parse_weight_value,
)
from app.core.extractor.scoring.scoring_table_parser import parse_scoring_table  # noqa: E402

pytestmark = pytest.mark.scoring

# ---------------------------------------------------------------------------
# 真实表 fixture(精简自 1497 chunk, 保留表头与关键行)
# ---------------------------------------------------------------------------


def test_score_column_semantics_shared_helpers():
    """W1-1: parser/detector 共享短分值、权重和序号列语义，避免重复定义漂移。"""
    rows = [
        ["序号", "评分项目", "评分标准", "标准分（分）"],
        ["1", "技术方案", "好100-120分，较好80-99分。", "120分"],
        ["2", "实施方案", "好8-10分，较好6-8分。", "10.0 分"],
    ]

    assert parse_short_score("120分") == 120.0
    assert parse_short_score("每高1%扣3分，最多扣100分") is None
    assert parse_weight_value("0.40") == 0.4
    assert parse_weight_value("1.0") == 1.0
    assert parse_weight_value("1") is None
    assert is_ordinal_column(rows, 0) is True
    assert is_ordinal_column(rows, 3) is False
    assert is_total_row(["标准分合计", "", "", "100.0"]) is True
    assert is_structural_score_item_name("合计") is True
    assert is_structural_score_item_name("分值构成") is True
    assert is_structural_score_item_name("技术合计方案") is False


def test_is_ordinal_column_detects_chinese_ordinals():
    """中文序号列必须被识别为行标识（归档 /Task2 能力移植 U1）。

 holdout31 的「评审项目」列取值为 一/二/五，表头无序号词面线索，
 仅凭阿拉伯数字判据会漏判，导致序号被当成评分项名。
 该判据是列角色兜底（U4）拒绝序号列充当 name 列的输入。
 """
    rows = [
        ["评审项目", "评审因素", "分值"],
        ["一", "报价", "9"],
        ["二", "技术方案", "31"],
        ["五", "履约能力", "60"],
    ]
    assert is_ordinal_column(rows, 0) is True
    # 带顿号/句号的序号同样命中
    assert is_ordinal_column(
        [["项目", "分值"], ["一、", "9"], ["二。", "31"]], 0
    ) is True
    # 负例: 单个中文数字不足以判定（避免把"一次性投入"这类正常项名误杀）
    assert is_ordinal_column([["项目", "分值"], ["一", "9"], ["技术方案", "31"]], 0) is False
    # 负例: 分值列不得被误判为序号列
    assert is_ordinal_column(rows, 2) is False

FRAMEWORK_TABLE = """[评分表 第25-26页]
| 条款 号 | 条款名称 | 评审内容 | 评审因素 | 权重 占比 (%) | 评分标准 |
| --- | --- | --- | --- | --- | --- |
| 3.2.2 | 详细评审 标准 | 综合实力 | 综合实力 标准 | 0 |  |
|  |  | 技术 | 技术标准 | 40 | 技术总分100分，按40%权重进行折算。技术得分低于60分不具备中选资格。 |
|  |  | 后评估 | 评审标准 | 0 |  |
|  |  | 价格 | 价格评审 | 60 | 价格总分100分，按60%权重进行折算。价格评审采用向下引导的中间价评分法。 |
"""

DETAIL_TABLE = """[评分表 第28-30页]
| 评分标准 |  | 评分内容 | 主观/ 客观/ 扣分 | 满分 分值 |
| --- | --- | --- | --- | --- |
| 综合 实力 （25% ） | 相关业 绩 | 根据销售业绩进行评分，累计金额达到400万元及以上得满分19分。 | 客观 | 19.0 |
|  | 企业资 质 | 具备CCRC一级证书得3分；其他情况不得分。 | 客观 | 6.0 |
| 硬件 资源 (5%) | 硬件资 源占用 情况 | 应用服务器的IT云资源能耗低于60分并附测算依据，满足得1分。 | 客观 | 5.0 |
"""

DETAIL_WITH_CONTINUATION = """[评分表 第34-34页]
| 评分标准 |  | 评分内容 | 主观/ 客观/ 扣分 | 满分 分值 |
| --- | --- | --- | --- | --- |
| 技术 方案 | 整体建 设方案 | 根据整体建设方案进行评分，优秀得6分。 | 主观 | 6.0 |
|  |  | 续行说明：内容包括总体架构设计、技术架构。 |  |  |
"""

MULTIROW_HEADER_DETAIL_TABLE_1504 = """[评分表 第47-49页]
| 评分因素与权重分值 |  |  |  |  | 评分标准 |
| --- | --- | --- | --- | --- | --- |
| 条款 号 | 评分因 素 | 评分因 素权重 分值 | 各评分因素细分项 | 分值 |  |
| 2.2.4 （1） | 技术建 议书 | 50分 | 对招标项目的理解 和总体设计思路 | 12分 | 根据投标人对本工程重点分析、难点分析、对本工程勘察设计工作建议的合理性等因素打分。 |
|  |  |  | 对招标项目勘察设 计的特点、关键性技 术问题的认识及其 对策措施 | 15分 |  |
|  |  |  | 勘察设计工作量及 计划安排 | 8分 |  |
|  |  |  | 勘察设计的质量保 证措施、进度保证措 施、安全保证措施 | 9分 |  |
|  |  |  | 后续服务的安排及 保证措施 | 6分 |  |
| 2.2.4 （2） | 主要 人员 | 20分 | 项目负责人 任职资格与 业绩 | 10分 | 项目负责人每多一项资格要求的业绩加5分，本项最多加10分。 |
|  |  |  | 项目团队配置 | 10分 | 除项目负责人外拟配备的本项目团队各分项负责人中有2名以上高级职称的得6分，最多得10分。 |
| 2.2.4 （3） | 评标价 | 10分 | 评标价得分计算公式示例: 如果投标人的评标价高于或低于评标基准价，按公式计算得分。 |  |  |
| 2.2.4 （4） | 其他 因素 | 10分 | 类似项目业绩 | 1.满足资格审查条件的得基本分4分；2.增加一项类似业绩加3分，最多加6分。 |  |
|  |  | 10分 | 信誉 | 信用评级为AA及以上的得10分，A得5分，B级及以下与未参评得1分。 |  |
"""

COMPLIANCE_TABLE = """[评分表 第26-27页]
| 序号 | 评审大类 | 二级评审子类 | 主要内容 |
| --- | --- | --- | --- |
| 1 | 响应性评审 | 应答人名称 | 与营业执照、资质证书或其他组织证明一致。 |
| 2 |  | 应答函 | 按询比文件要求提供。 |
"""

DIRTY_HEADER = """[评分表 第34-34页]
| elid= 2740 45 | s earch | word | ， CCS C |
| --- | --- | --- | --- |
| x | y | z | w |
"""

# C 真实场景(精简自 1499 chunk 124131): 跨页续表表头丢失, 首行被 pdfplumber 塞入
# 超链接注解(elid/URL)当表头 → 关键词列定位失败。但数据行有"主观/客观列 + 相邻满分列"特征,
# 应被无表头识别确定性提全(救回 LLM 兜底曾漏的"整体建设6+实施计划6"=12分)。
HEADERLESS_DIRTY_TABLE = """[评分表 第34-34页]
| elid= 2740 45&s earch word =， CCS C： https: //ww w.ccs rp.or g.cn/ #/） |  |  |  |  |
| --- | --- | --- | --- | --- |
| 维保 要求 （2% ） | 维保期 限 | 在1年免费维保期的基础上，每增加1年得1分，满分2分。 | 客观 | 2.0 |
| 售后 承诺 (4%) | 维保费 用 | 根据承诺出保后的年维保费用占应答报价的比例进行评分。 | 客观 | 4.0 |
| 技术 方案 （24% ） | 整体建 设方案 | 根据应答人提供的整体建设方案进行评分，优秀得6分。 | 主观 | 6.0 |
|  | 实施计 划方案 | 根据应答人提供的实施计划方案进行评分，优秀得6分。 | 主观 | 6.0 |
|  | 数据初 始化服 务方案 | 根据数据初始化服务方案进行评分。 | 主观 | 6.0 |
|  | 人员培 训方案 | 根据人员培训方案进行评分。 | 主观 | 6.0 |
"""

# 资源测算表: 有数字列但无"主观/客观"列, 不得被无表头识别误判为评分表(防回归)。
RESOURCE_TABLE_DIRTY = """[评分表 第70-70页]
| 模块 | CPU（核） | 内存（GB） | 存储 （GB） | 对应节点 |
| --- | --- | --- | --- | --- |
| 应用服务器 | 8 | 16 | 500 | node1 |
| 数据库 | 16 | 64 | 2000 | node2 |
"""

MIXED_FORMULA_RECOMMENDATION_TABLE_1933 = """[评分表 第67-72页]
| 序号 | 评分因素 | 评分标准 | 分值 |
| --- | --- | --- | --- |
| 1 | 管理体系认证 | 投标人具备有效质量管理体系认证证书，全部具备得1分。 | 1 |
| 2 | 企业业绩 | 按合同累计金额分档计分，本项最高得2分。 | 2 |
| 3 | 施工质量保障措施 | 根据措施完整性评分。 | 3 |
| 4 | 基准价法 | 评标基准价计算及报价得分规则说明。 | 80 |
| 5 | 综合得分 | 综合得分=商务得分+价格得分 | 100 |
| 3.4 | 中标候选人推荐原则 | 详见第二章投标人须知前附表。 | 3.4 |
"""

# 真实场景(项目1936 chunk 170200 精简): "评分因素"列为序号数字(真实因素名被
# 竖排文字挤入规则文本尾部), 明细/列统计路径全部失配。修复前落入符合性分支被
# confident=True 静默跳过 → 评分项0条且不触发 LLM 兜底。
ORDINAL_NAME_SCORE_TABLE_1936 = """[评分表 第5-13页]
| 分组 | 评分因素 | 评分内容与标准 | 分值 |
| --- | --- | --- | --- |
|  |  | 分值构成 商务分： 10分 技术分： 30分 价格分： 60分 |  |
|  | 1 | 提供员工近六个月社保证明，员工大于等于10人得 2 分,少于10人不得分。 商务 部分 企业 规模 | 2 |
|  | 2 | 供应商提供项目负责人近 6 个月社会保险记录，完全提供得2分，未提供不得分。 社会 保险 | 2 |
|  | 3 | 提供 2022 年至今类似项目维保合同，每提供 1 份得 1 分；最多得 6 分。 经营 业绩 | 6 |
|  | 4 | 维保方案合理，内容详细完整性好得 7分，专家根据文件酌情扣减。 技术 部分 方案 | 25 |
|  | 5 | 维修人员定期培训，每提供 1 项得 1 分；最多得 5 分。 定期 培训 | 5 |
|  | 6 | 投标报价=评标基准价，得分=60；投标报价高于评标基准价按公式扣分。 投标 报价 | 60 |
"""

# 真实场景(项目1506 page21): 无独立"满分分值"列,分值嵌入"评审项目/评审标准"文本。
# 当前旧解析器会误判为"非评分明细表"并 confident=True 跳过,导致 scoring_item=0。
EMBEDDED_SCORE_TABLE_1506 = """[评分表 第21-21页]
| 序号 | 评审项目 |  | 评审标准 | 评审依据 |
| --- | --- | --- | --- | --- |
| 1 | 承包商业绩（5分） |  | 1.投标人所附业绩中压裂液技术服务井数大于等于10口井（5分）；2.大于等于7口井但小于10口井（4分）；3.大于等于5口井但小于7口井（3分）；4.大于等于1口井但小于5口井（2分）；5.无业绩相关证明得0分。 | 要求提供合同及结算发票复印件。 |
| 2 | 技术方案（10分） |  | 1.设计方案可行性：论据充分4分；一般3分；较差2分；2.施工方案可操作性：论据充分6分；一般4分；较差3分。 | 提供理论分析和现场施工证明。 |
| 3 | 技术服务（30分） | 材料性能（20分） | 各项性能指标均达到技术指标要求，得基础分14分。表观黏度、耐温耐剪切能力、分子量为核心指标，每有一项核心指标优于技术指标的10%，加2分。 | 需提供近1年内第三方实验检测报告。 |
|  |  | 现场技术服务（10分） | 现场技术服务人员最低人数配置6人，低于6人该项不得分；现场带队负责人具有10年以上施工指挥经验，6分，且有高级工程师证加1分，施工经验每增加或减少1年，加减0.5分，最高得分10分。 | 要求提供相应证明。 |
| 4 | 安全环保措施（15分） |  | 为该项目成立项目部或专门负责机构得5分；有健全的HSE管理体系得2分；应急预案基本涵盖所有风险点4分；施工风险识别完善4分。 | 方案里添加现场风险识别及注意事项 |
"""

EMBEDDED_PRICE_TABLE_1506 = """[评分表 第22-22页]
| 项目 | 评分标准 |
| --- | --- |
| 商务报价部分 | 投标报价高于最高限价为废标。各投标单位报价下降比例的平均值为基准值：1.投标报价的下降比例值与基准值相等的，得基础分30分；2.下降比例值小于基准值的，每小于1%在基础分基础上扣1分，扣完为止；3.下降比例值大于基准值的，每大于1%在基础分基础上加1分，最高加10分；如投标报价下降比例大于10%的，不参与算术平均值计算，直接在40分的基础上每下降1%扣1分，扣完为止。保留2位小数。 |
"""

# 真实场景(项目1519 page96-109 精简): 满分列叫"标准分（分）",
# 且表内同时具备"评分项目 + 评分标准 + 具体分值"三元关系。
STANDARD_SCORE_TABLE_1519 = """[评分表 第96-109页]
| （一）报价部分 |  |  |  |  |  |  |
| --- | --- | --- | --- | --- | --- | --- |
| 序号 | 评分项目 | 评分标准 | 标准分 （分） | 投标人名称 及评审结论 |  |  |
|  |  |  |  | 投 标 人 1 | 投 标 人 2 | … … |
| 1 | 投标报价 | 基准值公式标准：有效投标报价等于评标基准价时，得满分；每高1%扣3分，最多扣100分；每低1%扣1分，最多扣100分。 | 100.0 分 |  |  |  |
| 标准分合计 |  |  | 100.0 |  |  |  |
| （二）综合部分 |  |  |  |  |  |  |
| 序号 | 评分项目 | 评分标准 | 标准分 （分） | 投标人名称 及评审结论 |  |  |
| 1 | 投标人业 绩 | 2023年以来有信息化相关施工项目：每项10分，满分30分。 | 30.0 分 |  |  |  |
| 2 | 项目经理 业绩 | 2023年以来有信息化相关项目经理业绩：每项10分，满分30分。 | 30.0 分 |  |  |  |
| 3 | 技术负责 人业绩 | 2023年以来有信息化相关技术负责人业绩：每项10分，满分30分。 | 30.0 分 |  |  |  |
| 4 | 投标文件 编制质量 | 好：8-10分；较好：6-7分；有较多瑕疵：1-5分。 | 10.0 分 |  |  |  |
| 标准分合计 |  |  | 100.0 |  |  |  |
| （三）技术部分 |  |  |  |  |  |  |
| 序号 | 评分项目 | 评分标准 | 标准分 （分） | 投标人名称 及评审结论 |  |  |
| 1 | 项目机构 设置 | 从机构设置是否合理、职责是否明确等方面综合评审：好8-10分，较好6-8分。 | 10.0 分 |  |  |  |
| 2 | 特殊技术 措施 | 针对工程难点、特殊过程控制措施综合评审：好11-15分，较好6-11分。 | 15.0 分 |  |  |  |
| 标准分合计 |  |  | 100.0 |  |  |  |
"""

DIRECTORY_TEMPLATE_1519 = """[评分表 第131-131页]
| 技术投标文件目录 | 对应页码 | 编制说明 |
| --- | --- | --- |
| 1.xxxx |  | 技术投标文件目录由投标人编制：按照第四章“评标办法”附件3-1“详细评审标准及计分表”中的“序号”及“评分项目”内容对应编制。 |
| …… |  |  |
"""

WEIGHT_SUMMARY_TABLE_1519 = """[评分表 第110-110页]
| 序号 | 投标人名称 | 评审项 | 评标委员会成员姓名及得分评审结果 | 得分小计（分） | 平均分（分） | 加权得分（分） | 各有效投标的最终得分（分） |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 |  | 报价 | 0.40 |  |  |  |  |
| 2 |  | 综合 | 0.15 |  |  |  |  |
| 3 |  | 技术 | 0.45 |  |  |  |  |
"""

MIXED_DETAIL_AND_WEIGHT_TABLE = """[评分表 第20-20页]
| 序号 | 评分项目 | 评分标准 | 标准分（分） | 加权得分（分） |
| --- | --- | --- | --- | --- |
| 1 | 技术方案 | 好8-10分，较好6-8分。 | 10.0 分 |  |
| 2 | 评审项 | 技术 |  | 0.60 |
| 3 | 评审项 | 价格 |  | 0.40 |
"""

NON_100_SCORE_TABLE = """[评分表 第20-20页]
| 序号 | 评分项目 | 评分标准 | 满分 |
| --- | --- | --- | --- |
| 1 | 技术方案 | 好100-120分，较好80-99分。 | 120分 |
"""


# ---------------------------------------------------------------------------
# 框架表 → 卷定义
# ---------------------------------------------------------------------------


def test_framework_table_yields_volume_defs_not_items():
    res = parse_scoring_table(FRAMEWORK_TABLE)
    assert res.confident is True
    assert res.items == []  # 框架表不产评分子项(根治"100分"伪项)
    assert set(res.volume_defs.keys()) == {"technical", "price"}
    tech = res.volume_defs["technical"]
    price = res.volume_defs["price"]
    assert tech.weight == 0.4 and tech.volume_max == 100.0
    assert price.weight == 0.6 and price.volume_max == 100.0


# ---------------------------------------------------------------------------
# 明细表 → score_weight 取自满分列(核心回归: "低于60分"不被当满分)
# ---------------------------------------------------------------------------


def test_detail_table_score_from_value_column():
    res = parse_scoring_table(DETAIL_TABLE)
    assert res.confident is True
    assert res.volume_defs == {}
    by_name = {it.score_item_name: it for it in res.items}
    assert len(res.items) == 3
    # 业绩满分取自"满分分值"列=19, 非内容里的"400万"或"19分"文本歧义
    assert by_name["相关业绩"].score_weight == 19.0
    assert by_name["企业资质"].score_weight == 6.0
    # 关键回归: 硬件项满分=5(满分列), 绝不是内容里"IT云资源能耗低于60分"的 60
    hw = by_name["硬件资源占用情况"]
    assert hw.score_weight == 5.0
    assert "低于60分" in hw.description  # 60 留在内容, 不进满分
    # 明细子项归技术卷(默认), 供后续按卷加权折算
    assert all(it.volume == "technical" for it in res.items)


def test_price_group_row_with_score_infers_price_volume():
    """: 含分值的`报价部分`明细行应归 price, 不应落入默认 technical。"""
    table = """[评分表 第11-11页]
| 序号 | 评审项目 | 分值 | 评审内容 |
| --- | --- | --- | --- |
| 1 | 报价部分 （70分） | 70分 | 低价优先法，价格分=(Pmin/P)×A，价格分最高得满分。 |
"""

    res = parse_scoring_table(table, section_path="评分相关章节")

    assert res.confident is True
    assert len(res.items) == 1
    # 名称装饰剥离(失败归因专项): 尾部"（70分）"是排版装饰非名称组成,
    # 真值口径为"报价部分"(sample_truth_training.md structure-15)。
    assert res.items[0].score_item_name == "报价部分"
    assert res.items[0].score_weight == 70.0
    assert res.items[0].volume == "price"


def test_detail_table_continuation_row_merged():
    res = parse_scoring_table(DETAIL_WITH_CONTINUATION)
    assert res.confident is True
    assert len(res.items) == 1  # 满分列空的续行并入上一项, 不新建评分项
    item = res.items[0]
    assert item.score_weight == 6.0
    assert "整体建设方案" in item.description or "总体架构设计" in item.description
    assert "续行说明" in item.description  # 续行内容已并入


def test_multirow_header_detail_table_uses_real_score_column():
    # 项目1504真实样式为"分组表头 + 真实列头"两层表头。
    # 解析器必须选择第二层"分值"列, 不能把第一层"评分因素与权重分值"误当满分列。
    res = parse_scoring_table(MULTIROW_HEADER_DETAIL_TABLE_1504)
    assert res.confident is True

    weights = sorted(it.score_weight for it in res.items)
    assert 2.2 not in weights
    assert weights == sorted([12.0, 15.0, 8.0, 9.0, 6.0, 10.0, 10.0, 10.0, 10.0, 10.0])

    descriptions = " ".join(it.description for it in res.items).replace(" ", "")
    for expected in (
        "总体设计思路",
        "关键性技术问题",
        "工作量",
        "后续服务",
        "项目负责人",
        "项目团队配置",
        "评标价",
        "类似项目业绩",
        "信誉",
    ):
        assert expected in descriptions


def test_embedded_score_table_1506_extracts_project_scores():
    # 1506 的评分表分值嵌入"评审项目/评审标准"文本,没有独立满分列。
    # 解析器应识别"评分对象-满分-评分规则"三元关系,不能当符合性表跳过。
    res = parse_scoring_table(EMBEDDED_SCORE_TABLE_1506)
    assert res.confident is True
    by_name = {it.score_item_name: it for it in res.items}

    assert by_name["承包商业绩"].score_weight == 5.0
    assert by_name["技术方案"].score_weight == 10.0
    assert by_name["材料性能"].score_weight == 20.0
    assert by_name["现场技术服务"].score_weight == 10.0
    assert by_name["安全环保措施"].score_weight == 15.0
    assert "技术服务" not in by_name  # 父项30分由20+10两个子项承接,不重复计分
    assert sum(it.score_weight for it in res.items) == 60.0


def test_embedded_price_formula_table_1506_extracts_single_price_item():
    # 商务报价是公式型评分,应作为一个40分价格/商务项,不拆成多个加扣分项。
    res = parse_scoring_table(EMBEDDED_PRICE_TABLE_1506)
    assert res.confident is True
    assert len(res.items) == 1
    item = res.items[0]
    assert item.score_item_name == "商务报价部分"
    assert item.score_weight == 40.0
    assert item.category == "COMM"
    assert item.volume in ("price", "commercial")


def test_standard_score_column_table_1519_extracts_scoring_triples():
    """: `标准分（分）` 是满分列语义,不是只能靠 `满分/分值` 字样。"""
    res = parse_scoring_table(STANDARD_SCORE_TABLE_1519)

    assert res.confident is True
    by_name = {it.score_item_name.replace(" ", ""): it for it in res.items}

    assert by_name["投标报价"].score_weight == 100.0
    assert by_name["投标报价"].volume == "price"
    assert by_name["投标人业绩"].score_weight == 30.0
    assert by_name["投标人业绩"].volume == "commercial"
    assert by_name["项目经理业绩"].score_weight == 30.0
    assert by_name["技术负责人业绩"].score_weight == 30.0
    assert by_name["投标文件编制质量"].score_weight == 10.0
    assert by_name["项目机构设置"].score_weight == 10.0
    assert by_name["项目机构设置"].volume == "technical"
    assert by_name["特殊技术措施"].score_weight == 15.0
    assert "每高1%扣3分" in by_name["投标报价"].description
    assert "标准分合计" not in by_name


def test_directory_template_referencing_scoring_table_is_not_scoring_item_source():
    """: 引用评标办法的投标文件目录模板不得生成评分项或触发 LLM 兜底。"""
    res = parse_scoring_table(DIRECTORY_TEMPLATE_1519)

    assert res.confident is True
    assert res.items == []
    assert "目录" in res.note


def test_weight_summary_table_1519_yields_volume_defs_not_items():
    """: 评分汇总表中的 0.40/0.15/0.45 是卷权重,不是评分项。"""
    res = parse_scoring_table(WEIGHT_SUMMARY_TABLE_1519)

    assert res.confident is True
    assert res.items == []
    assert res.volume_defs["price"].weight == 0.4
    assert res.volume_defs["commercial"].weight == 0.15
    assert res.volume_defs["technical"].weight == 0.45
    assert all(v.volume_max == 100.0 for v in res.volume_defs.values())


def test_mixed_detail_and_weight_table_keeps_items_and_volume_defs():
    """同一表同时出现明细评分项与权重汇总时,不能早返回导致评分项丢失。"""
    res = parse_scoring_table(MIXED_DETAIL_AND_WEIGHT_TABLE)

    assert res.confident is True
    assert len(res.items) == 1
    assert res.items[0].score_item_name == "技术方案"
    assert res.items[0].score_weight == 10.0
    assert res.volume_defs["technical"].weight == 0.6
    assert res.volume_defs["price"].weight == 0.4


def test_score_column_allows_non_100_point_scale():
    """评分表可能不是百分制,满分列不应硬编码 <=100。"""
    res = parse_scoring_table(NON_100_SCORE_TABLE)

    assert res.confident is True
    assert len(res.items) == 1
    assert res.items[0].score_weight == 120.0


# ---------------------------------------------------------------------------
# 符合性表 → 无分值
# ---------------------------------------------------------------------------


def test_compliance_table_not_extracted_as_scoring():
    # 追修: 响应性/资格审查表是否决/符合条款, 非评分项, 不再逐行提取为无分值"评分项"
    # (否则确认页堆成大量"评分项·重点"误导用户; 这类条款由 tender_extractor 走 normal 通道)。
    res = parse_scoring_table(COMPLIANCE_TABLE)
    assert res.confident is True   # 已识别为非评分明细表(无需 LLM 兜底重提)
    assert res.items == []         # 不产 scoring_item, 不污染评分项列表
    assert res.volume_defs == {}


def test_reappearing_parent_group_cell_is_not_a_leaf_item():
    """: 跨页重复的父分组合并单元格再现时, 不得被当作叶子评分项重复计分。

 项目2035: "商务部分评分标准（13分）"首现与子项同行(正确判父分组),
 翻页再现时单独成行, 旧逻辑产出13分叶子项 → 商务卷26/13、全集113/100。
 """
    from app.core.extractor.scoring.embedded_score_parser import parse_embedded_score_table

    header = ["条款号", "", "条款内容", "编列内容"]
    data = [
        [
            "2.2.3 （3）",
            "商务部分 评分标准 （13分）",
            "业绩 （7分）",
            "1.企业业绩：每提供1项有效业绩得1分，最多得5分。2.项目负责人业绩：每提供1项得1分，最多得2分。",
        ],
        [
            "",
            "",
            "团队成员 （6分）",
            "团队成员每有一人具备高级及以上职称得2分，具备中级职称得1分，本项最多得6分。",
        ],
        [
            "",
            "商务部分 评分标准 （13分）",
            "评审方法采用综合评估法评审的项目",
            "评审小组应按照询比文件规定的评审标准对供应商的响应文件进行综合评价。",
        ],
    ]

    items = parse_embedded_score_table(header, data)

    names = [it.score_item_name for it in items]
    assert "商务部分评分标准" not in names, f"父分组再现不得成项: {names}"
    total = round(sum(float(it.score_weight or 0) for it in items), 4)
    assert total == 13.0  # 业绩7 + 团队成员6
    assert {"业绩", "团队成员"} <= set(names)


def test_score_column_table_must_not_be_confidently_skipped_as_compliance():
    """: 含真实分值列的评分表即使全部确定性路径失配, 也禁止确定性跳过。

 confident=True + items=[] 会同时关闭 L1/L2/L3 整条救援链且无任何前端提示
 (项目1936静默零提取)。必须返回 confident=False 交 LLM 兜底并留下诊断。
 """
    res = parse_scoring_table(ORDINAL_NAME_SCORE_TABLE_1936)

    assert res.confident is False, f"不得确定性跳过: note={res.note}"
    assert "跳过" not in res.note


# ---------------------------------------------------------------------------
# 脏表头 → 不自信, 交 LLM 兜底
# ---------------------------------------------------------------------------


def test_dirty_header_falls_back_to_llm():
    res = parse_scoring_table(DIRTY_HEADER)
    assert res.confident is False
    assert res.items == []


def test_empty_or_nontable_not_confident():
    assert parse_scoring_table("").confident is False
    assert parse_scoring_table("一些普通文字，没有表格。").confident is False


# ---------------------------------------------------------------------------
# C: 无表头续表(表头丢失/乱码)经"主观客观列+相邻满分列"确定性救回
# ---------------------------------------------------------------------------


def test_headerless_table_recovered_via_subjectivity_column():
    res = parse_scoring_table(HEADERLESS_DIRTY_TABLE)
    assert res.confident is True
    # 无漏行: 维保/售后/整体建设/实施计划/数据初始化/人员培训 共6行
    assert len(res.items) == 6
    total = sum(it.score_weight for it in res.items)
    assert total == 30.0  # 2+4+6+6+6+6, 含 LLM 兜底曾漏的整体建设6+实施计划6
    descriptions = " ".join(it.description for it in res.items)
    assert "整体建设" in descriptions and "实施计划" in descriptions
    # 满分取自分值列(2/4/6), 不被内容里的"得6分""每增加1年"误导
    assert sorted(it.score_weight for it in res.items) == [2.0, 4.0, 6.0, 6.0, 6.0, 6.0]


def test_resource_table_not_misrecovered_as_scoring():
    # 资源测算表有数字列但无"主观/客观"列 → 无表头识别不触发, 不污染评分项(防回归)
    res = parse_scoring_table(RESOURCE_TABLE_DIRTY)
    assert res.items == []


def test_mixed_formula_recommendation_rows_are_dropped_row_level():
    """: 综合得分公式与推荐原则按行剔除，真实评分行保持可信并闭合100分。

 整表降级会把真实评分行推给更弱的L1兜底(1934实测16/100)，因此必须行级剔除。
 """
    res = parse_scoring_table(MIXED_FORMULA_RECOMMENDATION_TABLE_1933)

    assert res.confident is True
    names = [it.score_item_name.replace(" ", "") for it in res.items]
    assert all("综合得分" not in name for name in names)
    assert all("推荐原则" not in name for name in names)
    total = round(sum(float(it.score_weight or 0) for it in res.items), 4)
    assert total == 86.0  # 1+2+3+80, 公式100分与推荐原则3.4分被剔除
    assert any("已剔除非评分项行" in item for item in res.diagnostics)


def test_dirty_header_without_subjectivity_still_falls_back():
    # 脏表头 + 无主观客观信号 + 单数据行 → 不触发无表头识别, 维持 LLM 兜底
    res = parse_scoring_table(DIRTY_HEADER)
    assert res.confident is False
    assert res.items == []


# ---------------------------------------------------------------------------
# 嵌入式层级评分表(分值在括号内, 无独立满分列, 父项+子项+规则列)
# ---------------------------------------------------------------------------

# 精简自项目1521真实 page24-25: 前置混入形式/资格符合性噪声行, 真实表头在中部;
# 分值以 `（N分）` 嵌在父级评审因素与子项评审因素单元格, 规则列无表头关键词。
EMBEDDED_HIERARCHY_TABLE_1521 = """[评分表 第24-25页]
|  |  | 交货期 | 符合招标文件要求 |
| --- | --- | --- | --- |
|  |  | 质量标准/要求 | 符合招标文件要求 |
| 条款号 |  | 评审因素 | 评审标准 |
| 2.2.1 |  | 分值构成 (总分100分) | 商务部分：25分 技术服务部分：45 分 投标报价：30分 |
| 2.2.3（1） | 商务评分 标准 （25分） | 实力及信誉 （5分） | 根据投标人企业资信、人员、效益、荣誉等方面综合评价，进行横向对比，酌情赋0-5分。 |
|  |  | 交货期与实施 方案 （5分） | 根据投标人提供的交货期与实施方案是否满足招标文件要求，酌情赋0-5分。 |
|  |  | 售后服务方案 （10 分） | 根据投标人提供的售后服务方案进行综合评分，酌情赋0-10分。 |
|  |  | 财务状况 （5分） | 根据投标人财务状况进行横向对比，酌情赋0-5分。 |
| 2.2.3（2） | 技术服务 评分标准 （45 分） | 产品参数与技 术方案 （20分） | 根据投标人提供的技术方案科学、合理、先进性，酌情赋0-20分。 |
|  |  | 产品质量与设 备性能 （15分） | 根据投标人提供的检测报告、认证证书等，酌情赋0-15分。 |
|  |  | 节能环保与认 证 （10 分） | 根据投标人提供的认证证书，酌情赋0-10分。 |
| 2.2.3（3） | 投标报价 评分标准 （30分） | 投标报价（30 分） | 当投标人的投标报价等于评标基准价时，得30分。 |
"""


def test_embedded_hierarchy_table_1521_extracts_child_items():
    """: 括号内嵌分值 + 父子层级表, 应取子项分值, 合计商务25+技术45+报价30=100。"""
    res = parse_scoring_table(EMBEDDED_HIERARCHY_TABLE_1521, section_path="第四章 评标办法")

    assert res.confident is True
    by_name = {it.score_item_name.replace(" ", ""): it for it in res.items}

    # 8 个子项评分项, 父级(25/45/30)与"分值构成(总分100)"不单独计分
    assert by_name["实力及信誉"].score_weight == 5.0
    assert by_name["交货期与实施方案"].score_weight == 5.0
    assert by_name["售后服务方案"].score_weight == 10.0
    assert by_name["财务状况"].score_weight == 5.0
    assert by_name["产品参数与技术方案"].score_weight == 20.0
    assert by_name["产品质量与设备性能"].score_weight == 15.0
    assert by_name["节能环保与认证"].score_weight == 10.0
    assert by_name["投标报价"].score_weight == 30.0
    assert "商务评分标准" not in by_name
    assert "分值构成" not in by_name
    assert sum(it.score_weight for it in res.items) == 100.0


def test_embedded_hierarchy_table_1521_infers_volume_from_parent():
    """: 子项应按父项名归卷, 商务→commercial, 技术服务→technical, 报价→price。"""
    res = parse_scoring_table(EMBEDDED_HIERARCHY_TABLE_1521, section_path="第四章 评标办法")
    by_name = {it.score_item_name.replace(" ", ""): it for it in res.items}

    assert by_name["实力及信誉"].volume == "commercial"
    assert by_name["产品参数与技术方案"].volume == "technical"
    assert by_name["投标报价"].volume == "price"


EMBEDDED_BUSINESS_TABLE_1550 = """[评分表 第26-28页]
| 项目 | 评审要素 | 评审内容 |
| --- | --- | --- |
| 2.绿色低碳 评价 （12分） | 绿色发展规划 | 对企业的绿色发展顶层规划及执行情况进行评价，建立较好得分。 |
| 3.科研创新 评价（7分） | 企业创新成果 | 对供应商投标截止日近3年内取得的科技成果情况进行综合评价。 |
| 4.综合评价 （81分） | 投标响应 | 对商务投标文件的整体情况进行评审，优：提交了完整的商务投标文件且整体情况优良。报价质量：对投标报价完整性、准确性、规范性、合理性进行综合评审。 |
"""


def test_embedded_business_table_1550_uses_table_level_volume_signal():
    """: 商务评分表表头/父项无商务字样时, 应从全表商务投标文件信号归 commercial。"""
    res = parse_scoring_table(EMBEDDED_BUSINESS_TABLE_1550, section_path="应答人须知前附表附件一")

    assert res.confident is True
    by_name = {it.score_item_name.replace(" ", ""): it for it in res.items}
    assert next(v for k, v in by_name.items() if k.endswith("绿色低碳评价")).score_weight == 12.0
    assert next(v for k, v in by_name.items() if k.endswith("科研创新评价")).score_weight == 7.0
    assert next(v for k, v in by_name.items() if k.endswith("综合评价")).score_weight == 81.0
    assert {it.volume for it in res.items} == {"commercial"}


FOLDED_WEIGHTED_VOLUME_TABLE_1574 = """[评分表 第42-43页]
|  | 权重 | 评审因素 | 分值 | 评 审 标 准 |
| --- | --- | --- | --- | --- |
| 资 信 标 （100 分） | 10% | 体系认证 | 0-30 | 每提供一项符合要求的证书得10分，本条满分30分。 |
| 技术 标 （100 分） | 50% | 项目实施方案 | 0-18 | 方案科学、合理、规范、具有可操作性的得12（不含）-18分。 |
| 商 务 标 （100 分） | 40% | 投标报价 | 0-100 | 投标人报价等于评标基准价的，得100分。 |
"""


def test_folded_weighted_volume_table_1574_extracts_volume_defs():
    """: `权重|评审因素|分值`型三卷各100表应建模卷权重, 不应误报300/100。

 更新: 区间分值列走明细路径后, 评分项须为卷内子项粒度(体系认证30等),
 卷标签合并单元格只提供卷别/卷定义, 不再折叠为 100 分卷级评分项。
 """
    res = parse_scoring_table(FOLDED_WEIGHTED_VOLUME_TABLE_1574, section_path="评标方法及评价标准")

    assert res.confident is True
    by_name = {it.score_item_name.replace(" ", ""): it for it in res.items}
    assert "资信标" not in by_name
    assert by_name["体系认证"].score_weight == 30.0
    assert by_name["体系认证"].volume == "qualification"
    assert by_name["项目实施方案"].score_weight == 18.0
    assert by_name["项目实施方案"].volume == "technical"
    assert by_name["投标报价"].score_weight == 100.0
    assert by_name["投标报价"].volume == "commercial"

    assert set(res.volume_defs.keys()) == {"qualification", "technical", "commercial"}
    assert res.volume_defs["qualification"].weight == 0.1
    assert res.volume_defs["technical"].weight == 0.5
    assert res.volume_defs["commercial"].weight == 0.4
    assert all(defn.volume_max == 100.0 for defn in res.volume_defs.values())


# ---------------------------------------------------------------------------
# 独立分值列但表头错位/前导小表(分值构成)干扰, 需按列统计逐行识别
# ---------------------------------------------------------------------------

# 精简自项目1523真实 page5-7: 前导"分值构成/评标基准价"小表与真实评分表共表头列数,
# 真实表头`评分因素|评分标准|分值`被埋进数据; 序号在col0, 部分在col1, 子项名在col2,
# 规则在col3, 分值在col4(2/2/6/25/5/60=100)。表头列名与数据列错位。
NUMERIC_SCORE_COLUMN_TABLE_1523 = """[评分表 第5-7页]
| 条款内容 |  |  | 编列内容 | 分值 |
| --- | --- | --- | --- | --- |
| 分值构成 |  |  | 商务分：10分 技术分：30分 价格分：60分 | 100 |
| 评标基准价计算方 法 |  |  | 1、评标基准价=所有有效投标报价的算术平均值*95%。 | / |
| 评分因素 |  |  | 评分标准 | 分值 |
| 1 | 商务 部分 | 企业 规模 | 提供员工近六个月社保证明，员工大于等于10人得 2 分，少于10人不得分。 | 2 |
| 2 |  | 社会 保险 | 提供项目负责人近6个月社保记录，完全提供得2分，未提供不得分。 | 2 |
| 3 |  | 经营 业绩 | 提供2022年至今类似项目维保合同，每提供1份得1分，最多得6分。 | 6 |
| 4 | 技术 部分 | 性能、 方案 和措施 | 维修品种齐全得8分；维保方案合理得7分；检验方案得6分；话台维修得4分。 | 25 |
| 5 |  | 定期 培训 | 维修人员定期专项培训，每提供1项得1分，最多得5分。 | 5 |
| 6 | 投标 报价 | 投标 报价 | 投标报价=评标基准价，得分=60；高于或低于按公式扣分。 | 60 |
"""


THREE_COL_SCORE_TABLE_1545 = """[评分表 第59-62页]
| 项目 | 评 定 内 容 及 标 准 | 得分 |
| --- | --- | --- |
| 一、价格部分 |  | 20 |
| 价格 | 以满足招标文件要求的最低投标报价为评标基准价，得满分 20 分，其他投标报价得分=（评标基准价/其他投标报价）×20。 | 20 |
| 二、商务部分 |  | 13 |
| 合同案例 | 投标人提供投标截止时间前三年完成的类似业绩，每提供一个签署的合同案例得 2 分，最高得 4 分。 | 4 |
| 投标人能力 | 每提供一个云安全或服务能力相关认证得 0.5 分，最高得 3 分。 | 3 |
| 团队人员 | 项目负责人证书得 2 分；团队成员每有一个证书得 1 分，满分 4 分。 | 6 |
| 三、技术部分 |  | 42 |
| 项目需求理解 | 对项目现状、网络组网、存储和业务系统需求逐条阐述，满分 8 分。 | 8 |
| 项目规划设计 | 提供整体架构、网络架构、计算存储规划和应用系统规划，满分 8 分。 | 8 |
| 数据中心及网络设计 | 提供数据中心、网络、数据及存储设计方案，满分 6 分。 | 6 |
| 应用系统设计 | 提供应用系统功能详细方案，满分 10 分。 | 10 |
| 性能与配置 | 根据投标产品资料完整性、性能稳定性等综合打分，满分 10 分。 | 10 |
"""


def test_numeric_score_column_table_1523_extracts_items_by_column_stats():
    """: 有独立分值列但表头错位, 应按列统计逐行识别评分项, 不落 LLM/符合性跳过。"""
    res = parse_scoring_table(NUMERIC_SCORE_COLUMN_TABLE_1523, section_path="第四章 评标办法")

    assert res.confident is True
    by_name = {it.score_item_name.replace(" ", ""): it for it in res.items}

    assert by_name["企业规模"].score_weight == 2.0
    assert by_name["社会保险"].score_weight == 2.0
    assert by_name["经营业绩"].score_weight == 6.0
    assert by_name["性能、方案和措施"].score_weight == 25.0
    assert by_name["定期培训"].score_weight == 5.0
    assert by_name["投标报价"].score_weight == 60.0
    assert "分值构成" not in by_name
    assert "评标基准价计算方法" not in by_name
    assert sum(it.score_weight for it in res.items) == 100.0


def test_three_column_score_table_1545_prefers_children_not_subtotals():
    """: 三列表含价格公式与小计行时, 不应因价格公式局部命中而漏掉其余子项。"""
    res = parse_scoring_table(THREE_COL_SCORE_TABLE_1545, section_path="第三章 评标标准和评标方法")

    assert res.confident is True
    by_name = {it.score_item_name.replace(" ", ""): it for it in res.items}

    assert by_name["价格"].score_weight == 20.0
    assert by_name["合同案例"].score_weight == 4.0
    assert by_name["投标人能力"].score_weight == 3.0
    assert by_name["团队人员"].score_weight == 6.0
    assert by_name["项目需求理解"].score_weight == 8.0
    assert by_name["项目规划设计"].score_weight == 8.0
    assert by_name["数据中心及网络设计"].score_weight == 6.0
    assert by_name["应用系统设计"].score_weight == 10.0
    assert by_name["性能与配置"].score_weight == 10.0
    assert "一、价格部分" not in by_name
    assert "二、商务部分" not in by_name
    assert "三、技术部分" not in by_name
    assert sum(it.score_weight for it in res.items) == 75.0


def test_numeric_score_column_table_1523_infers_volume_from_group():
    """: 商务部分→commercial, 技术部分→technical, 投标报价→price。"""
    res = parse_scoring_table(NUMERIC_SCORE_COLUMN_TABLE_1523, section_path="第四章 评标办法")
    by_name = {it.score_item_name.replace(" ", ""): it for it in res.items}

    assert by_name["企业规模"].volume == "commercial"
    assert by_name["社会保险"].volume == "commercial"
    assert by_name["经营业绩"].volume == "commercial"
    assert by_name["性能、方案和措施"].volume == "technical"
    assert by_name["投标报价"].volume == "price"


# 真实场景(项目1585): 第一列分组合并单元格声明"商务标34分/技术标66分"。
# 组内"报价"是商务标评分项, 项名中的报价词不得覆盖分组归属为独立 price 卷。
GROUP_SUBTOTAL_VOLUME_TABLE_1585 = """[评分表 第33-34页]
| 评审项目 |  | 分值 | 评审要素 |
| --- | --- | --- | --- |
| 商 务 标 34 分 | 报价 | 30分 | 满足磋商文件要求且最终价格最低的报价为基准价，其价格分为满分（30分）。 |
|  | 类似项目业绩 | 4分 | 供应商近三年以来签订的类似项目案例，每提供1份得2分，最高得4分。 |
| 技 术 标 66 分 | 施工方案与技术措施 | 20分 | 本项最高得20分，每出现一处瑕疵扣分。 |
|  | 质量管理体系与措施 | 18分 | 本项最高得18分，每出现一处瑕疵扣2分。 |
|  | 安全管理体系与措施 | 15分 | 本项最高得15分，每出现一处瑕疵扣2分。 |
|  | 环境保护管理体系与措施 | 5分 | 本项最高得5分。 |
|  | 工程进度计划与措施 | 8分 | 本项最高得8分。 |
"""


def test_group_subtotal_volume_table_1585_keeps_bid_price_under_commercial_group():
    """: 分组卷别是权威证据, 组内"报价"不得被拆成独立价格卷。"""
    res = parse_scoring_table(GROUP_SUBTOTAL_VOLUME_TABLE_1585, section_path="评分相关章节")

    assert res.confident is True
    by_name = {it.score_item_name.replace(" ", ""): it for it in res.items}
    assert len(res.items) == 7
    assert by_name["报价"].score_weight == 30.0
    assert by_name["报价"].volume == "commercial"
    assert by_name["报价"].volume_label == "商务标"
    assert by_name["类似项目业绩"].volume == "commercial"
    assert by_name["类似项目业绩"].volume_label == "商务标"
    assert by_name["施工方案与技术措施"].volume == "technical"
    assert by_name["施工方案与技术措施"].volume_label == "技术标"
    assert {k: v.volume_max for k, v in res.volume_defs.items()} == {
        "commercial": 34.0,
        "technical": 66.0,
    }
    assert {k: v.weight for k, v in res.volume_defs.items()} == {
        "commercial": 0.34,
        "technical": 0.66,
    }


EMBEDDED_BUSINESS_PARENT_PRICE_CHILD_TABLE_1585 = """[评分表 第33-34页]
| 项目 | 子项 | 评分标准 |
| --- | --- | --- |
| 商务评分标准（34分） | 报价（30分） | 满足磋商文件要求且最终价格最低的报价为基准价，得30分。 |
|  | 类似项目业绩（4分） | 每提供1份类似项目案例得2分，最高得4分。 |
"""


def test_embedded_price_child_inherits_explicit_commercial_parent_volume():
    """: 嵌入式路径中父分组显式商务时, 子项报价不得覆盖为 price。"""
    res = parse_scoring_table(EMBEDDED_BUSINESS_PARENT_PRICE_CHILD_TABLE_1585)
    by_name = {it.score_item_name.replace(" ", ""): it for it in res.items}

    assert by_name["报价"].volume == "commercial"
    assert by_name["报价"].volume_label == "商务评分标准"
    assert by_name["类似项目业绩"].volume == "commercial"
    assert by_name["类似项目业绩"].volume_label == "商务评分标准"


PACKAGE1_OBJECTIVE_ATTRIBUTE_TABLE_1552 = """[评分表 第36-38页]
| 项目 | 分值 | 是 否 客 观 项 | 描述 |
| --- | --- | --- | --- |
| 1、参数响应情况1 | 28.00 | 是 | 根据技术指标响应情况评分，▲指标每负偏离一项扣2.8分，共计28分。 |
| 2、参数响应情况2 | 20.00 | 是 | 其他未标注符号的技术指标项每负偏离一项扣0.25分，共计20分。 |
| 3、产品能力证明1 | 2.00 | 是 | 提供满足要求的检测报告得2分，未提供不得分。 |
| 4、产品能力证明2 | 2.00 | 是 | 提供满足要求的检测报告得2分，未提供不得分。 |
| 5、产品能力证明3 | 2.00 | 是 | 提供满足要求的检测报告得2分，未提供不得分。 |
| 6、产品能力证明4 | 2.00 | 是 | 提供满足要求的检测报告得2分，未提供不得分。 |
| 1、投标人实力1 | 1.00 | 是 | 具备隐私信息管理体系认证证书得1分。 |
| 2、投标人实力2 | 1.00 | 是 | 提供相关软件著作权，每项0.5分，满分1分。 |
| 3、项目负责人 | 3.00 | 是 | 同时具有三项或以上证书得3分。 |
| 4、技术负责人 | 3.00 | 是 | 同时具有三项或以上证书得3分。 |
| 5、质保期期限 | 1.50 | 是 | 每增加一年得0.5分，满分1.5分。 |
| 6、培训方案 | 1.50 | 是 | 培训方案完整得1.5分。 |
| 7、售后服务方案 | 3.00 | 否 | 方案要点齐全、内容适用于本项目得3分。 |
"""


def test_numeric_score_column_table_1552_ignores_yes_no_attribute_column():
    """: “是否客观项”的是/否二值列不是评分对象列，不能污染名称并触发去重吞分。"""
    from app.core.extractor.scoring.column_role_inference import infer_column_roles
    from app.core.extractor.scoring.scoring_table_parser import _markdown_to_grid

    grid = _markdown_to_grid(PACKAGE1_OBJECTIVE_ATTRIBUTE_TABLE_1552)
    roles = infer_column_roles(grid)
    assert roles is not None
    assert roles.name == 0
    assert roles.score == 1
    assert roles.rule == 3

    res = parse_scoring_table(PACKAGE1_OBJECTIVE_ATTRIBUTE_TABLE_1552, section_path="采购包1")
    assert res.confident is True
    assert len(res.items) == 13
    assert sum(item.score_weight for item in res.items) == 70.0
    names = {item.score_item_name.replace(" ", "") for item in res.items}
    assert "是" not in names
    assert "否" not in names
    assert "产品能力证明1" in "".join(names)


def test_price_formula_rule_column_enters_score_rules_bug631():
    """: 价格项公式在“评审内容”列时也必须进入 score_rules, 不应误报规则缺失。"""
    markdown = """[评分表 第11-11页]
| 序号 | 评审项目 | 分值 | 评审内容 |
| --- | --- | --- | --- |
| 1 | 报价部分 （70分） | 70分 | 低价优先法，价格分=(Pmin／P)×A，A为价格权重分；价格分最高得满分。 |
"""

    res = parse_scoring_table(markdown)

    assert res.confident is True
    assert len(res.items) == 1
    item = res.items[0]
    # 同上: 尾部括号分值为排版装饰, 真值项名为"报价部分"
    assert item.score_item_name == "报价部分"
    assert item.score_weight == 70.0
    assert item.score_rules == ["低价优先法，价格分=(Pmin／P)×A，A为价格权重分；价格分最高得满分。"]


# 真实场景(项目1539 p39-40 拼接): ★合同条款表与"技术规范书对应章节"索引表拼接,
# "对应章节"列的章节号(3/6/7/8)满足"短数值+左侧短中文对象"两元, 但全表无评分规则语义,
# 曾被列统计解析当分值产出假评分项"技术规范书 3分"。
CHAPTER_INDEX_STITCHED_TABLE_1539 = """[评分表 第39-40页]
| 序号 | 条款名称 | 编列内容 |
| --- | --- | --- |
| 1 | ★合同期限 | 合同生效之日起，直至卖方履行完毕售后、维保服务等本合同项下义务之日为止。 |
| 2 | ★履约约束方式 | 卖方应提供合同金额5%的履约保证金。买方有权直接扣除违约金或赔偿款。 |
| 评审因素 | 评审标准 | 技术规范书 对应章节 |
| 技术规范书 | 3.3 软件系统要求3.3.6★兼容性及升级3.3.7.★部署方式 | 3 |
|  | 6.2 ★工程进度要求 | 6 |
|  | 7.1 ★验收标准 | 7 |
|  | 8 ★技术支持、培训和服务 | 8 |
"""


def test_numeric_column_parser_rejects_chapter_index_rows_without_rule_semantics():
    """: 行内无"得X分/N分/酌情"评分规则语义时, 列统计路径不得产出评分项。"""
    res = parse_scoring_table(CHAPTER_INDEX_STITCHED_TABLE_1539, section_path="评分相关章节")

    assert res.items == []
    names = [it.score_item_name for it in res.items]
    assert "技术规范书" not in names


# 装配器 canonical 渲染形态(项目1547 精简)——prelude 分值构成行 + 分组标题行 +
# 归一四列数据行。parser 须按分组行继承卷别、解析组基准卷定义。
CANONICAL_ASSEMBLED_TABLE_1547 = """[评分表 第67-71页]
| 分组 | 评分因素 | 评分内容与标准 | 分值 |
| --- | --- | --- | --- |
|  |  | 2.2.1 分值构成（总分100分） 商务技术评审部分： 20 分； 价格评审部分： 80 分。 |  |
| 商务 技术 评审 |  |  |  |
|  | 管理体 系认证 | 投标人具备有效的质量管理体系认证证书、职业健康安全管理体系认证证书、环境管理体系认证证书，全部具备得1分。 | 1 |
|  | 企业业 绩 | 根据同类施工业绩合同累计金额进行评分，合同累计金额≥1.4亿元得2分。本项满分2分。 | 2 |
|  | 企业业绩(2) | 根据同类施工业绩数量进行评分，每提供一个合同得0.5分，满分2分。 | 2 |
|  | 团队人 员能力 | 根据投标人拟投入项目的人员职称证书情况进行评分，本项最高得1分。 | 2 |
|  | 财务 状况 | 根据2023年财务报表净利润进行评分，前30%有效投标人得满分1分。 | 2 |
|  | 履约 评价 | 根据考核得分由高到低排名，排名前30%（含）得3分。 | 3 |
|  | 施工质 量保障 措施 | 根据施工质量保障措施进行评分，内容全面得[2,3]分。 | 3 |
|  | 施工安 全保障 措施 | 根据施工安全保障措施进行评分，内容全面得[2,3]分。 | 3 |
|  | 施工进 度保障 措施 | 根据施工进度保障措施进行评分，内容全面得[1.3,2]分。 | 2 |
| 价格 评审 |  |  |  |
|  | 基准价 法 | 所有有效投标人中，去掉一定数量的最高最低评标价后计算基准价，按公式计算得分。 | 80 |
"""


def test_canonical_assembled_table_1547_parses_groups_and_flat_defs():
    """: canonical 段须解析出组基准(商务技术20/价格80)且按分组行归卷。"""
    res = parse_scoring_table(CANONICAL_ASSEMBLED_TABLE_1547, section_path="评标办法前附表")

    assert res.confident is True
    by_name = {it.score_item_name.replace(" ", ""): it for it in res.items}
    assert len(res.items) == 10
    assert by_name["基准价法"].score_weight == 80.0
    assert by_name["基准价法"].volume == "price"
    assert by_name["管理体系认证"].volume == "commercial"
    assert by_name["企业业绩"].score_weight == 2.0
    assert by_name["企业业绩(2)"].score_weight == 2.0
    commercial_sum = sum(it.score_weight for it in res.items if it.volume == "commercial")
    assert commercial_sum == 20.0
    # 平铺分值构成 → 组基准卷定义(最终得分制: volume_max=组分值)
    assert res.volume_defs["commercial"].volume_max == 20.0
    assert res.volume_defs["price"].volume_max == 80.0
    assert res.volume_defs["commercial"].weight == 0.2


# ---------------------------------------------------------------------------
# P0 A1: 续行 rule 列内容丢失防御测试（A1-fix）
# 场景: 满分列为空（续行）但规则列有续写文本 → 上一项 description 和 score_rules 均应包含
# ---------------------------------------------------------------------------

DETAIL_CONTINUATION_WITH_RULE_COL = """[评分表 第20-21页]
| 评审大类 | 评分内容 | 满分分值 | 评分标准 |
| --- | --- | --- | --- |
| 整体建设 | 根据技术方案综合评分。 | 6.0 | 核心维度一：架构合理性。 |
|  |  |  | 续写维度二：技术可行性评估。 |
"""


def test_continuation_row_with_rule_col_merges_into_score_rules():
    """P0 A1: 续行满分列为空但规则列有内容，应并入上一项 description 且追加 score_rules。"""
    res = parse_scoring_table(DETAIL_CONTINUATION_WITH_RULE_COL)
    assert res.confident is True
    assert len(res.items) == 1
    item = res.items[0]
    assert item.score_weight == 6.0
    # description 应包含续写规则文本
    assert "续写维度二" in item.description, f"description={item.description!r}"
    # score_rules 也应包含续写文本
    assert any("续写维度二" in r for r in item.score_rules), \
        f"score_rules 未包含续写文本，实际={item.score_rules}"


# ---------------------------------------------------------------------------
# P0 A6: rule forward-fill 不得跨独立评分项（A6-fix）
# 场景: 新评分项有自己的 cat_val（非空），规则列为空 → 不应继承上一项规则
# ---------------------------------------------------------------------------

DETAIL_NO_RULE_FORWARD_FILL = """[评分表 第10-10页]
| 评审大类 | 评分内容 | 满分分值 | 评分标准 |
| --- | --- | --- | --- |
| 技术类 | 技术方案综合评分 | 10 | 技术规则：优秀10分，良好7分。 |
| 价格类 | 价格公式评分 | 80 |  |
"""


def test_independent_item_does_not_inherit_previous_rule():
    """P0 A6: 独立新评分项（cat_val 非空）规则列为空时，score_rules 不应继承上一项规则。"""
    res = parse_scoring_table(DETAIL_NO_RULE_FORWARD_FILL)
    assert res.confident is True
    assert len(res.items) == 2
    # 技术类应有自己的规则
    tech_items = [it for it in res.items if "技术" in it.score_item_name]
    assert tech_items, "应能解析出技术类评分项"
    assert any("技术规则" in r for r in tech_items[0].score_rules), \
        f"技术类 score_rules={tech_items[0].score_rules}"
    # 价格类规则列为空，不应继承技术类规则
    price_items = [it for it in res.items if "价格" in it.score_item_name]
    assert price_items, "应能解析出价格类评分项"
    assert not any("技术规则" in r for r in price_items[0].score_rules), \
        f"价格类不应继承技术类规则，实际 score_rules={price_items[0].score_rules}"


# ---------------------------------------------------------------------------
# 真实场景(项目1649 chunk 163911 精简): 表头为"评分项目|评分内容|分值",
# 规则列("评分内容")位于分值列左侧, 且 content 角色被"评分项目"列抢占 →
# 规则整列曾被丢弃(score_rules 全空, 确认页误报"评审标准疑似缺失")。
# ---------------------------------------------------------------------------

RULE_LEFT_OF_SCORE_TABLE_1649 = """[评分表 第50-52页]
| 评分 项目 | 评分内容 | 分值 |
| --- | --- | --- |
| 技术商务部分（70分） |  |  |
| 类似 业绩 | 供应商自2022年1月1日（以合同签订时间为准）以来完成过类似项目业绩，每个得0.5分，最高得1分。 | 0-1分 |
| 质量 保证 措施 | 根据本项目的质量及工期服务作出承诺并提供保障措施，措施方案完整、全面合理，科学可操作性强的得7（不含）-10分。 | 0-10分 |
| 安装 方案 | 根据供应商提供的安装方案（现场平面布置方案、安装方法、技术措施等内容）进行综合评议：安装方案内容详细的得4（不含）-6分； | 0-6分 |
|  | 安装方案内容较详细的得2（不含）-4（含）分；安装方案内容简单的得0-2分（含）。 |  |
| 合理 化建 议 | 根据供应商提出的针对本项目的合理化建议进行综合评议：建议针对性强、科学合理、可落地，得4（不含）-5分。 | 0-5分 |
| 报价部分（30分） |  |  |
| 投标 报价 | 本项目最高限价：120.6424万元。基准价为满足评标要求且响应价格最低的响应报价，响应报价得分=(基准价／响应报价)×30。 | 0-30分 |
"""


def test_rule_column_left_of_score_enters_score_rules_1649():
    """: 规则列在分值列左侧且 content 角色被抢占时, 规则文本不得整列丢弃。"""
    res = parse_scoring_table(RULE_LEFT_OF_SCORE_TABLE_1649, section_path="第五章 评标办法")

    assert res.confident is True
    by_name = {it.score_item_name.replace(" ", ""): it for it in res.items}
    assert len(res.items) == 5

    assert by_name["类似业绩"].score_weight == 1.0
    assert any("每个得0.5分" in r for r in by_name["类似业绩"].score_rules), \
        f"规则列文本未进入 score_rules: {by_name['类似业绩'].score_rules}"
    assert by_name["质量保证措施"].score_weight == 10.0
    assert by_name["合理化建议"].score_weight == 5.0
    assert by_name["投标报价"].score_weight == 30.0
    assert any("基准价" in r for r in by_name["投标报价"].score_rules)
    # 全部计分项都必须带评审规则(确认页 missing_rule 误报的根源断言)
    assert all(it.score_rules for it in res.items), \
        f"存在规则为空的评分项: {[(it.score_item_name, it.score_rules) for it in res.items]}"
    # description 应包含规则文本而非仅项名
    assert "每个得0.5分" in by_name["类似业绩"].description


def test_rule_left_table_1649_volume_and_heading_isolation():
    """: 分组标题行只切换卷别, 组名不得并入上一项 description; 续行仍正常合并。"""
    res = parse_scoring_table(RULE_LEFT_OF_SCORE_TABLE_1649, section_path="第五章 评标办法")
    by_name = {it.score_item_name.replace(" ", ""): it for it in res.items}

    # 卷归属: 组标题行驱动("技术商务部分"→commercial 带原文标签, "报价部分"→price)
    assert by_name["类似业绩"].volume == "commercial"
    assert by_name["类似业绩"].volume_label == "技术商务部分"
    assert by_name["投标报价"].volume == "price"
    assert by_name["投标报价"].volume_label == "报价部分"
    # 纯分组标题行"报价部分（30分）"不得作为续行并入上一项"合理化建议"
    assert "报价部分" not in by_name["合理化建议"].description
    # 真续行(满分列空、规则列有文本)仍并入上一项
    assert "安装方案内容较详细" in by_name["安装方案"].description


def test_scoring_table_parser_reads_optional_lot_from_header():
    """: 页头 lot 标签写入 item.lot; 无 lot 页头保持 None 兼容旧文档。"""
    table_with_lot = """[评分表 第36-38页 · 采购包1]
| 项目 | 分值 | 评分标准 |
| --- | --- | --- |
| 技术方案 | 70 | 根据技术方案评分，满分70分。 |
| 服务方案 | 20 | 根据服务方案评分，满分20分。 |
| 进度方案 | 10 | 根据进度方案评分，满分10分。 |
"""
    table_without_lot = """[评分表 第36-38页]
| 项目 | 分值 | 评分标准 |
| --- | --- | --- |
| 技术方案 | 70 | 根据技术方案评分，满分70分。 |
| 服务方案 | 20 | 根据服务方案评分，满分20分。 |
| 进度方案 | 10 | 根据进度方案评分，满分10分。 |
"""

    with_lot = parse_scoring_table(table_with_lot)
    without_lot = parse_scoring_table(table_without_lot)

    assert {item.lot for item in with_lot.items} == {"采购包1"}
    assert {item.lot for item in without_lot.items} == {None}
