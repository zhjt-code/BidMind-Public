"""P0/P1 防御测试: 合同隔离 + 噪音降级 + 评审意义最终判定 + 分组透传。

映射真实问题:
- 问题二: 拟签合同/合同格式文本被误提取为招标要求 -> contract_classifier 隔离。
- 问题一: 商务噪音(面向中小企业/串通投标纪律/异议渠道/踏勘)占满确认页 -> noise_classifier 降级。
- 合并阶段可能丢失降级标记 -> relevance_resolver 入库前按描述二次兜底(仅降级不升级)。
- 确认页分组需透传 review_relevance(组内取最可见级别)。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.section_context import classify_section_phase  # noqa: E402
from app.core.extractor.tools.noise_classifier import (  # noqa: E402
    classify_noise_relevance,
    detect_template_placeholder,
)
from app.core.extractor.relevance_resolver import resolve_review_relevance  # noqa: E402
from app.models.requirement import Requirement, RequirementCategory  # noqa: E402
from app.services.requirement_grouping import group_requirements_for_display  # noqa: E402


# ---------------------------------------------------------------------------
# contract_classifier (问题二: 合同文本隔离)
# ---------------------------------------------------------------------------


def test_contract_title_strong_signal():
    assert classify_section_phase("第八章 合同条款", "...").is_contract is True
    assert classify_section_phase("合同协议书格式", "").is_contract is True
    assert classify_section_phase("拟签订的合同", "").is_contract is True


def test_non_contract_section():
    assert classify_section_phase("评标办法", "技术标评分细则").is_contract is False
    assert classify_section_phase("投标人须知", "投标报价不得超过最高限价").is_contract is False


def test_performance_guard_blocks_false_positive():
    # "类似项目业绩" 含"合同金额" 但属 PERF, 不应判为合同文本
    verdict = classify_section_phase("业绩要求", "近三年类似项目合同金额不少于500万元")
    assert verdict.is_contract is False
    assert verdict.reason == "performance_guard"


def test_contract_body_markers_fallback_when_no_section():
    body = "甲方与乙方签订本合同；违约责任由违约方承担；争议解决向法院起诉；不可抗力"
    assert classify_section_phase(None, body).is_contract is True


def test_single_business_clause_not_contract():
    # 单条含"付款"的正常商务要求(无足够合同正文标记)不应被误隔离
    assert classify_section_phase("商务要求", "付款方式为货到30天内支付").is_contract is False


# ---------------------------------------------------------------------------
# noise_classifier (问题一: 商务噪音降级)
# ---------------------------------------------------------------------------


def test_noise_sme_policy_informational():
    assert classify_noise_relevance("本项目专门面向中小企业采购") == "informational"


def test_noise_collusion_discipline_procedural():
    assert (
        classify_noise_relevance("投标人不得相互串通投标或与招标人串通投标")
        == "procedural"
    )


def test_noise_objection_channel_procedural():
    assert (
        classify_noise_relevance("异议提出渠道:投标人可通过交易平台在线提出异议")
        == "procedural"
    )


def test_noise_site_survey_procedural():
    assert (
        classify_noise_relevance("招标人不组织踏勘现场，投标人无需参加现场踏勘活动")
        == "procedural"
    )


def test_noise_machine_code_ban_procedural():
    assert (
        classify_noise_relevance("禁止不同投标人上传投标文件的电脑机器码均相同")
        == "procedural"
    )


def test_normal_requirement_not_noise():
    assert classify_noise_relevance("投标报价不得超过最高限价") is None
    assert classify_noise_relevance("投标人须具备建筑工程施工总承包一级资质") is None


def test_noise_global_open_bid_process_procedural():
    # 跨类别通用程序: 开标/评标流程
    assert classify_noise_relevance("开标时间为2026年6月10日上午9时") == "procedural"
    assert classify_noise_relevance("评标委员会由7人组成,负责评标办法说明") == "procedural"


def test_noise_project_overview_informational():
    assert classify_noise_relevance("项目概况:本项目位于晋州市,资金来源已落实") == "informational"


def test_noise_format_physical_form_is_category_scoped():
    # FORMAT 物理形态: 仅 category=FORMAT 命中(全局不含"装订")
    assert classify_noise_relevance("投标文件须胶装成册并加硬质封面", category="FORMAT") == "procedural"
    # 不传 category 时不应误折叠(避免其它类别偶含"装订"被误伤)
    assert classify_noise_relevance("投标文件须胶装成册并加硬质封面") is None


# ---------------------------------------------------------------------------
# P1.3: 合同模板占位符 + 1527 样例词条
# ---------------------------------------------------------------------------


def test_placeholder_detection_positive():
    assert detect_template_placeholder(
        "每延误1日赔偿金额为合同价格的[具体比例]%或人民币[具体金额]"
    ) is True
    assert detect_template_placeholder("赔偿比例为[比例],最高[金额]") is True
    assert detect_template_placeholder("承包人应在____日内提交") is True


def test_placeholder_detection_negative():
    # 正常带方括号引用/普通数值要求不误命中
    assert detect_template_placeholder("投标报价不得超过最高限价500万元") is False
    assert detect_template_placeholder("详见招标文件[第三章]技术规范") is False
    assert detect_template_placeholder(None) is False


def test_noise_placeholder_contract_template_informational():
    # 1527 样例: 未填占位符的合同模板工期条款 -> informational
    desc = (
        "工期要求：因承包人原因导致工期延误的误期赔偿：每延误1日赔偿金额为"
        "合同价格的[具体比例]%或人民币[具体金额]"
    )
    assert classify_noise_relevance(desc) == "informational"


def test_noise_1527_qualification_post_review_procedural():
    # 1527 样例: 资格后审程序(评委动作+外部信息库) -> procedural
    assert (
        classify_noise_relevance("资格审查方式为资格后审,由评标委员会进行审核")
        == "procedural"
    )
    assert (
        classify_noise_relevance("资料从内蒙古自治区公共资源交易主体信息库中调取")
        == "procedural"
    )


def test_noise_1527_site_survey_procedural():
    # 1527 样例: 现场查勘风险告知 -> procedural
    assert (
        classify_noise_relevance("承包人现场查勘要求:提交投标文件视为已对施工现场及周围环境进行了踏勘")
        == "procedural"
    )


def test_noise_1527_subcontract_notice_procedural():
    # 1527 样例: 分包履约期报批告知 -> procedural
    assert (
        classify_noise_relevance("分包管理要求:分包前须提前请示建设单位同意")
        == "procedural"
    )


def test_noise_reviewable_duration_not_folded():
    # 反例护栏: "工期235日历天"是可评审承诺项, 规则地板不得误折叠
    assert (
        classify_noise_relevance("工期要求:235日历天,计划开工日期2024年05月10日")
        is None
    )


# ---------------------------------------------------------------------------
# relevance_resolver (入库前最终判定, 仅降级)
# ---------------------------------------------------------------------------


def test_resolver_contract_section_forces_informational():
    assert (
        resolve_review_relevance("actionable", "任意描述", is_contract_section=True)
        == "informational"
    )


def test_resolver_contract_body_downgrade():
    body = "违约责任由违约方承担；争议解决向法院起诉；不可抗力；合同生效条件"
    assert resolve_review_relevance("actionable", body) == "informational"


def test_resolver_noise_downgrade():
    assert (
        resolve_review_relevance("actionable", "本项目专门面向中小企业采购")
        == "informational"
    )
    assert (
        resolve_review_relevance("actionable", "投标人不得串通投标、围标、陪标")
        == "procedural"
    )


def test_resolver_normal_stays_actionable():
    assert resolve_review_relevance("actionable", "投标报价不得超过最高限价") == "actionable"


def test_resolver_never_upgrades():
    assert (
        resolve_review_relevance("informational", "投标报价不得超过最高限价")
        == "informational"
    )
    assert resolve_review_relevance("procedural", "技术参数满足XX标准") == "procedural"


def test_resolver_mandatory_is_exempt_from_downgrade():
    # 否决项即使含噪音词/合同区段也强制 actionable, 绝不折叠隐藏
    assert (
        resolve_review_relevance(
            "actionable", "投标人不得串通投标", is_mandatory=True
        )
        == "actionable"
    )
    assert (
        resolve_review_relevance(
            "actionable", "拟签合同条款...违约责任...", is_mandatory=True,
            is_contract_section=True,
        )
        == "actionable"
    )
    # 即使 LLM 误标 procedural, 否决项也拉回 actionable
    assert (
        resolve_review_relevance("procedural", "装订成册", is_mandatory=True, category="FORMAT")
        == "actionable"
    )


def test_resolver_format_downgrade_by_category_when_not_mandatory():
    # 非否决的 FORMAT 物理形态 -> procedural
    assert (
        resolve_review_relevance(
            "actionable", "投标文件须胶装并加硬质封面", category="FORMAT"
        )
        == "procedural"
    )
    # 未传 category 时全局规则不含"装订", 保持 actionable
    assert (
        resolve_review_relevance("actionable", "投标文件须胶装并加硬质封面")
        == "actionable"
    )


# ---------------------------------------------------------------------------
# requirement_grouping review_relevance 透传(组内最可见级别)
# ---------------------------------------------------------------------------


def _row(rid: int, desc: str, relevance: str) -> Requirement:
    row = Requirement(
        project_id=1,
        document_id=1,
        category=RequirementCategory.COMM,
        description=desc,
        is_mandatory=0,
        source_page=1,
        source_pages=json.dumps([1]),
        keywords=json.dumps([]),
        sub_requirements=json.dumps([]),
        confirmed=0,
        review_relevance=relevance,
    )
    row.id = rid
    row.review_relevance = relevance
    return row


def test_grouping_keeps_most_visible_relevance():
    # 同描述两子面: 一 actionable 一 informational -> 组取 actionable(最可见)
    mixed = group_requirements_for_display(
        [_row(1, "同一条款", "informational"), _row(2, "同一条款", "actionable")]
    )
    assert len(mixed) == 1
    assert mixed[0]["review_relevance"] == "actionable"


def test_grouping_all_folded_stays_folded():
    folded = group_requirements_for_display(
        [_row(3, "纪律告知条款", "procedural"), _row(4, "纪律告知条款", "procedural")]
    )
    assert len(folded) == 1
    assert folded[0]["review_relevance"] == "procedural"
