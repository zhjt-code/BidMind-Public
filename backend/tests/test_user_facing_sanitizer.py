"""A1/A2 防御测试 — i18n.internal_to_display.sanitize_user_facing_text 行为契约。

覆盖：
- 风险等级 / 决策态 / 证据级 / 全局发现类型 等英文枚举值替换
- 调试参数 (pair_id / doc_id) 与内部 pair 编号 (597:598) 清除
- 边界匹配（不误伤 'lower' 中的 'low'）
- 幂等（已是中文文本调用 N 次结果不变）

对应 教训：
"进度文案属于展示协议的一部分，必须在统一入口做用户态清洗"。
"""

from __future__ import annotations

import pytest

from app.core.i18n.internal_to_display import (
    DISPLAY_TOKEN_MAP,
    sanitize_user_facing_list,
    sanitize_user_facing_text,
    translate_token,
)


class TestTranslateToken:
    def test_known_token(self):
        assert translate_token("inconclusive") == "无法判定"
        assert translate_token("unknown") == "未知"
        assert translate_token("first") == "一级证据"

    def test_uppercase_normalized(self):
        assert translate_token("INCONCLUSIVE") == "无法判定"

    def test_unknown_returns_self(self):
        assert translate_token("totally_unknown_token") == "totally_unknown_token"

    def test_none_or_empty(self):
        assert translate_token(None) == ""
        assert translate_token("") == ""


class TestSanitizeUserFacingTextEnum:
    def test_replaces_inconclusive(self):
        text = "项目级综合结论为inconclusive，建议人工复核。"
        out = sanitize_user_facing_text(text)
        assert "inconclusive" not in out
        assert "无法判定" in out

    def test_replaces_industry_unknown(self):
        text = "行业画像置信度为unknown，无法提供行业先验支持。"
        out = sanitize_user_facing_text(text)
        assert "unknown" not in out
        assert "未知" in out

    def test_replaces_evidence_levels(self):
        text = "命中 first 与 second 级证据，但缺少 third 级支持。"
        out = sanitize_user_facing_text(text)
        assert "一级证据" in out
        assert "二级证据" in out
        assert "三级证据" in out
        # 关键：不能保留任何小写英文 token（边界匹配，避免误伤复合词）
        assert " first " not in out
        assert " second " not in out

    def test_replaces_decision_state(self):
        text = "decisionState=clear_no_risk，文件对均无嫌疑。"
        out = sanitize_user_facing_text(text)
        assert "clear_no_risk" not in out
        assert "未发现串标嫌疑" in out

    def test_replaces_global_finding_types(self):
        text = "本次分析触发 personnel_overlap 与 metadata_cluster 信号。"
        out = sanitize_user_facing_text(text)
        assert "人员重叠" in out
        assert "元数据聚类" in out

    def test_replaces_industry_profile_status_enums(self):
        """: 行业画像 status 四态英文枚举必须被替换为中文。

 即使后端 i18n 闸门遗漏，前端兜底替换也要兜住这些枚举值。
 """
        text = "industry_profile_status=llm_failed，本次行业画像未能形成。"
        out = sanitize_user_facing_text(text)
        assert "llm_failed" not in out
        assert "AI 调用失败" in out

        text2 = "信号判定结果: insufficient_signals。"
        out2 = sanitize_user_facing_text(text2)
        assert "insufficient_signals" not in out2
        assert "材料信号不足" in out2

        text3 = "AI 推断结果 llm_returned_unknown，需结合证据原文。"
        out3 = sanitize_user_facing_text(text3)
        assert "llm_returned_unknown" not in out3
        assert "AI 无法归纳" in out3


class TestSanitizeUserFacingTextDebugParams:
    def test_strips_pair_id(self):
        text = "对比对 pair_id=597:598 完成判定，未发现异常。"
        out = sanitize_user_facing_text(text)
        assert "pair_id=" not in out
        assert "597:598" not in out

    def test_strips_doc_id_and_project_id(self):
        text = "doc_a_id=12, doc_b_id=34, project_id=5 已完成。"
        out = sanitize_user_facing_text(text)
        assert "doc_a_id" not in out
        assert "doc_b_id" not in out
        assert "project_id" not in out


class TestSanitizeUserFacingTextLlmPollution:
    """: review comment / evidence_anchor 的 LLM 自由文本英文污染清洗。

 review prompt 含 "Golden Rule1-6" / "score=100" 指令，LLM 会把这些内部术语
 写进 comment；evidence_anchor 由代码生成为 "pages:X-Y" 前缀。三者均非枚举词，
 token 表不覆盖，需独立模式清洗（图示用户实例：Golden Rule6 / score=30 / pages:5-10-11-16-82）。
 """

    def test_removes_golden_rule_reference(self):
        text = "根据Golden Rule6相关性审查，响应内容相关性不足。"
        out = sanitize_user_facing_text(text)
        assert "Golden Rule" not in out
        assert "Rule" not in out
        assert "相关性审查" in out

    def test_removes_golden_rule_with_space(self):
        out = sanitize_user_facing_text("违反 Golden Rule 3 的否决项判定")
        assert "Golden Rule" not in out
        assert "Rule" not in out

    def test_score_assign_to_chinese(self):
        text = "故判定为不符合，score=30。"
        out = sanitize_user_facing_text(text)
        assert "score" not in out.lower()
        assert "评分30" in out

    def test_score_colon_variant(self):
        assert "评分85" in sanitize_user_facing_text("score: 85")

    def test_relevance_score_assign_to_chinese(self):
        text = "该要求与投标响应领域不相关，relevance_score=10。"
        out = sanitize_user_facing_text(text)
        assert "relevance_score" not in out
        assert "相关性评分10" in out

    def test_relevance_score_colon_variant(self):
        out = sanitize_user_facing_text("relevance_score：25，需人工复核")
        assert "relevance_score" not in out
        assert "相关性评分25" in out

    def test_pages_anchor_to_chinese(self):
        out = sanitize_user_facing_text("pages:5-10-11-16-82")
        assert "pages" not in out.lower()
        assert out == "第5、10、11、16、82页"

    def test_pages_anchor_single(self):
        assert sanitize_user_facing_text("pages:7") == "第7页"

    def test_idempotent_on_llm_pollution(self):
        text = "根据Golden Rule6相关性审查，score=30，pages:5-10"
        once = sanitize_user_facing_text(text)
        twice = sanitize_user_facing_text(once)
        assert once == twice
        assert "Golden Rule" not in once
        assert "score" not in once.lower()
        assert "pages" not in once.lower()

    def test_strips_llm_review_failure_marker(self):
        """: 失败兜底 comment 的机器标记不得出现在用户态文案。"""
        from app.core.i18n.leak_detector import detect_internal_leaks

        raw = "审查失败[LLM_REVIEW_FAILURE]：模型调用超时或输出不合规，请人工复核"
        out = sanitize_user_facing_text(raw)
        assert "LLM_REVIEW_FAILURE" not in out
        assert "审查失败" in out
        assert "请人工复核" in out
        assert detect_internal_leaks(out) == []
        twice = sanitize_user_facing_text(out)
        assert twice == out


class TestSanitizeUserFacingTextLedgerOverlay:
    """: 账本闸门/prompt 诊断串不得出现在用户态文案。"""

    _ID_OVERLAY = (
        "投标文件第6页提供了身份证。"
        "项目证据账本已登记「法定代表人身份证复印件」（第44页，来自要求"
        "#43177、#43175、#43171），本条未发现结论与账本冲突，待复核"
    )
    _LICENSE_OVERLAY = (
        "经全面检索未发现开户许可证。"
        "项目证据账本已登记「营业执照」（第608页，来自要求#88、#99），"
        "本条未发现结论与账本冲突，待复核"
    )

    def test_strips_id_copy_overlay_keeps_narrative(self):
        out = sanitize_user_facing_text(self._ID_OVERLAY)
        assert "第6页" in out
        assert "身份证" in out
        assert "项目证据账本" not in out
        assert "来自要求#" not in out
        assert "#43177" not in out
        assert "账本冲突" not in out

    def test_strips_license_overlay_second_entity(self):
        out = sanitize_user_facing_text(self._LICENSE_OVERLAY)
        assert "开户许可证" in out
        assert "项目证据账本" not in out
        assert "#88" not in out
        assert "来自要求#" not in out

    def test_idempotent_on_ledger_overlay(self):
        once = sanitize_user_facing_text(self._ID_OVERLAY)
        assert sanitize_user_facing_text(once) == once

    def test_strips_three_atom_overlay_with_repeated_pks(self):
        """用户截图形态: 三条长分面键 + 同一组要求主键重复三遍。"""
        raw = (
            "投标文件第6页提供了身份证，未发现开户许可证。"
            "项目证据账本已登记「法定代表人身份证复印件」（第44页，来自要求"
            "#43177、#43175、#43171、#43186、#43169、#43164、#43183）、"
            "「提供法定代表人身份证明、法定代表人授权委托书及开户许可证复印法定代表人身份证明」"
            "（第44页，来自要求#43177、#43175、#43171、#43186、#43169、#43164、#43183）、"
            "「提供法定代表人身份证明、法定代表人授权委托书及开户许可证复印法定代表人授权委托书」"
            "（第44页，来自要求#43177、#43175、#43171、#43186、#43169、#43164、#43183），"
            "本条未发现结论与账本冲突，待复核"
        )
        out = sanitize_user_facing_text(raw)
        assert "第6页" in out
        assert "开户许可证" in out
        assert "项目证据账本" not in out
        assert "#43177" not in out
        assert "账本冲突" not in out


class TestSanitizeUserFacingTextBoundary:
    def test_does_not_break_chinese_words(self):
        # "三级" 中的 "third" 不应被替换（中文边界完好）
        text = "三级证据置信度偏低"
        out = sanitize_user_facing_text(text)
        assert out == text  # 已是中文，幂等不变

    def test_does_not_match_substring(self):
        # "lower" 不能因为含 "low" 被误伤为"低风险er"
        # "noneoftheabove" 不能因为含 "none" 被误伤
        text = "The lower bound is noneoftheabove text"
        out = sanitize_user_facing_text(text)
        assert "lower" in out
        assert "noneoftheabove" in out
        # 但独立的 high / low / medium 应被替换
        text2 = "整体风险 high，单点 low"
        out2 = sanitize_user_facing_text(text2)
        assert "高风险" in out2
        assert "低风险" in out2

    def test_idempotent(self):
        text = "项目级综合结论为inconclusive，行业画像置信度为unknown。"
        once = sanitize_user_facing_text(text)
        twice = sanitize_user_facing_text(once)
        thrice = sanitize_user_facing_text(twice)
        assert once == twice == thrice
        assert "inconclusive" not in once
        assert "unknown" not in once


class TestSanitizeUserFacingTextEdge:
    def test_empty_input(self):
        assert sanitize_user_facing_text(None) == ""
        assert sanitize_user_facing_text("") == ""
        assert sanitize_user_facing_text("   ") == ""

    def test_non_string_coerced(self):
        assert sanitize_user_facing_text(123) == "123"

    def test_replaces_writer_schema_field_names(self):
        """: 写标一致性 detail 偶发拼进 source_clause_ids，闸门必须中文化。"""
        text = (
            "约束ID 18279 尚未出现在任何当前章节草稿的 source_clause_ids 中，"
            "请在绑定或章节内容中确认是否需要补充响应。"
        )
        out = sanitize_user_facing_text(text)
        assert "source_clause_ids" not in out
        assert "来源约束" in out
        assert "source_material_ids" not in sanitize_user_facing_text(
            "请检查 source_material_ids 是否为空"
        )


class TestSanitizeUserFacingList:
    def test_filters_empty(self):
        items = ["", None, "high 风险", "  "]
        out = sanitize_user_facing_list(items)
        assert out == ["高风险 风险"]

    def test_none_input(self):
        assert sanitize_user_facing_list(None) == []
        assert sanitize_user_facing_list([]) == []


class TestDisplayTokenMapCoverage:
    """确保关键业务枚举都登记在映射表中（防止遗漏）。"""

    @pytest.mark.parametrize(
        "token",
        [
            "high", "medium", "low", "none", "inconclusive",
            "first", "second", "third", "unknown",
            "personnel_overlap", "company_relation", "pricing_pattern", "metadata_cluster",
            "clear_no_risk", "suspicious_found", "partial_review_needed", "analysis_failed",
            "source_clause_ids", "source_material_ids",
        ],
    )
    def test_critical_tokens_registered(self, token: str):
        assert token in DISPLAY_TOKEN_MAP, f"token '{token}' 未登记到 DISPLAY_TOKEN_MAP"
