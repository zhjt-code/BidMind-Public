"""W1 防御测试：free_writing 字数闭环 + token 联动。

职责: 验证 ``compute_section_max_tokens`` 公式、``count_plain_text`` 剥离 HTML/
 占位符、``ensure_section_length`` 偏差触发与降级行为；防止生成后无字数
 校验、长章节被 token 截断的回归。
不做: 真实 LLM 调用（mock chat）、真实 normalize_section_html（已在 writer 单测覆盖）、
 data_fill / fixed_template 路径（W1 仅作用于 free_writing）。

历史教训映射:
 同源教训：长章节烂尾、素材消费不足。
 本测试守护"生成后必校字数、必比对目标"的最小契约。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import settings
from app.core.writer.composer import length_control
from app.core.writer.composer.length_control import (
    compute_section_max_tokens,
    count_plain_text,
    ensure_section_length,
)

# ---------------------------------------------------------------------------
# 1. count_plain_text: HTML / 占位符 / 空白剥离
# ---------------------------------------------------------------------------


def test_count_plain_text_strips_html_tags() -> None:
    assert count_plain_text("<p>我方承诺按时交付</p>") == len("我方承诺按时交付")


def test_count_plain_text_strips_nested_tags() -> None:
    html = "<h4>方案概述</h4><p>本方案<strong>覆盖</strong>全流程。</p>"
    assert count_plain_text(html) == len("方案概述本方案覆盖全流程。")


def test_count_plain_text_strips_placeholders() -> None:
    """[待补充:xxx] 占位符不计入字数（避免占位符把虚高字数掩盖真实不足）。"""
    html = "<p>项目经理为 [待补充:姓名] ，工作年限 [待补充:年限] 年。</p>"
    assert count_plain_text(html) == len("项目经理为，工作年限年。")


def test_count_plain_text_returns_zero_for_empty_or_pure_tags() -> None:
    assert count_plain_text("") == 0
    assert count_plain_text("<p></p><h4></h4>") == 0


# ---------------------------------------------------------------------------
# 2. compute_section_max_tokens 公式与上限
# ---------------------------------------------------------------------------


def test_compute_section_max_tokens_uses_formula(monkeypatch) -> None:
    """正常情况下 max_tokens = max(registry, target*per_char + 800)，被 CAP 截断。"""
    monkeypatch.setattr(settings, "WRITING_SECTION_TOKENS_PER_CHAR", 1.5)
    monkeypatch.setattr(settings, "WRITING_MAX_SECTION_TOKENS_CAP", 8192)
    # target=2000, per_char=1.5 -> 3000+800=3800; registry=4000 -> 取 4000
    assert compute_section_max_tokens(2000, registry_max_tokens=4000) == 4000
    # target=5000 -> 7500+800=8300，被 CAP=8192 截断
    assert compute_section_max_tokens(5000, registry_max_tokens=4000) == 8192


def test_compute_section_max_tokens_falls_back_when_target_missing(monkeypatch) -> None:
    monkeypatch.setattr(settings, "WRITING_MAX_SECTION_TOKENS_CAP", 8192)
    monkeypatch.setattr(settings, "WRITING_MAX_SECTION_TOKENS", 4000)
    assert compute_section_max_tokens(None, registry_max_tokens=4000) == 4000
    assert compute_section_max_tokens(0, registry_max_tokens=4000) == 4000


def test_compute_section_max_tokens_respects_cap_above_registry(monkeypatch) -> None:
    """CAP 永远不应低于 registry_max_tokens（即便误配 CAP<registry）。"""
    monkeypatch.setattr(settings, "WRITING_MAX_SECTION_TOKENS_CAP", 2000)
    monkeypatch.setattr(settings, "WRITING_SECTION_TOKENS_PER_CHAR", 1.5)
    # registry=4000 > cap=2000；公式仍返回 >= registry
    result = compute_section_max_tokens(1000, registry_max_tokens=4000)
    assert result >= 4000


# ---------------------------------------------------------------------------
# 3. resolve_section_length_tolerance: 建议字数动态软阈值
# ---------------------------------------------------------------------------


def test_resolve_section_length_tolerance_allows_short_technical_sections_to_double(monkeypatch) -> None:
    """短中篇技术章节常需展开论证，上限应能放宽到全局 cap。"""
    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MIN_RATIO", 0.6)
    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MAX_RATIO", 2.0)

    assert hasattr(length_control, "resolve_section_length_tolerance")
    assert length_control.resolve_section_length_tolerance(1500) == (0.6, 2.0)


def test_resolve_section_length_tolerance_tightens_long_sections(monkeypatch) -> None:
    """建议字数越大，过长越容易冗余，应逐档收敛上限。"""
    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MIN_RATIO", 0.6)
    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MAX_RATIO", 2.0)

    assert hasattr(length_control, "resolve_section_length_tolerance")
    assert length_control.resolve_section_length_tolerance(2500) == (0.6, 1.8)
    assert length_control.resolve_section_length_tolerance(4000) == (0.6, 1.6)


# ---------------------------------------------------------------------------
# 4. ensure_section_length: 开关 / 容忍区间 / 重生成
# ---------------------------------------------------------------------------


def _make_composer_with_chat(chat_return: str) -> MagicMock:
    composer = MagicMock()
    composer.llm_client = MagicMock()
    composer.llm_client.chat = AsyncMock(return_value=chat_return)
    return composer


@pytest.mark.anyio("asyncio")
async def test_ensure_length_returns_original_when_min_ratio_zero(monkeypatch) -> None:
    """MIN_RATIO=0 时关闭闭环（回滚开关），不调用 LLM。"""
    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MIN_RATIO", 0.0)
    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MAX_RATIO", 1.6)
    composer = _make_composer_with_chat("<p>调整后</p>")
    content = "<p>原文</p>"
    out = await ensure_section_length(composer, 1, "章节", content, 2000)
    assert out == content
    composer.llm_client.chat.assert_not_called()


@pytest.mark.anyio("asyncio")
async def test_ensure_length_returns_original_when_in_tolerance(monkeypatch) -> None:
    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MIN_RATIO", 0.6)
    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MAX_RATIO", 1.6)
    composer = _make_composer_with_chat("<p>调整后</p>")
    # 目标 100，实际 90 -> ratio=0.9 在 [0.6, 1.6] 内
    content = "<p>" + "字" * 90 + "</p>"
    out = await ensure_section_length(composer, 1, "章节", content, 100)
    assert out == content
    composer.llm_client.chat.assert_not_called()


@pytest.mark.anyio("asyncio")
async def test_ensure_length_uses_dynamic_upper_ratio_for_short_targets(monkeypatch) -> None:
    """target=1500 且 ratio<2.0 时应视为可接受篇幅，不应因旧 1.6 上限压缩。"""
    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MIN_RATIO", 0.6)
    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MAX_RATIO", 2.0)
    composer = _make_composer_with_chat("<p>调整后</p>")
    content = "<p>" + "字" * 2925 + "</p>"  # target=1500 -> ratio=1.95

    out = await ensure_section_length(composer, 1, "技术测试方案", content, 1500)

    assert out == content
    composer.llm_client.chat.assert_not_called()


@pytest.mark.anyio("asyncio")
async def test_ensure_length_returns_original_when_target_missing(monkeypatch) -> None:
    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MIN_RATIO", 0.6)
    composer = _make_composer_with_chat("<p>调整后</p>")
    out = await ensure_section_length(composer, 1, "章节", "<p>内容</p>", None)
    assert out == "<p>内容</p>"
    composer.llm_client.chat.assert_not_called()


@pytest.mark.anyio("asyncio")
async def test_ensure_length_triggers_expand_when_too_short(monkeypatch) -> None:
    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MIN_RATIO", 0.6)
    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MAX_RATIO", 1.6)
    # 重生成后字数更接近 target
    long_text = "<p>" + "扩" * 800 + "</p>"
    composer = _make_composer_with_chat(long_text)
    short = "<p>" + "短" * 100 + "</p>"  # target=1000 -> ratio=0.1 → 触发扩写
    out = await ensure_section_length(composer, 1, "章节", short, 1000)
    composer.llm_client.chat.assert_awaited_once()
    # 调整后字数应更接近目标
    assert count_plain_text(out) > count_plain_text(short)


@pytest.mark.anyio("asyncio")
async def test_ensure_length_keeps_original_when_llm_fails(monkeypatch) -> None:
    """LLM 抛异常时保留原稿，不破坏主流程。"""
    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MIN_RATIO", 0.6)
    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MAX_RATIO", 1.6)
    composer = MagicMock()
    composer.llm_client = MagicMock()
    composer.llm_client.chat = AsyncMock(side_effect=RuntimeError("boom"))
    short = "<p>" + "短" * 100 + "</p>"
    out = await ensure_section_length(composer, 1, "章节", short, 1000)
    assert out == short


@pytest.mark.anyio("asyncio")
async def test_ensure_length_keeps_original_when_adjustment_makes_it_worse(monkeypatch) -> None:
    """重生成后偏差更大时必须保留原稿。"""
    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MIN_RATIO", 0.6)
    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MAX_RATIO", 1.6)
    # 原稿 ratio=0.1（偏差 0.9），重生成产出更短的内容 ratio=0.05（偏差 0.95）
    even_shorter = "<p>" + "更" * 50 + "</p>"
    composer = _make_composer_with_chat(even_shorter)
    short = "<p>" + "短" * 100 + "</p>"  # target=1000
    out = await ensure_section_length(composer, 1, "章节", short, 1000)
    assert out == short  # 保留原稿


@pytest.mark.anyio("asyncio")
async def test_ensure_length_keeps_original_when_empty_output(monkeypatch) -> None:
    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MIN_RATIO", 0.6)
    composer = _make_composer_with_chat("   ")
    content = "<p>" + "短" * 100 + "</p>"
    out = await ensure_section_length(composer, 1, "章节", content, 1000)
    assert out == content
