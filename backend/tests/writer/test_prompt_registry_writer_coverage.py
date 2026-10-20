"""P0.1 防御测试：data_fill / free_writing 必须经过 Prompt Registry。

历史背景 (姊妹问题):
 clause_classify / chapter_judge / material_tag 已接入 Registry,
 但 data_fill / free_writing 在 prompts/section_compose.py 直接 .format,
 导致 LLM Center 编辑对生产调用完全无效。P0.1 修复后两者应均能:
 1. 命中 Registry 时使用 Registry 模板;
 2. 未命中 Registry 时 fallback 到代码默认 (不崩溃);
 3. Registry 模板占位符错误时 fallback 到代码默认 (safe_format)。

此测试用 monkeypatch 替换 resolve_system_prompt / resolve_user_template,
不依赖真实 DB。
"""

from __future__ import annotations

import pytest

from app.core.writer.prompts import section_compose as sc_mod


@pytest.fixture
def registry_stub(monkeypatch):
    """把 Registry 解析函数替换成可控 stub, 返回一个 dict 让测试写入预期值。"""

    state: dict[str, str] = {}

    def fake_system(node_key: str, code_default: str) -> str:
        return state.get(f"system:{node_key}", code_default)

    def fake_user(node_key: str, code_default: str) -> str:
        return state.get(f"user:{node_key}", code_default)

    monkeypatch.setattr(sc_mod, "resolve_system_prompt", fake_system, raising=False)
    # 上述 attr 在模块里是延迟 import, 所以 patch 要打到实际 import 链
    import app.core.ai.prompt_registry as reg_mod

    monkeypatch.setattr(reg_mod, "resolve_system_prompt", fake_system, raising=True)
    monkeypatch.setattr(reg_mod, "resolve_user_template", fake_user, raising=True)
    return state


class TestDataFillRegistryIntegration:

    def test_registry_hit_system_and_user_used(self, registry_stub):
        registry_stub["system:writing.data_fill"] = "REG_SYS_DF"
        registry_stub["user:writing.data_fill"] = (
            "REG_USER_DF[title={node_title}|words={target_word_count}"
            "|clauses={clauses_text}|materials={materials_text}"
            "|profile={chapter_profile_text}|no={chapter_number}"
            "|hint={sub_numbering_hint}|rules={material_usage_rules}]"
        )

        system, user = sc_mod.get_data_fill_prompt(
            node_title="公司简介", target_word_count=400,
            clauses_text="C1", materials_text="M1",
            chapter_profile_text="P1", chapter_number="1",
            sub_numbering_hint="H", material_usage_rules="R",
        )

        assert system == "REG_SYS_DF"
        assert "REG_USER_DF[title=公司简介" in user
        assert "clauses=C1" in user and "materials=M1" in user

    def test_registry_miss_falls_back_to_code(self, registry_stub):
        system, user = sc_mod.get_data_fill_prompt(
            node_title="x", target_word_count=500,
            clauses_text="y", materials_text="z",
        )
        assert system == sc_mod.DATA_FILL_SYSTEM
        assert "章节信息" in user  # 代码模板的特征字串
        assert "Markdown代码块" in system

    def test_registry_broken_placeholder_safe_fallback(self, registry_stub):
        """Registry 用户误删占位符 → format 抛 KeyError → 回退到代码默认, 不崩溃。"""
        registry_stub["user:writing.data_fill"] = "BROKEN {not_a_real_key}"
        _, user = sc_mod.get_data_fill_prompt(
            node_title="x", target_word_count=500,
            clauses_text="y", materials_text="z",
        )
        # 应当走 code_default, 能成功 format 出代码模板
        assert "章节信息" in user


class TestFreeWritingRegistryIntegration:

    def test_registry_hit_system_and_user_used(self, registry_stub):
        registry_stub["system:writing.free_writing"] = (
            "REG_SYS_FW[industry={industry_profile_excerpt}]"
        )
        registry_stub["user:writing.free_writing"] = (
            "REG_USER_FW[title={node_title}|prev={previous_summaries_text}"
            "|clauses={clauses_text}|materials={materials_text}"
            "|profile={chapter_profile_text}|no={chapter_number}"
            "|hint={sub_numbering_hint}|rules={material_usage_rules}"
            "|words={target_word_count}]"
        )

        system, user = sc_mod.get_free_writing_prompt(
            node_title="技术方案", target_word_count=2000,
            clauses_text="C", materials_text="M",
            previous_summaries_text="PREV",
            industry_profile_excerpt="IND",
            chapter_profile_text="P", chapter_number="2",
            sub_numbering_hint="H", material_usage_rules="R",
        )

        assert system == "REG_SYS_FW[industry=IND]"
        assert "REG_USER_FW[title=技术方案" in user
        assert "prev=PREV" in user and "words=2000" in user

    def test_registry_miss_falls_back_to_code(self, registry_stub):
        system, user = sc_mod.get_free_writing_prompt(
            node_title="t", target_word_count=1500,
            clauses_text="c", materials_text="m",
            previous_summaries_text="",
            industry_profile_excerpt="IND",
        )
        assert "资深投标方案撰写专家" in system  # code default system 特征
        assert "正在编写正式投标文件正文" in system  # 回写自 LLM Center published v7 (db)
        assert "标书正文自然化与去AI味规则" in system
        assert "章节信息" in user
        assert "Markdown代码块" in system

    def test_registry_broken_system_placeholder_safe_fallback(self, registry_stub):
        registry_stub["system:writing.free_writing"] = "SYS_BROKEN {nope}"
        system, _ = sc_mod.get_free_writing_prompt(
            node_title="t", target_word_count=1500,
            clauses_text="c", materials_text="m",
            previous_summaries_text="",
            industry_profile_excerpt="IND",
        )
        # 回退到 code system (含 industry_profile_excerpt 占位并成功 format)
        assert "资深投标方案撰写专家" in system

    def test_registry_broken_user_placeholder_safe_fallback(self, registry_stub):
        registry_stub["user:writing.free_writing"] = "USER_BROKEN {nope}"
        _, user = sc_mod.get_free_writing_prompt(
            node_title="t", target_word_count=1500,
            clauses_text="c", materials_text="m",
            previous_summaries_text="",
            industry_profile_excerpt="IND",
        )
        assert "章节信息" in user
