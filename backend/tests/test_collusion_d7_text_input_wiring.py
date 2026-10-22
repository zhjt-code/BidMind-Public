"""Plan Step 3 防御测试：D7AbnormalConsistencyGate text_a/text_b 接线 helper。

背景: analyzer.py 构造 Gate evidence 时未提供 text_a/text_b，导致
D7 算法判定恒返回 ``reason="文本不可用"``，算法 LCS / 共同错别字检测形同虚设。

本测试固定 helper ``_build_chunk_text_blob`` 行为，覆盖:
  - 正常拼接 chunk 内容
  - 字符上限截断
  - 文档缺失 / chunk 列表为空 / 内容为空 / None 内容
  - 与 D7AbnormalConsistencyGate 真实接线后能驱动算法路径
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")


def test_concatenates_chunk_contents():
    from app.core.collusion.analyzer import _build_chunk_text_blob

    chunk_texts = {1: [(1, "AAA"), (2, "BBB"), (3, "CCC")], 2: []}
    blob = _build_chunk_text_blob(chunk_texts, 1)
    assert "AAA" in blob
    assert "BBB" in blob
    assert "CCC" in blob


def test_caps_total_length():
    from app.core.collusion.analyzer import _build_chunk_text_blob

    chunk_texts = {1: [(p, "x" * 6000) for p in range(1, 11)]}
    blob = _build_chunk_text_blob(chunk_texts, 1, max_chars=10000)
    assert len(blob) <= 10000


def test_returns_empty_for_missing_doc():
    from app.core.collusion.analyzer import _build_chunk_text_blob

    chunk_texts = {1: [(1, "abc")]}
    blob = _build_chunk_text_blob(chunk_texts, 999)
    assert blob == ""


def test_returns_empty_for_no_chunks():
    from app.core.collusion.analyzer import _build_chunk_text_blob

    chunk_texts: dict = {1: []}
    blob = _build_chunk_text_blob(chunk_texts, 1)
    assert blob == ""


def test_skips_empty_or_none_content():
    from app.core.collusion.analyzer import _build_chunk_text_blob

    chunk_texts = {1: [(1, ""), (2, None), (3, "real-content")]}
    blob = _build_chunk_text_blob(chunk_texts, 1)
    assert "real-content" in blob


def test_non_positive_max_chars_returns_empty():
    """边界: max_chars<=0 应直接返回空，避免歧义切片。"""
    from app.core.collusion.analyzer import _build_chunk_text_blob

    chunk_texts = {1: [(1, "abc")]}
    assert _build_chunk_text_blob(chunk_texts, 1, max_chars=0) == ""
    assert _build_chunk_text_blob(chunk_texts, 1, max_chars=-5) == ""


def test_d7_gate_recovers_from_text_unavailable_when_wired():
    """与 D7AbnormalConsistencyGate 真实联动: 接入 text_a/text_b 后不再返回 '文本不可用'。"""
    from app.core.ai.gates.base import GateContext
    from app.core.ai.gates.collusion.d7_abnormal_consistency import (
        D7AbnormalConsistencyGate,
    )
    from app.core.collusion.analyzer import _build_chunk_text_blob

    base = (
        "本公司具有丰富的施工管理经验，项目经理具备一级建造师资格证书，"
        "曾主持多个大型市政工程项目并均按时高质完工，"
        "技术团队拥有专业工程师十余名，设备齐全且均已通过质量认证。"
    )
    long_text = (base * 4)[:1500]

    chunk_texts = {10: [(1, long_text)], 20: [(1, long_text)]}
    text_a = _build_chunk_text_blob(chunk_texts, 10)
    text_b = _build_chunk_text_blob(chunk_texts, 20)
    assert text_a and text_b, "helper 应能产出非空字符串供 Gate 消费"

    ctx = GateContext(
        flow="collusion",
        stage="post_llm",
        target={},
        evidence={"text_a": text_a, "text_b": text_b},
    )
    verdict = D7AbnormalConsistencyGate().evaluate(ctx)
    assert verdict.reason != "文本不可用", (
        "接入 chunk 文本后 D7 不应再返回 '文本不可用'"
    )


def test_d7_gate_still_degrades_when_chunk_texts_empty():
    """边界: 文档无 chunk 时 helper 返回空字符串，D7 仍走 '文本不可用' 兜底分支。"""
    from app.core.ai.gates.base import GateContext
    from app.core.ai.gates.collusion.d7_abnormal_consistency import (
        D7AbnormalConsistencyGate,
    )
    from app.core.collusion.analyzer import _build_chunk_text_blob

    chunk_texts: dict = {1: [], 2: []}
    text_a = _build_chunk_text_blob(chunk_texts, 1)
    text_b = _build_chunk_text_blob(chunk_texts, 2)
    assert text_a == "" and text_b == ""

    ctx = GateContext(
        flow="collusion",
        stage="post_llm",
        target={},
        evidence={"text_a": text_a, "text_b": text_b},
    )
    verdict = D7AbnormalConsistencyGate().evaluate(ctx)
    assert verdict.reason == "文本不可用"
    assert verdict.confidence == 0.0
