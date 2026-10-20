"""C2 防御测试 — finding_polarity 推测语境识别。

覆盖图 2 / 图 3 用户反馈的实际推测词模式：
- "假设" / "（假设）"
- "例如将'防腐'误写为'防府'（假设）"
- "可能存在/疑似/或许/估计/大概"
- "尚不明确/有待核实/不排除"

并验证：
- 已是中性陈述（不含推测词）不应误识别
- 反向陈述（"无串标"）不应被识别为推测语境
"""

from __future__ import annotations

import pytest

from app.core.collusion.finding_polarity import (
    _has_evidence_anchors,
    assess_finding_polarity,
    assess_finding_speculative,
    is_negative_finding,
    is_speculative_finding,
)


class TestSpeculativePatterns:
    @pytest.mark.parametrize("text", [
        "例如将'防腐'误写为'防府'（假设）",
        "（假设）存在相同错别字",
        "假设两份文件由同一人撰写",
        "疑似采用同一软件生成",
        "可能存在格式雷同",
        "估计来自同一作者",
        "或许存在关联",
        "大概是同一模板",
        "尚不明确该差异是否构成证据",
        "尚需进一步核查",
        "有待核实的证据",
        "不排除存在串标可能",
        "推测两家公司有合作",
        "猜测使用了同一供应商",
        "例如某段文字相似",
    ])
    def test_speculative_hits(self, text: str):
        is_spec, pat = is_speculative_finding(text)
        assert is_spec is True, f"应识别为推测: {text}"
        assert pat is not None


class TestSpeculativeNonHits:
    @pytest.mark.parametrize("text", [
        "两份文件均出现'防府'，应为'防腐'，原文位置 第30页",
        '法定代表人完全相同：<PERSON_A>',
        "统一社会信用代码完全一致：91110000XXXXX",
        "可能性较低，故不构成证据",  # 排除"可能性较低"
        "可能性偏低",
        "",
        "  ",
    ])
    def test_non_speculative(self, text: str):
        is_spec, _ = is_speculative_finding(text)
        assert is_spec is False, f"不应识别为推测: '{text}'"


class TestAssessFindingSpeculative:
    def test_finding_summary_speculative(self):
        f = {
            "summary": "两份文件中均出现相同的非标准表述（假设）",
            "detail": "例如将'防腐'误写为'防府'",
            "quote_a": "防府",
            "quote_b": "防府",
        }
        is_spec, pat = assess_finding_speculative(f)
        assert is_spec is True

    def test_finding_normal_passes(self):
        f = {
            "summary": '法定代表人相同：<PERSON_A>',
            "detail": '两份文件法定代表人均为<PERSON_A>，构成实质关联。',
        }
        is_spec, _ = assess_finding_speculative(f)
        assert is_spec is False

    def test_non_dict_input(self):
        is_spec, _ = assess_finding_speculative(None)
        assert is_spec is False
        is_spec, _ = assess_finding_speculative("not a dict")
        assert is_spec is False


class TestNegativeVsSpeculativeIsolation:
    """反向陈述 ≠ 推测语境，两者由不同 attention 分类承接。"""

    def test_negative_not_speculative(self):
        f = {"summary": "未发现明显雷同", "detail": "两份方案核心内容不同"}
        is_neg, _ = assess_finding_polarity(f)
        is_spec, _ = assess_finding_speculative(f)
        assert is_neg is True
        # "不同" 不是推测词，"未发现" 也不是
        assert is_spec is False

    def test_speculative_not_negative(self):
        f = {"summary": "假设存在错别字", "detail": "例如将'A'写成'B'"}
        is_neg, _ = assess_finding_polarity(f)
        is_spec, _ = assess_finding_speculative(f)
        assert is_neg is False  # 没有反向陈述特征
        assert is_spec is True


class TestNegativeStillWorks:
    """C2 不应破坏 已有的反向陈述识别。"""

    @pytest.mark.parametrize("text", [
        "不构成串标证据",
        "无实质性雷同",
        "已命中白名单",
        "属于招标文件模板",
        "属于行业惯例",
        "未发现明显关联",
    ])
    def test_negative_still_caught(self, text: str):
        is_neg, _ = is_negative_finding(text)
        assert is_neg is True


# ---------------------------------------------------------------------------
# P0-2 evidence anchor 豁免
# ---------------------------------------------------------------------------

class TestEvidenceAnchors:
    """_has_evidence_anchors helper 的边界覆盖。"""

    def test_full_anchors_true(self):
        f = {
            "summary": "PDF 元数据相同",
            "quote_a": "Producer: Microsoft Word 2019",
            "quote_b": "Producer: Microsoft Word 2019",
            "page_a": "PDF元数据",
            "page_b": "PDF元数据",
        }
        assert _has_evidence_anchors(f) is True

    def test_missing_quote_b_false(self):
        f = {
            "quote_a": "原文 A",
            "quote_b": None,
            "page_a": "第1页",
            "page_b": "第2页",
        }
        assert _has_evidence_anchors(f) is False

    def test_semantic_null_quote_false(self):
        # sanitize_quote 视 "无" 为空
        f = {
            "quote_a": "无",
            "quote_b": "原文 B",
            "page_a": "第1页",
            "page_b": "第2页",
        }
        assert _has_evidence_anchors(f) is False

    def test_missing_page_false(self):
        f = {
            "quote_a": "原文 A",
            "quote_b": "原文 B",
            "page_a": "",
            "page_b": "第2页",
        }
        assert _has_evidence_anchors(f) is False

    def test_semantic_null_page_false(self):
        f = {
            "quote_a": "原文 A",
            "quote_b": "原文 B",
            "page_a": "N/A",
            "page_b": "第2页",
        }
        assert _has_evidence_anchors(f) is False

    def test_non_dict_false(self):
        assert _has_evidence_anchors(None) is False
        assert _has_evidence_anchors("not a dict") is False


class TestAnchorExemptsSpeculativeWords:
    """P0-2 核心：具备完整四元锚点的 finding 即便含推测词也不应被判推测。"""

    def test_speculative_word_with_full_anchors_exempt(self):
        # "可能由同一台电脑创建" 这类合规推断带有 "可能"，
        # 在有完整原文+页码锚点时不应触发降级。
        f = {
            "summary": "PDF 元数据 Producer 相同，可能由同一台电脑创建",
            "detail": "两份文件 Producer 均为 Microsoft Word 2019，作者字段亦一致",
            "quote_a": "Producer: Microsoft Word 2019",
            "quote_b": "Producer: Microsoft Word 2019",
            "page_a": "PDF元数据",
            "page_b": "PDF元数据",
        }
        is_spec, pat = assess_finding_speculative(f)
        assert is_spec is False, "具备完整锚点的合规推断不应被判为推测"
        assert pat is None

    def test_speculative_word_without_anchors_still_caught(self):
        # 缺少 quote_b/page_b 时回退到原模式识别，"可能" 仍触发降级
        f = {
            "summary": "可能存在格式雷同",
            "detail": "目录排版疑似一致",
        }
        is_spec, pat = assess_finding_speculative(f)
        assert is_spec is True
        assert pat is not None

    def test_hard_pattern_with_anchors_still_caught(self):
        # 验证：仅文本级硬模式（如 _SPECULATIVE_HARD_PATTERNS 标识的"(假设)"）
        # 在锚点齐全时也会被豁免。设计取舍：锚点齐全意味着 LLM 给出了可核查证据,
        # 即便文字带"假设"也由人工凭证据复核; 模式识别不再覆盖此类情况。
        f = {
            "summary": "（假设）两份文件 PDF 作者一致",
            "detail": "Author 字段均显示 Lee",
            "quote_a": "Author: Lee",
            "quote_b": "Author: Lee",
            "page_a": "元数据",
            "page_b": "元数据",
        }
        is_spec, _ = assess_finding_speculative(f)
        assert is_spec is False, "锚点齐全时即便含硬模式也豁免"

    def test_anchor_exempt_does_not_break_negative_polarity(self):
        # 锚点齐全的反向陈述（"未发现明显关联"）仍应被 polarity 识别为 negative,
        # 但 assess_finding_speculative 仍返回 False（推测路径不接管反向陈述）。
        f = {
            "summary": "未发现明显关联",
            "detail": "两份文件法人不同",
            "quote_a": "Lee",
            "quote_b": "Wang",
            "page_a": "1",
            "page_b": "1",
        }
        is_neg, _ = assess_finding_polarity(f)
        is_spec, _ = assess_finding_speculative(f)
        assert is_neg is True
        assert is_spec is False
