"""项目2231评分项提取防回归。

固定真实样本暴露的泛化风险:
- 命名净化语义标签被"不可重复计分"交叉引用/免责语境误命中, 把"企业业绩"行
 错命名为"项目经理及设计负责人业绩";
- 派生名碰撞后两条不同物理行身份键(卷+页+满分+归一名)完全一致,
 `_dedupe_exact` 误当解析重复行合并吞行(资信卷 100→96);
- 三卷独立百分制(技术100+资信100+商务100)因单卷缺口整体失效,
 回退全局口径误报"全集296/100"。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.extractor.scoring.score_totals import verify_score_totals  # noqa: E402
from app.core.extractor.scoring.scoring_item_naming import (  # noqa: E402
    normalize_scoring_item_name,
)

pytestmark = pytest.mark.scoring


# 项目2231真实评分表 chunk(document_id=1769, chunk_id=177845)的资信/商务段。
# 技术段19行结构同质(无命名污染风险), golden 保留资信段全部5行(碰撞现场)
# 与商务段, 外加1行技术段代表, 锁定"资信卷各行独立存活、卷内合计闭合"。
_GROUP_RULE = (
    "技术标(汇总规则:当专家数量小于等于4位，取去掉0个最高分、0个最低分后的算术平均值;"
    "当专家数量大于4位，取去掉1个最高分、1个最低分后的算术平均值;)"
)

PROJECT2231_QUALIFICATION_TABLE = f"""[评分表 第1-6页]
| 分组 | 评分因素 | 评分内容与标准 | 分值 |
| --- | --- | --- | --- |
|  | {_GROUP_RULE} | 设计任务书优化：根据招标人提供的设计任务书进行优化，提出优化建议，满足工程设计要求，设计质量保证措施等具体、可行，评委根据投标文件情况分为一般、良、优，分别酌情得10-14分 14-17分、17-20分，内容不全酌情扣分， 若此条缺项不得分。 设计任务书优化 | 20 |
|  | 资信标 | 拟派项目部应配备专门的项目经理、技术负责人、设计负责人、采购负责人、 质量负责人、安全负责人、商务负责人等，并应有明确的职权划分，评委根据投标文件情况分为一般、良、优，分别酌情得5-7分、7-11分、11-13分，内容不全酌情扣分, 若此条缺项不得分。 注： 1、投标人的投标文件中拟派项目管理班子人员达不到项目管理班子人员配备最低表要求的（见本章第2.2.5款的技术标不得分，且商务标不参与评标 项目管理机构配备 基准值计算。 2、关键岗位是指：施工质检（量）、安全、材料、预算（造价机械（设备）管理、劳管、资料等工作岗位。 | 13 |
|  | 资信标(2) | 拟派项目管理主要人员（技术负责人、 设计负责人、关键岗位人员）具有 工程经验（附有相关证明），评委根据投标文件情况分为一般、良、优，分别酌情得3-6分、6-10分、10-12分，内容不全酌情扣分，若此条缺项不得分。 注： 1、"项目班子管理人员具有工程经验"，应按照施工合同中约定的相关人员为依据，作为该项赋分的主要依据。 拟派项目管理主要人员工程经验 | 12 |
|  | 资信标(3) | 根据《关于停止济南市房屋建筑市场信用评价管理工作的通知》，济南市房屋建筑市场信用评价评分和评级工作停止 ，信誉评价结果均按满分计取。 信誉评价结果 | 67 |
|  | 资信标(4) | 申请人近5年（2020年08月10日至2025 年08月10日）具有类似业绩的，每个得4分，最多得4分。 ①业绩须提供合同协议书、竣工验收证明，类似业绩时间以竣工验收时间为准。②企业业绩与项目经理业绩、设计负责人业绩不可以重复计分。 注：以上要求不得缺项，缺项或不符合要求的不得分。 企业业绩 | 4 |
|  | 资信标(5) | 1、项目经理近5年（2020年08月10 日至2025年08月10日）以项目负责人（项目经理）身份承担过单项合同建筑面积24000平方米及以上或单项合同额 1.5亿元及以上的公共建筑（住宅、厂房、仓库除外）施工总承包或工程总承包（EPC）业绩，每个得2分，最多得2 分。 ①业绩须提供合同协议书、竣工验收证明，类似业绩时间以竣工验收时间为准。 ②合同或验收证明材料中应体现项目经理姓名，如项目经理姓名不一致 项目经理及设计负责人业绩 以验收证明为准。 ③企业业绩与项目经理业绩、设计负责人业绩不可以重复计分。 注：以上要求不得缺项，缺项或不符合要求的不得分 。 2、设计负责人近5（2020年08月10日至2025年08月10日 ）以设计负责人身份承担过单项合同建筑面积24000平方米及以上公共建筑（住宅、厂房、仓库除外）设计（含施工图设计）业绩，每项加2分，最多加2 分。 | 4 |
"""

_ENTERPRISE_PERF_RULE = (
    "申请人近5年（2020年08月10日至2025 年08月10日）具有类似业绩的，每个得4分，最多得4分。 "
    "①业绩须提供合同协议书、竣工验收证明，类似业绩时间以竣工验收时间为准。"
    "②企业业绩与项目经理业绩、设计负责人业绩不可以重复计分。 "
    "注：以上要求不得缺项，缺项或不符合要求的不得分。 企业业绩"
)

_COMPOSITE_PERF_RULE = (
    "1、项目经理近5年（2020年08月10 日至2025年08月10日）以项目负责人（项目经理）身份"
    "承担过单项合同建筑面积24000平方米及以上的公共建筑施工总承包或工程总承包（EPC）业绩，"
    "每个得2分，最多得2分。 2、设计负责人近5年以设计负责人身份承担过单项合同建筑面积"
    "24000平方米及以上公共建筑设计业绩，每项加2分，最多加2分。 "
    "③企业业绩与项目经理业绩、设计负责人业绩不可以重复计分。 "
    "注：以上要求不得缺项，缺项或不符合要求的不得分 。"
)

_PRICE_FORMULA_RULE = (
    "投标总报价超出招标控制价范围为废标。 总价评标基准值（C1）=总价招标控制价（A1）"
    "×下浮系数（K）×权重（Q1）+ 有效投标报价的算术平均值（B1）×（1-Q1）。 "
    "与报价评标基准值（C）相同得满分; 低于总价评标基准值（C），每低1%扣1分，扣完为止;"
)


class TestScoringItemNamingCrossReferenceGuard:
    """: 命名净化不得被交叉引用/免责语境误导。"""

    def test_enterprise_perf_row_named_by_tail_label(self) -> None:
        """企业业绩行: 规则尾部自带权威标签, 不得因免责句被语义标签改名。"""
        assert normalize_scoring_item_name("资信标(4)", _ENTERPRISE_PERF_RULE) == "企业业绩"

    def test_composite_perf_row_keeps_semantic_label(self) -> None:
        """ 回归保护: 真复合人员业绩行(无合法尾标签)仍走语义标签。"""
        assert (
            normalize_scoring_item_name("资信标(5)", _COMPOSITE_PERF_RULE)
            == "项目经理及设计负责人业绩"
        )

    def test_price_formula_row_keeps_semantic_label(self) -> None:
        """ 回归保护: 价格公式行(尾部为扣分语句)仍走语义标签。"""
        assert normalize_scoring_item_name("商务标", _PRICE_FORMULA_RULE) == "投标报价"


# 阶段3(2026-08-31): _dedupe_exact 物理行守恒用例与资信卷链 golden 随确定性链
# 去重助手删除; 命名净化与独立百分制判定用例保留。


class TestProject2231QualificationGolden:
    """真实chunk golden: 三卷独立百分制判定。"""

    def test_independent_100_volumes_pass_totals_check(self) -> None:
        """三卷各100时独立百分制判定必须通过, 不得回退全局300/100误报。"""
        from types import SimpleNamespace

        items = [
            SimpleNamespace(score_weight=100.0, volume="technical"),
            SimpleNamespace(score_weight=100.0, volume="qualification"),
            SimpleNamespace(score_weight=100.0, volume="commercial"),
        ]
        report = verify_score_totals(items)
        assert report.needs_review is False
        assert report.within_tolerance is True
        assert "独立百分制" in report.note
