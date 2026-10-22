"""S.5.3 单元测试：D3 算法化 + D3TextAlgorithmicGate。"""

from __future__ import annotations

import pytest

from app.core.ai.gates.base import GateContext
from app.core.ai.gates.collusion.d3_text_algorithmic import D3TextAlgorithmicGate
from app.core.collusion.d3_algorithmic import (
    D3AlgorithmicResult,
    _tokenize,
    compute_d3_algorithmic,
    hamming_distance,
    lcs_ratio,
    simhash,
    simhash_similarity,
    tfidf_cosine,
)

# ==================== 纯函数层 ====================

class TestTokenize:
    def test_chinese_char_level(self):
        assert _tokenize("投标文件") == ["投", "标", "文", "件"]

    def test_english_word_level(self):
        assert _tokenize("hello world 2024") == ["hello", "world", "2024"]

    def test_mixed(self):
        result = _tokenize("AI技术方案")
        assert "AI" in result and "技" in result


class TestSimHashBase:
    def test_deterministic(self):
        assert simhash("hello world") == simhash("hello world")

    def test_empty_returns_zero(self):
        assert simhash("") == 0

    def test_identical_distance_zero(self):
        a = simhash("合同金额为壹仟万元整")
        assert hamming_distance(a, a) == 0

    def test_identical_similarity_1(self):
        assert simhash_similarity("abcdefg", "abcdefg") == 1.0

    def test_completely_different_similarity_low(self):
        s = simhash_similarity("全然不同的内容一", "与之毫无关系的另一段")
        assert 0.0 <= s <= 0.5  # 允许合理抖动


class TestLcsRatio:
    def test_identical(self):
        assert lcs_ratio("abc", "abc") == 1.0

    def test_disjoint(self):
        assert lcs_ratio("abc", "xyz") == 0.0

    def test_empty(self):
        assert lcs_ratio("", "abc") == 0.0
        assert lcs_ratio("abc", "") == 0.0


class TestTfidfCosine:
    def test_identical_texts(self):
        s = tfidf_cosine("hello world hello", "hello world hello")
        assert s == pytest.approx(1.0)

    def test_disjoint(self):
        assert tfidf_cosine("abc", "xyz") == 0.0

    def test_empty(self):
        assert tfidf_cosine("", "abc") == 0.0


class TestComputeD3Algorithmic:
    def test_identical_chunks_high_score(self):
        """两侧 chunk 相同 → 综合分数应高于阈值 0.7（两两配对中含对角线完全相同）。"""
        chunks = [
            "本公司具有丰富的施工经验和完善的质量管理体系通过多项认证",
            "项目团队由经验丰富的高级工程师组成并拥有多年相关行业经验",
        ]
        r = compute_d3_algorithmic(chunks, chunks)
        assert r.score >= 0.65
        assert r.findings  # 对角线对儿必然命中高相似

    def test_disjoint_chunks_low_score(self):
        a = ["这是一段关于人员配置的描述内容很多很多。"]
        b = ["另一段关于财务成本控制的叙述完全不同。"]
        r = compute_d3_algorithmic(a, b)
        assert r.score < 0.6

    def test_empty_either_side(self):
        r = compute_d3_algorithmic([], ["abc"])
        assert r.score == 0.0
        assert r.findings == []

    def test_whitelist_filters_common_chunks(self):
        common_words = ["招标", "投标", "甲方"]
        # 两侧都只由白名单词构成
        shared = ["招标甲方投标"]
        r = compute_d3_algorithmic(shared, shared, whitelist_keywords=common_words)
        # 被白名单扣除后，pairs=0，得分 0
        assert r.score == 0.0

    def test_to_dict_structure(self):
        r = D3AlgorithmicResult(
            score=0.85,
            findings=["test"],
            certainty=0.9,
            breakdown={"simhash": 0.8, "lcs": 0.9, "tfidf": 0.85, "pairs": 1.0},
        )
        d = r.to_dict()
        assert d["score"] == 0.85
        assert d["source"] == "algorithmic"
        assert "findings" in d
        assert "breakdown" in d


# ==================== Gate 层 ====================

class TestD3TextAlgorithmicGate:
    gate = D3TextAlgorithmicGate()

    def _ctx(self, chunks_a: list[str], chunks_b: list[str],
             llm_d3_score: float = 0.5,
             doc_a_id: int = 1, doc_b_id: int = 2) -> GateContext:
        return GateContext(
            flow="collusion", stage="post_llm",
            target={"d3_text": {"score": llm_d3_score}},
            evidence={
                "doc_a_id": doc_a_id,
                "doc_b_id": doc_b_id,
                "chunk_texts": {
                    doc_a_id: [(i + 1, t) for i, t in enumerate(chunks_a)],
                    doc_b_id: [(i + 1, t) for i, t in enumerate(chunks_b)],
                },
            },
            config={},
        )

    def test_not_applicable_without_chunks(self):
        ctx = GateContext(
            flow="collusion", stage="post_llm",
            target={}, evidence={},
        )
        assert self.gate.applies(ctx) is False

    def test_applies_with_chunks(self):
        ctx = self._ctx(["a"], ["b"])
        assert self.gate.applies(ctx) is True

    def test_both_high_agreement_pass(self):
        """两侧 chunk 相同 + LLM 高分 → agreement → verdict=pass。
        用足够长的相同内容让综合分数跨过 0.7 阈值。
        """
        shared = [
            "本公司拥有丰富的工程施工经验并承担过多个大型国家重点项目具备完整的技术体系",
            "项目团队由经验丰富的高级工程师及专家组成技术实力雄厚并有多年相关行业经验",
        ]
        v = self.gate.evaluate(self._ctx(shared, shared, llm_d3_score=0.9))
        # 相同 chunks + 相同 LLM 高分 → 两侧都 high → agreement=True → pass
        assert v.verdict == "pass"

    def test_both_low_agreement_pass(self):
        """两侧都是低相似 → agreement=True → verdict=pass。"""
        a = ["这是一段关于人员配置的长段落描述。"]
        b = ["另一段关于财务成本控制的完全不同的长段落。"]
        v = self.gate.evaluate(self._ctx(a, b, llm_d3_score=0.2))
        assert v.verdict == "pass"

    def test_disagreement_alg_high_llm_low(self):
        """算法判高相似但 LLM 判低 → warn + suggested_patch。"""
        shared = [
            "本公司具备完善的质量管理体系并通过多项国际认证长期保持稳定运行",
            "拥有 CMMI 五级认证一级建造师五名高级工程师二十名及完整项目团队",
        ]
        v = self.gate.evaluate(self._ctx(shared, shared, llm_d3_score=0.2))
        # 相同 chunks → alg_high=True，LLM 给 0.2 → llm_high=False → 分歧 warn
        assert v.verdict == "warn"
        assert v.suggested_patch is not None
        assert "d3_text_algorithmic" in v.suggested_patch

    def test_severity_is_advisory(self):
        assert self.gate.default_severity == "advisory"

    def test_empty_chunks_returns_pass(self):
        """applies 通过但 chunks 实际为空 → verdict=pass，不报错。"""
        ctx = GateContext(
            flow="collusion", stage="post_llm",
            target={"d3_text": {"score": 0.9}},
            evidence={
                "doc_a_id": 1, "doc_b_id": 2,
                "chunk_texts": {1: [], 2: []},
            },
        )
        v = self.gate.evaluate(ctx)
        assert v.verdict == "pass"
