"""：行业画像六段契约闸。

问题形态：结构化长文只设 token 上限、不校验约定节是否齐全。
本案例：行业画像写成「项目背景/市场分析」并在「四、」截断。
同形态第二例：证书隔离要求写成「一、资质 / 3.1 有效期」长文。
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

from app.core.writer.industry_profile_gate import (
    assess_profile_completeness,
    build_completion_user_message,
    fulfill_industry_profile_text,
    merge_profile_texts,
)
from app.core.writer.industry_profile_view import (
    REQUIRED_PROFILE_SECTIONS,
    SECTION_REVIEWER_FOCUS,
)
from app.core.writer.industry_profiler import IndustryProfiler
from app.core.writer.prompts.industry_profile import (
    PROFILE_OUTPUT_CONTRACT_MARKER,
    PROFILE_SECTION_HEADINGS,
    attach_output_contract,
    get_industry_profile_prompt,
)


def _six_section_guide() -> str:
    return "\n\n".join(
        [
            "### 1. 行业识别与典型特征\n物业服务行业，投标文件常见商务与技术分卷。",
            "### 2. 专业术语表（20-30个）\n- 秩序维护\n- 设施设备维保",
            "### 3. 技术方案典型结构\n建议按人员配置、作业流程、应急预案展开。",
            "### 4. 评标专家核心关注点（5-8个）\n- 人员到岗率\n- 响应时效",
            "### 5. 写作风格指导\n用可核验的措施和数据，少用空话。",
            "### 6. 差异化竞争建议\n可突出智慧物业平台，但不得写成招标已要求。",
        ]
    )


def _market_essay() -> str:
    return (
        "一、项目背景和政策依据\n"
        "• 相关政策要求持续加码\n"
        "二、市场背景分析\n"
        "（一）行业现状\n"
        "1. 需求增长\n"
        "3.1 核心产品竞争力分析\n"
        "四、"
    )


def _certificate_isolation_essay() -> str:
    return (
        "一、资质与证书隔离要求\n"
        "• 不得在第一信封出现证书原件\n"
        "二、投标文件组成说明\n"
        "3.1 有效期与发证机关\n"
        "四、"
    )


def test_complete_six_section_guide_passes():
    result = assess_profile_completeness(_six_section_guide())
    assert result.complete is True
    assert result.reason == "ok"
    assert result.present == REQUIRED_PROFILE_SECTIONS


def test_market_essay_fails_unparsed():
    result = assess_profile_completeness(_market_essay())
    assert result.complete is False
    assert result.reason == "unparsed"
    assert result.missing == REQUIRED_PROFILE_SECTIONS


def test_certificate_isolation_essay_same_shape_fails():
    result = assess_profile_completeness(_certificate_isolation_essay())
    assert result.complete is False
    assert result.reason == "unparsed"
    assert result.missing == REQUIRED_PROFILE_SECTIONS


def test_truncated_fourth_heading_is_incomplete_tail():
    text = (
        "### 1. 行业识别与典型特征\n物业服务行业特征清楚。\n\n"
        "### 2. 专业术语表（20-30个）\n- 秩序维护\n\n"
        "### 3. 技术方案典型结构\n按人员与流程组织章节。\n\n"
        "### 4. 评标专家核心关注点（5-8个）\n"
    )
    result = assess_profile_completeness(text)
    assert result.complete is False
    assert SECTION_REVIEWER_FOCUS in result.incomplete_tail
    assert "writing_style" in result.missing
    assert "differentiation" in result.missing


def test_completion_message_asks_rewrite_for_essay():
    assessment = assess_profile_completeness(_market_essay())
    message = build_completion_user_message(_market_essay(), assessment)
    assert "不要续写该长文" in message
    assert PROFILE_SECTION_HEADINGS[0] in message
    assert "项目背景和政策依据" in message


def test_merge_prefers_complete_rewrite():
    merged = merge_profile_texts(_market_essay(), _six_section_guide())
    assert assess_profile_completeness(merged).complete is True
    assert "### 1. 行业识别与典型特征" in merged
    assert "项目背景和政策依据" not in merged


@pytest.mark.asyncio
async def test_fulfill_repairs_essay_once():
    calls: list[str] = []

    async def _complete(user_msg: str) -> str:
        calls.append(user_msg)
        return _six_section_guide()

    text, meta = await fulfill_industry_profile_text(
        _market_essay(),
        sanitize=lambda value: value.strip(),
        complete=_complete,
    )
    assert assess_profile_completeness(text).complete is True
    assert meta["profile_confidence"] == "repaired"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_fulfill_keeps_complete_text_without_second_call():
    calls: list[str] = []

    async def _complete(user_msg: str) -> str:
        calls.append(user_msg)
        raise AssertionError("完整六段不应续写")

    text, meta = await fulfill_industry_profile_text(
        _six_section_guide(),
        sanitize=lambda value: value.strip(),
        complete=_complete,
    )
    assert text.startswith("### 1.")
    assert meta == {}
    assert calls == []


def test_code_prompt_contains_output_contract():
    system, user = get_industry_profile_prompt(
        project_context="园区物业",
        scoring_summary="服务方案 20 分",
        content_requirements="覆盖人员配置",
        material_requirements="业绩证明",
    )
    assert PROFILE_OUTPUT_CONTRACT_MARKER in system
    assert PROFILE_OUTPUT_CONTRACT_MARKER in user
    assert "禁止手写可见编号" in user
    assert "项目背景」「市场分析" in user


def test_attach_contract_only_when_marker_missing():
    system, user = attach_output_contract("SYS", "旧模板没有契约")
    assert PROFILE_OUTPUT_CONTRACT_MARKER in user
    again_sys, again_user = attach_output_contract(system, user)
    assert again_user.count(PROFILE_OUTPUT_CONTRACT_MARKER) == 1
    assert again_sys == system


@pytest.mark.asyncio
async def test_profiler_rewrites_essay_then_saves_six_sections():
    async def _refresh(clause) -> None:
        clause.id = 88

    fake_db = SimpleNamespace(
        add=Mock(),
        commit=AsyncMock(),
        refresh=AsyncMock(side_effect=_refresh),
    )
    fake_llm = SimpleNamespace(
        chat=AsyncMock(side_effect=[_market_essay(), _six_section_guide()]),
    )
    profiler = IndustryProfiler(db=fake_db, llm_client=fake_llm)
    clause = await profiler.generate(
        project_id=3,
        document_id=4,
        clauses=[
            SimpleNamespace(
                sub_type="project_context",
                content="园区物业服务项目",
                score_weight=None,
                metadata_json=None,
            ),
        ],
    )

    assert clause.id == 88
    assert assess_profile_completeness(clause.content).complete is True
    assert clause.metadata_json["profile_confidence"] == "repaired"
    assert fake_llm.chat.await_count == 2
