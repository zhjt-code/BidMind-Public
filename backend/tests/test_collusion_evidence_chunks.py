"""问题1 防御测试：重合片段载荷构建（_build_evidence_chunks_payload）

映射风险：命中白名单/疑似模板时维度被门控归零、findings 为空，用户只看到
"重合可能源于通用模板"的结论却看不到具体重合内容，无法甄别。本组测试锁定
similar_chunks → evidence_chunks 的压缩与"疑似通用模板"标注必须被保留。
"""

from app.core.collusion.analyzer_parts.pair_persistence import (
    _build_evidence_chunks_payload,
)


def _chunk(**kw):
    base = {
        "text_a": "甲方文本片段",
        "text_b": "乙方文本片段",
        "page_a": 3,
        "page_b": 5,
        "score": 0.82,
        "literal_score": 0.41,
        "boilerplate_flag": False,
        "kb_rule_match": False,
    }
    base.update(kw)
    return base


class TestBuildEvidenceChunksPayload:
    def test_none_returns_empty(self):
        assert _build_evidence_chunks_payload(None) == []

    def test_empty_returns_empty(self):
        assert _build_evidence_chunks_payload([]) == []

    def test_normal_pair_fields_preserved(self):
        out = _build_evidence_chunks_payload([_chunk()])
        assert len(out) == 1
        item = out[0]
        assert item["text_a"] == "甲方文本片段"
        assert item["text_b"] == "乙方文本片段"
        assert item["page_a"] == 3
        assert item["page_b"] == 5
        assert item["score"] == 0.82
        assert item["literal_score"] == 0.41
        assert item["boilerplate_flag"] is False
        assert item["kb_rule_match"] is False

    def test_template_flags_passthrough(self):
        """命中白名单/疑似模板标注必须保留，前端据此弱化展示。"""
        out = _build_evidence_chunks_payload([
            _chunk(boilerplate_flag=True, kb_rule_match=True),
        ])
        assert out[0]["boilerplate_flag"] is True
        assert out[0]["kb_rule_match"] is True

    def test_text_truncated(self):
        long_text = "字" * 1000
        out = _build_evidence_chunks_payload([_chunk(text_a=long_text)], max_chars=300)
        assert len(out[0]["text_a"]) == 300

    def test_skip_when_both_texts_empty(self):
        out = _build_evidence_chunks_payload([_chunk(text_a="", text_b="   ")])
        assert out == []

    def test_max_pairs_capped(self):
        chunks = [_chunk(text_a=f"a{i}", text_b=f"b{i}") for i in range(40)]
        out = _build_evidence_chunks_payload(chunks, max_pairs=15)
        assert len(out) == 15

    def test_non_numeric_score_becomes_none(self):
        out = _build_evidence_chunks_payload([_chunk(score="高", literal_score=None)])
        assert out[0]["score"] is None
        assert out[0]["literal_score"] is None

    def test_non_dict_items_skipped(self):
        out = _build_evidence_chunks_payload([_chunk(), "garbage", None, 123])
        assert len(out) == 1
