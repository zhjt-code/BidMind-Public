"""高风险原子条件召回：正文列举 / ★散句 / 表形文本同形态夹具。"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.scoring

import os
import sys
from pathlib import Path

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://user:pass@localhost:5432/testdb",
)
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.mandatory.contracts import (
    AtomicCondition,
    ExistingRequirementView,
    PageText,
)
from app.core.extractor.mandatory.coverage import (
    _format_auto_handled_warning,
    decide_mandatory_coverage,
    text_covers,
)
from app.core.extractor.mandatory.requirement_voice import (
    covers_same_condition,
    is_failure_circumstance_voice,
    is_prohibition_voice,
)
from app.core.extractor.mandatory.signals import (
    atomic_conditions_must_stay_apart,
    count_enumerable_anchors,
    extend_windows_with_scattered_signals,
    harvest_mandatory_signals,
    locate_high_stakes_windows,
    looks_like_atomic_disqualifier,
    pages_from_chunks,
)
from app.core.extractor.tender_extractor_parts.secondary_merge_stage import (
    apply_merge_decisions,
)
from app.schemas.requirement import ExtractedRequirement


def _page(num: int, text: str, section: str = "") -> PageText:
    return PageText(page_num=num, text=text, section_path=section)


def _req(description: str, *, mandatory: bool = False) -> ExtractedRequirement:
    return ExtractedRequirement(
        category="QUAL",
        description=description,
        is_mandatory=mandatory,
        keywords=["投标"],
        source_pages=[3],
    )


def test_locate_numbered_invalid_bid_prose_not_just_tables():
    pages = [
        _page(
            8,
            "第三章 投标无效\n有下列情形之一的，投标无效：\n"
            "（1）未提交营业执照副本；\n"
            "（2）投标报价超过最高限价；\n"
            "（3）未按要求提交投标保证金。",
            section="第三章 投标无效",
        )
    ]
    windows = locate_high_stakes_windows(pages)
    signals = harvest_mandatory_signals(pages)
    assert windows and 8 in windows[0].pages
    numbered = [item for item in signals if item.kind == "numbered_condition"]
    assert len(numbered) == 3


def test_star_clause_in_technical_prose_is_harvested_outside_veto_chapter():
    pages = [
        _page(12, "评标办法\n综合评分法，技术分60分，商务分40分。", section="评标办法"),
        _page(
            40,
            "技术方案应包含实施计划。\n"
            "★项目负责人须具备高级工程师职称，未提供职称证书的按废标处理。",
            section="第五章 技术规格",
        ),
    ]
    windows = locate_high_stakes_windows(pages)
    assert all(12 not in window.pages for window in windows)
    signals = harvest_mandatory_signals(pages)
    stars = [item for item in signals if item.kind == "star"]
    assert stars and "高级工程师" in stars[0].snippet
    assert looks_like_atomic_disqualifier(stars[0].snippet)
    extended = extend_windows_with_scattered_signals(windows, signals)
    assert any(40 in window.pages for window in extended)


def test_qualification_markdown_table_is_scope_not_a_dedicated_table_parser():
    pages = [
        _page(
            5,
            "资格审查\n"
            "| 序号 | 审查因素 | 审查内容 |\n"
            "| 1 | 营业执照 | 投标人须提供有效营业执照 |\n"
            "| 2 | 资质证书 | 须具备建筑工程施工总承包一级资质 |",
            section="资格审查",
        )
    ]
    windows = locate_high_stakes_windows(pages)
    assert windows and windows[0].pages == (5,)
    numbered = [
        item
        for item in harvest_mandatory_signals(pages)
        if item.kind == "numbered_condition"
    ]
    assert len(numbered) == 2
    assert count_enumerable_anchors(pages, windows[0]) == 2


def test_scoring_table_ordinals_are_not_high_stakes_signals():
    pages = [
        _page(
            20,
            "评标办法\n"
            "| 序号 | 评分因素 | 分值 |\n"
            "| 1 | 技术方案 | 40 |\n"
            "| 2 | 类似业绩 | 20 |",
            section="评标办法",
        )
    ]
    assert locate_high_stakes_windows(pages) == ()
    assert not any(
        item.kind == "numbered_condition" for item in harvest_mandatory_signals(pages)
    )


def test_coverage_promotes_unflagged_atomic_and_inserts_missing_star_clause():
    existing = [
        ExistingRequirementView(
            index=0,
            requirement_id=11,
            description="投标报价超过最高限价的，投标无效",
            is_mandatory=False,
            requirement_kind="normal",
            source_pages=(8,),
        )
    ]
    pages = [
        _page(
            8,
            "有下列情形之一的，投标无效：\n（1）投标报价超过最高限价；",
            section="投标无效",
        ),
        _page(
            40,
            "★项目负责人须具备高级工程师职称，未提供职称证书的按废标处理。",
            section="技术规格",
        ),
    ]
    windows = locate_high_stakes_windows(pages)
    signals = harvest_mandatory_signals(pages)
    atomics = [
        AtomicCondition(
            description="项目负责人须具备高级工程师职称，未提供职称证书的按废标处理",
            category="STAFF",
            source_pages=(40,),
            keywords=("职称",),
        )
    ]
    decision = decide_mandatory_coverage(
        pages=pages,
        windows=windows,
        signals=signals,
        existing=existing,
        extracted_atomics=atomics,
    )
    assert 0 in decision.promote_indices
    assert decision.inserts and "高级工程师" in decision.inserts[0].description
    assert any("已标为否决项" in item for item in decision.warnings)


def test_auto_handled_warning_omits_zero_counts():
    assert (
        _format_auto_handled_warning(13, 13)
        == "系统已自动处理：13 条已有要求已标为否决项，另新增 13 条独立否决条件。"
    )
    assert _format_auto_handled_warning(13, 0) == "系统已自动处理：13 条已有要求已标为否决项。"
    assert _format_auto_handled_warning(0, 13) == "系统已自动处理：新增 13 条独立否决条件。"
    assert _format_auto_handled_warning(0, 0) == ""


def test_unclaimed_table_ordinal_is_fail_visible():
    pages = [
        _page(
            5,
            "资格审查\n"
            "| 序号 | 审查因素 | 审查内容 |\n"
            "| 1 | 营业执照 | 投标人须提供有效营业执照 |\n"
            "| 2 | 资质证书 | 须具备建筑工程施工总承包一级资质 |",
            section="资格审查",
        )
    ]
    decision = decide_mandatory_coverage(
        pages=pages,
        windows=locate_high_stakes_windows(pages),
        signals=harvest_mandatory_signals(pages),
        existing=[],
        extracted_atomics=[],
    )
    assert len(decision.unclaimed) >= 2
    assert any("还没有提取成单独的一条招标要求" in item for item in decision.warnings)


def test_unclaimed_numbered_condition_is_fail_visible():
    pages = [
        _page(
            3,
            "废标条款\n有下列情形之一的，作废标处理：\n"
            "（1）未提供安全生产许可证；\n"
            "（2）未提供类似业绩合同。",
        )
    ]
    existing = [
        ExistingRequirementView(
            index=0,
            requirement_id=1,
            description="投标人应具有独立法人资格",
            is_mandatory=False,
            requirement_kind="normal",
            source_pages=(1,),
        )
    ]
    decision = decide_mandatory_coverage(
        pages=pages,
        windows=locate_high_stakes_windows(pages),
        signals=harvest_mandatory_signals(pages),
        existing=existing,
        extracted_atomics=[],
    )
    assert decision.unclaimed
    assert any("还没有提取成单独的一条招标要求" in item for item in decision.warnings)


def test_merge_decisions_refuse_to_collapse_conditions_with_different_numbers():
    """: 禁并谓词只守数字指纹这条不可伪造轴。"""
    items = [
        _req("近三年类似业绩不少于2项，否则作废标处理", mandatory=True),
        _req("近三年类似业绩不少于3项，否则作废标处理", mandatory=True),
    ]
    merged = apply_merge_decisions(
        object(),
        items,
        [{"merge_ids": [0, 1], "description": "综合否决", "is_mandatory": True}],
    )
    assert len(merged) == 2
    assert atomic_conditions_must_stay_apart(items[0].description, items[1].description)


def test_same_condition_paraphrases_with_consequence_phrase_may_merge():
    """ 回归夹具(3560 #42770/#42771): 同一条件的两句改写不得被硬闸禁并。"""
    left = "当投标报价明显低于其他投标报价时，须按要求作出书面说明；不能合理说明的作废标处理。"
    right = "投标报价若被判定可能低于个别成本，须作出书面说明并提供证明材料；不能合理说明的，投标将作废标处理。"
    assert not atomic_conditions_must_stay_apart(left, right)
    # 同形态第二例(联合体): 文本不同、无数字、同一条件
    assert not atomic_conditions_must_stay_apart(
        "本项目不接受联合体投标，否则投标无效",
        "本次招标研究任务不接受联合体投标，按无效投标处理",
    )
    # 一方无数字、另一方有数字: 仍交给合并决策, 不硬禁
    assert not atomic_conditions_must_stay_apart(
        "投标总报价不得超过最高限价，否则否决投标",
        "投标总报价不得超过最高限价271000元，否则否决投标",
    )


def test_image_evidence_chunks_are_skipped_but_table_rows_count():
    class _Chunk:
        def __init__(self, page_num, content, chunk_type, section_path=""):
            self.page_num = page_num
            self.content = content
            self.chunk_type = chunk_type
            self.section_path = section_path

    chunks = [
        _Chunk(2, "资格审查\n投标人须具备安全生产许可证，否则投标无效", "table_row_with_header", "资格审查"),
        _Chunk(2, "扫描件身份证", "image_evidence"),
    ]
    pages = pages_from_chunks(chunks)
    assert len(pages) == 1
    assert "身份证" not in pages[0].text
    assert locate_high_stakes_windows(pages)


def test_text_covers_star_snippet_inside_extracted_description():
    assert text_covers(
        "项目负责人须具备高级工程师职称，未提供职称证书的按废标处理",
        "★项目负责人须具备高级工程师职称，未提供职称证书的按废标处理。",
    )


def test_site_original_and_license_circumstance_cover_obligation():
    assert not text_covers(
        "开标前需将回函原件带到现场",
        "开标前未将投标意向确认书回函原件带到现场",
    )
    assert covers_same_condition(
        "开标前需将回函原件带到现场",
        "开标前未将投标意向确认书回函原件带到现场",
    )
    assert covers_same_condition(
        "投标人须提供有效营业执照",
        "未提交营业执照副本的，投标无效",
    )
    assert is_failure_circumstance_voice("开标前未将投标意向确认书回函原件带到现场")
    assert not is_failure_circumstance_voice("不得在技术标中出现投标报价")
    assert is_prohibition_voice("不得在技术标中出现投标报价")


def test_coverage_does_not_insert_circumstance_when_obligation_exists():
    existing = [
        ExistingRequirementView(
            index=0,
            requirement_id=21,
            description="开标前需将回函原件带到现场",
            is_mandatory=False,
            requirement_kind="normal",
            source_pages=(4,),
        )
    ]
    atomics = [
        AtomicCondition(
            description="开标前未将投标意向确认书回函原件带到现场",
            category="FORMAT",
            source_pages=(4,),
            keywords=("回函原件",),
        )
    ]
    decision = decide_mandatory_coverage(
        pages=[],
        windows=(),
        signals=(),
        existing=existing,
        extracted_atomics=atomics,
    )
    assert decision.inserts == []
    assert 0 in decision.promote_indices


def test_coverage_does_not_insert_license_circumstance_when_obligation_exists():
    existing = [
        ExistingRequirementView(
            index=0,
            requirement_id=22,
            description="投标人须提供有效营业执照",
            is_mandatory=True,
            requirement_kind="normal",
            source_pages=(5,),
        )
    ]
    atomics = [
        AtomicCondition(
            description="未提交营业执照副本的，投标无效",
            category="QUAL",
            source_pages=(8,),
            keywords=("营业执照",),
        )
    ]
    decision = decide_mandatory_coverage(
        pages=[],
        windows=(),
        signals=(),
        existing=existing,
        extracted_atomics=atomics,
    )
    assert decision.inserts == []
    assert decision.promote_indices == []


def test_coverage_inserts_prohibition_not_collapsed_into_must_have():
    existing = [
        ExistingRequirementView(
            index=0,
            requirement_id=23,
            description="投标文件须提供技术方案",
            is_mandatory=True,
            requirement_kind="normal",
            source_pages=(12,),
        )
    ]
    atomics = [
        AtomicCondition(
            description="不得在技术标中出现投标报价",
            category="COMM",
            source_pages=(15,),
            keywords=("报价",),
        )
    ]
    decision = decide_mandatory_coverage(
        pages=[],
        windows=(),
        signals=(),
        existing=existing,
        extracted_atomics=atomics,
    )
    assert len(decision.inserts) == 1
    assert decision.inserts[0].description == "不得在技术标中出现投标报价"


def test_coverage_inserts_circumstance_verbatim_when_no_obligation_exists():
    atomics = [
        AtomicCondition(
            description="开标前未将回函原件带到现场",
            category="FORMAT",
            source_pages=(4,),
            keywords=("回函原件",),
        )
    ]
    decision = decide_mandatory_coverage(
        pages=[],
        windows=(),
        signals=(),
        existing=[],
        extracted_atomics=atomics,
    )
    assert len(decision.inserts) == 1
    assert decision.inserts[0].description == "开标前未将回函原件带到现场"


def test_extract_prompts_forbid_writing_circumstance_as_description():
    from app.core.ai.prompts.mandatory_atomic import MANDATORY_ATOMIC_SYSTEM
    from app.core.ai.prompts.tender_extract import TENDER_EXTRACT_SYSTEM

    assert "禁止把「未…" in TENDER_EXTRACT_SYSTEM or "禁止把「未提交" in TENDER_EXTRACT_SYSTEM
    assert "禁止新增原文没有的否定词" in TENDER_EXTRACT_SYSTEM
    assert "禁止新增原文没有的否定词" in MANDATORY_ATOMIC_SYSTEM
    assert "保留为独立否决条件" not in TENDER_EXTRACT_SYSTEM


def test_extract_atomic_conditions_emits_per_window_progress():
    """页窗抽取必须按窗上报独立 stage，不能 silently 占用 merging。: current 是已完成窗数。LLM 进行中不得把正在跑的窗口报到分母。
 """
    import asyncio

    from app.core.extractor.mandatory.atomic_extract import extract_atomic_conditions
    from app.core.extractor.mandatory.contracts import ScopeWindow

    seen: list[tuple] = []
    inflight: list[list[tuple]] = []

    class _FakeClient:
        async def chat_json(self, **_kwargs):
            inflight.append(list(seen))
            return [{
                "description": "须具备安全生产许可证",
                "category": "QUAL",
                "source_pages": [3],
                "keywords": ["许可"],
            }]

    def _cb(stage, current, total, message, extra=None):
        seen.append((stage, current, total, message))

    pages = [PageText(page_num=3, text="须具备安全生产许可证，否则投标无效")]
    windows = (
        ScopeWindow(pages=(3,), reason="heading"),
        ScopeWindow(pages=(3,), reason="scattered_signal"),
    )
    asyncio.run(
        extract_atomic_conditions(
            _FakeClient(),
            pages=pages,
            windows=windows,
            progress_callback=_cb,
        )
    )
    currents = [item[1] for item in seen]
    assert currents[0] == 0
    assert currents[-1] == 2
    assert currents == sorted(currents)
    assert all(item[0] == "mandatory_coverage" for item in seen)
    assert all(item[2] == 2 for item in seen)
    for snapshot in inflight:
        assert snapshot[-1][1] < 2


def test_same_page_unrelated_disqualifier_does_not_cover_table_ordinals():
    """同页密条目不得按页重叠把表内序号锚算满。"""
    pages = [
        _page(
            5,
            "资格审查\n"
            "| 序号 | 审查因素 | 审查内容 |\n"
            "| 1 | 营业执照 | 投标人须提供有效营业执照 |\n"
            "| 2 | 资质证书 | 须具备建筑工程施工总承包一级资质 |",
            section="资格审查",
        )
    ]
    existing = [
        ExistingRequirementView(
            index=0,
            requirement_id=31,
            description="投标人应具有独立法人资格，否则投标无效",
            is_mandatory=True,
            requirement_kind="normal",
            source_pages=(5,),
        ),
        ExistingRequirementView(
            index=1,
            requirement_id=32,
            description="投标有效期为90天",
            is_mandatory=False,
            requirement_kind="normal",
            source_pages=(5,),
        ),
    ]
    decision = decide_mandatory_coverage(
        pages=pages,
        windows=locate_high_stakes_windows(pages),
        signals=harvest_mandatory_signals(pages),
        existing=existing,
        extracted_atomics=[],
    )
    assert decision.missing_anchor_count >= 2
    numbered = [
        item
        for item in decision.unclaimed
        if item.kind == "numbered_condition"
    ]
    assert len(numbered) >= 2
    assert any("带序号或★的条件比当前招标要求多" in item for item in decision.warnings)


def test_same_page_unrelated_disqualifier_does_not_cover_numbered_safety_license():
    """同形态第二例：同页超限价否决不得盖住许可/业绩列举锚。"""
    pages = [
        _page(
            3,
            "废标条款\n有下列情形之一的，作废标处理：\n"
            "（1）未提供安全生产许可证；\n"
            "（2）未提供类似业绩合同。",
        )
    ]
    existing = [
        ExistingRequirementView(
            index=0,
            requirement_id=41,
            description="投标报价超过最高限价的，投标无效",
            is_mandatory=True,
            requirement_kind="normal",
            source_pages=(3,),
        )
    ]
    decision = decide_mandatory_coverage(
        pages=pages,
        windows=locate_high_stakes_windows(pages),
        signals=harvest_mandatory_signals(pages),
        existing=existing,
        extracted_atomics=[],
    )
    assert decision.missing_anchor_count == 2


def test_one_obligation_claims_only_one_enumerable_anchor():
    """一条义务只能消耗一条可枚举锚，不得顺带盖住邻行。"""
    pages = [
        _page(
            5,
            "资格审查\n"
            "| 序号 | 审查因素 | 审查内容 |\n"
            "| 1 | 营业执照 | 投标人须提供有效营业执照 |\n"
            "| 2 | 资质证书 | 须具备建筑工程施工总承包一级资质 |",
            section="资格审查",
        )
    ]
    existing = [
        ExistingRequirementView(
            index=0,
            requirement_id=51,
            description="投标人须提供有效营业执照",
            is_mandatory=True,
            requirement_kind="normal",
            source_pages=(5,),
        ),
        ExistingRequirementView(
            index=1,
            requirement_id=52,
            description="投标有效期为90天",
            is_mandatory=False,
            requirement_kind="normal",
            source_pages=(5,),
        ),
    ]
    decision = decide_mandatory_coverage(
        pages=pages,
        windows=locate_high_stakes_windows(pages),
        signals=harvest_mandatory_signals(pages),
        existing=existing,
        extracted_atomics=[],
    )
    assert decision.missing_anchor_count == 1
    assert any("资质" in item.snippet for item in decision.unclaimed)
