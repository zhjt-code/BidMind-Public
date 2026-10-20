import asyncio
from types import SimpleNamespace

import pytest

from app.core.collusion.analyzer import (
    CollusionAnalyzer,
    _extract_structured_findings,
)
from app.core.collusion.analyzer_parts import pair_evidence as pair_evidence_mod
from app.core.collusion.evidence_fusion import EvidenceFusionEngine


class _FakeRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _stmt):
        return _FakeRows(self._rows)


class _FakeAsyncSession:
    """支持 ``async with`` 协议的伪 session, 模拟 短 session 模式。"""

    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, _stmt):
        return _FakeRows(self._rows)


def _fake_analyzer(rows) -> CollusionAnalyzer:
    analyzer = CollusionAnalyzer.__new__(CollusionAnalyzer)
    analyzer._db = _FakeDb(rows)
    analyzer._db_lock = asyncio.Lock()
    return analyzer


def _patch_short_session(monkeypatch: pytest.MonkeyPatch, rows) -> None:
    """ 修复后, _chunks_for_doc 改用 async_session_factory 短 session,
 测试需 monkeypatch pair_evidence 模块内的 factory 引用以注入伪 session。"""
    monkeypatch.setattr(
        pair_evidence_mod, "async_session_factory",
        lambda: _FakeAsyncSession(rows),
    )


def test_extract_structured_findings_filters_items_without_quote() -> None:
    compare_result = {
        "pair_id": "1:2",
        "pair_ref": {"doc_a_name": "A.pdf", "doc_b_name": "B.pdf"},
        "d3_text": {
            "score": 0.9,
            "certainty": 0.9,
            "evidence_level": "second",
            "findings": [
                {
                    "summary": "只有页码的派生项",
                    "detail": "该项没有可复核原文",
                    "page_a": "第12页",
                    "page_b": "第12页",
                },
                {
                    "summary": "带原文引用的正式证据",
                    "detail": "两份文件相同段落",
                    "quote_a": "施工组织方案完全一致",
                    "page_a": "第12页",
                    "quote_b": "施工组织方案完全一致",
                    "page_b": "第15页",
                },
            ],
        },
    }

    findings = _extract_structured_findings(compare_result, 1, 2)

    assert len(findings) == 1
    assert findings[0]["description"] == "带原文引用的正式证据"
    assert findings[0]["evidences"][0]["quote"] == "施工组织方案完全一致"
    assert compare_result["_attention_items"][0]["category"] == "unverified_quote"


def test_backfill_compare_result_uses_page_to_fill_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        SimpleNamespace(page_num=12, content="施工组织方案完全一致，采用同一进度计划和资源配置。"),
    ]
    # _chunks_for_doc 改为按需短 session, 测试需注入 fake factory
    _patch_short_session(monkeypatch, rows)

    analyzer = _fake_analyzer(rows)
    compare_result = {
        "pair_ref": {"doc_a_name": "A.pdf", "doc_b_name": "B.pdf"},
        "d3_text": {
            "score": 0.9,
            "certainty": 0.9,
            "findings": [{"summary": "页码已知", "page_a": "第12页"}],
        },
    }

    asyncio.run(
        CollusionAnalyzer._backfill_compare_result_evidence_refs(
            analyzer, compare_result, 1, 2,
        )
    )

    finding = compare_result["d3_text"]["findings"][0]
    assert "施工组织方案完全一致" in finding["quote_a"]
    assert finding["evidences"][0]["quote"]


def test_backfill_compare_result_uses_quote_to_fill_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        SimpleNamespace(page_num=18, content="本项目报价基数按清单总价统一计算。"),
    ]
    # _chunks_for_doc 改为按需短 session, 测试需注入 fake factory
    _patch_short_session(monkeypatch, rows)

    analyzer = _fake_analyzer(rows)
    compare_result = {
        "pair_ref": {"doc_a_name": "A.pdf", "doc_b_name": "B.pdf"},
        "d4_price": {
            "score": 0.8,
            "certainty": 0.8,
            "findings": [{"summary": "引文已知", "quote_a": "报价基数按清单总价统一计算"}],
        },
    }

    asyncio.run(
        CollusionAnalyzer._backfill_compare_result_evidence_refs(
            analyzer, compare_result, 1, 2,
        )
    )

    finding = compare_result["d4_price"]["findings"][0]
    assert finding["page_a"] == "第18页"
    assert finding["evidences"][0]["location"] == "第18页"


def test_evidence_fusion_drops_group_findings_without_quotes() -> None:
    engine = EvidenceFusionEngine({1: "A.pdf", 2: "B.pdf"})
    fused = engine.fuse_for_group(
        member_doc_ids=[1, 2],
        related_pair_ids=["1:2"],
        pair_findings_map={
            "1:2": [
                {
                    "dimension": "D6",
                    "level": "third",
                    "description": "只有页码的格式提示",
                    "evidences": [{"doc_id": 1, "doc_name": "A.pdf", "location": "第5页"}],
                },
                {
                    "dimension": "D3",
                    "level": "second",
                    "description": "文本雷同",
                    "evidences": [
                        {
                            "doc_id": 1,
                            "doc_name": "A.pdf",
                            "quote": "完全一致的技术方案",
                            "location": "第6页",
                        }
                    ],
                },
            ],
        },
    )

    assert len(fused) == 1
    assert fused[0]["dimension"] == "D3"
    assert fused[0]["evidences"][0]["quote"] == "完全一致的技术方案"
