"""S.3.2 单元测试：PromptRegistry.stability_class 前缀推导与温度强制。"""

from __future__ import annotations

import pytest

from app.core.ai.prompt_registry import (
    PromptRegistry,
    ResolvedPrompt,
    _apply_stability_class_temperature,
    _derive_stability_class,
)


class TestDeriveStabilityClass:
    """按 node_key 前缀推导稳定性等级。"""

    @pytest.mark.parametrize("node_key", [
        "bid_review.tender_extract_page",
        "bid_review.review_item",
        "bid_review.review_no_response",
        "bid_review.image_recognition",
        "bid_review.bid_extract_match",
        "collusion.tender_format_extract",
        "collusion.route_extract",
        "collusion.pair_compare_entity",
        "collusion.pair_compare_text",
        "collusion.pair_compare_business",
        "collusion.single_dim_search",
        "collusion.pair_judgement",
        "collusion.group_analysis",
        "collusion.project_summary",
        "collusion.overall_synthesis",
        "writing.hard_clause",
        "writing.structure_clause",
        "writing.guide_clause",
        "writing.unified_clause",
        "writing.render_config",
        "writing.clause_dedup_decision",
        "writing.clause_classify",
        "writing.relevance_classify",
        "writing.bid_structure_analysis",
        "writing.bid_directory_extract",
        "writing.material_tag",
        "writing.image_classify",
        "writing.chapter_judge",
        # P0.2: chapter_profile 的 expects/rejects 直接被 material_matcher 消费, 属事实分类任务。
        "writing.chapter_profile",
        "writing.cross_ref_check",
        "judge.verdict",
        "cove.question",
    ])
    def test_deterministic_prefixes(self, node_key: str):
        assert _derive_stability_class(node_key) == "deterministic"

    @pytest.mark.parametrize("node_key", [
        "writing.free_writing",
        "writing.section_summary",
        "writing.industry_profile",
        "writing.skeleton_generate",
        "writing.skeleton_enrich",
        "writing.section_fill",
        "writing.outline_generate",
        "writing.data_fill",
    ])
    def test_creative_prefixes(self, node_key: str):
        assert _derive_stability_class(node_key) == "creative"

    @pytest.mark.parametrize("node_key", [
        "unknown.random_node",
        "some.other.thing",
        "",
        "misc",
    ])
    def test_unknown_returns_semi(self, node_key: str):
        assert _derive_stability_class(node_key) == "semi"


class TestApplyStabilityTemperature:
    def test_deterministic_forced_zero(self):
        assert _apply_stability_class_temperature(0.7, "deterministic") == 0.0
        assert _apply_stability_class_temperature(0.1, "deterministic") == 0.0

    def test_semi_keeps_original(self):
        assert _apply_stability_class_temperature(0.3, "semi") == 0.3
        assert _apply_stability_class_temperature(0.1, "semi") == 0.1

    def test_creative_keeps_original(self):
        assert _apply_stability_class_temperature(0.5, "creative") == 0.5


class TestPromptRegistryFallbackAppliesStability:
    """未注册 node 的 fallback 路径也应按 stability_class 覆盖温度。"""

    def test_unregistered_deterministic_gets_zero(self):
        reg = PromptRegistry()
        rp: ResolvedPrompt = reg.get_prompt("bid_review.review_item")
        # 未注册 → fallback；prefix 命中 deterministic → 温度强制 0.0
        assert rp.stability_class == "deterministic"
        assert rp.temperature == 0.0

    def test_unregistered_creative_keeps_default(self):
        reg = PromptRegistry()
        rp: ResolvedPrompt = reg.get_prompt("writing.free_writing")
        assert rp.stability_class == "creative"
        # fallback 基础温度 0.1，creative 不强制
        assert rp.temperature == 0.1

    def test_chapter_profile_is_deterministic(self):
        """P0.2 回归: chapter_profile 从 creative 移至 deterministic, 温度强制 0.0。"""
        reg = PromptRegistry()
        rp: ResolvedPrompt = reg.get_prompt("writing.chapter_profile")
        assert rp.stability_class == "deterministic"
        assert rp.temperature == 0.0

    def test_unregistered_unknown_semi(self):
        reg = PromptRegistry()
        rp: ResolvedPrompt = reg.get_prompt("totally.unknown.node")
        assert rp.stability_class == "semi"
        assert rp.temperature == 0.1
