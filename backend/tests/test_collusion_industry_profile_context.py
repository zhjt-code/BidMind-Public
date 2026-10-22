"""串标行业画像上下文构建防御测试。

覆盖风险:
- 招标文件前置公告/须知过长时，后部行业信号不能被前缀截断丢失。
- 有招标文件时，投标侧结构化信号必须作为补充上下文保留。
- 上下文需带来源分组，避免行业信号 Prompt 要求来源定位但输入无来源。
- 空材料应明确返回不足占位，不诱导模型硬造行业。
"""

from __future__ import annotations

from app.core.collusion.industry_profile_context import (
    IndustryProfileContextInput,
    assess_industry_signal_adequacy,
    build_industry_signal_context,
)


def test_context_keeps_late_tender_industry_signal_after_long_front_matter() -> None:
    front_matter = "投标人须知、公告、评分办法、通用合同条款。" * 500
    late_industry = (
        "本项目为医院信息化系统集成采购，包含HIS、EMR、PACS、LIS接口改造与等保三级安全建设。"
    )

    context = build_industry_signal_context(
        IndustryProfileContextInput(
            tender_text=front_matter + late_industry,
            bid_extractions=[],
        )
    )

    assert "医院信息化系统集成采购" in context
    assert "HIS" in context
    assert "招标文件行业相关片段" in context
    assert "来源=tender" in context


def test_context_keeps_bid_signals_as_supplement_when_tender_exists() -> None:
    context = build_industry_signal_context(
        IndustryProfileContextInput(
            tender_text="本项目为某市政道路工程施工招标，要求市政公用工程施工总承包资质。",
            bid_extractions=[
                {
                    "company_info": {"company_name": "甲建设公司"},
                    "technical_highlights": ["独家专利工法XYZ", "沥青路面摊铺组织方案"],
                    "qualifications": ["市政公用工程施工总承包二级"],
                    "price_info": {"total_price": "1200万元"},
                    "personnel_list": [
                        {"name": '<PERSON_A>', "role": "项目经理"},
                        {"name": '<PERSON_B>', "role": "技术负责人"},
                    ],
                }
            ],
        )
    )

    assert "市政道路工程" in context
    assert "投标侧补充信号" in context
    assert "独家专利工法XYZ" in context
    assert "市政公用工程施工总承包二级" in context
    assert "项目经理" in context
    assert "来源=bid_extraction" in context


def test_context_returns_explicit_insufficient_placeholder_for_empty_materials() -> None:
    context = build_industry_signal_context(
        IndustryProfileContextInput(tender_text=" ", bid_extractions=[])
    )

    assert "未提取到足够行业信号" in context
    assert "不得猜测行业" in context


def test_single_strong_signal_is_sufficient_for_medium_confidence_profile() -> None:
    adequacy = assess_industry_signal_adequacy(
        [
            {
                "signal_type": "domain",
                "value": "医院信息化系统集成采购",
                "source": "tender",
                "source_refs": ["招标文件行业相关片段1"],
                "confidence": 0.9,
            }
        ]
    )

    assert adequacy.is_sufficient is True
    assert adequacy.confidence == "medium"
    assert adequacy.reason == "single_strong_signal"


def test_empty_signals_are_insufficient() -> None:
    adequacy = assess_industry_signal_adequacy([])

    assert adequacy.is_sufficient is False
    assert adequacy.confidence == "unknown"
    assert adequacy.reason == "no_signals"
