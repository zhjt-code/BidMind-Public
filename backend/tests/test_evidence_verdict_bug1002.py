""" 合同 A: 证据不足/未找到/包内命中 落库判定与占位符单源。"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from types import SimpleNamespace

from app.core.extractor.extraction_failure import EXTRACTION_FAILED_CONTENT
from app.core.extractor.evidence_verdict import (
    EVIDENCE_INSUFFICIENT_CONTENT,
    HIT_UNCONFIRMED_HEADER,
    HIT_UNCONFIRMED_NOTE,
    HIT_UNCONFIRMED_USER_LABEL,
    NOT_FOUND_CONTENT,
    build_pack_partial_content,
    decide_bid_response_persist,
    is_evidence_insufficient_content,
    is_hit_unconfirmed_content,
    is_not_found_content,
    is_unconfirmed_response_content,
    pack_has_substantial_hits,
    user_facing_response_text,
)
from app.core.matcher.requirement_matcher import MatchedPair


def test_placeholders_are_unconfirmed() -> None:
    assert is_not_found_content(NOT_FOUND_CONTENT)
    assert is_evidence_insufficient_content(EVIDENCE_INSUFFICIENT_CONTENT)
    assert is_unconfirmed_response_content(NOT_FOUND_CONTENT)
    assert is_unconfirmed_response_content(EXTRACTION_FAILED_CONTENT)
    assert is_unconfirmed_response_content(EVIDENCE_INSUFFICIENT_CONTENT)
    assert is_unconfirmed_response_content("  [证据不足待复核]  ")
    assert not is_unconfirmed_response_content("已提供营业执照扫描件")
    assert not is_evidence_insufficient_content(NOT_FOUND_CONTENT)


def test_starvation_or_facet_gap_never_not_found() -> None:
    starved = decide_bid_response_persist(
        facet_missing=False, starvation=True, pack_has_substantial=True
    )
    gapped = decide_bid_response_persist(
        facet_missing=True, starvation=False, pack_has_substantial=True
    )
    assert starved.action == "insufficient"
    assert gapped.action == "insufficient"


def test_covered_pack_missed_by_llm_is_partial() -> None:
    verdict = decide_bid_response_persist(
        facet_missing=False, starvation=False, pack_has_substantial=True
    )
    assert verdict.action == "pack_as_partial"
    assert verdict.relevance_note == HIT_UNCONFIRMED_NOTE


def test_empty_exhausted_pack_allows_not_found() -> None:
    verdict = decide_bid_response_persist(
        facet_missing=False, starvation=False, pack_has_substantial=False
    )
    assert verdict.action == "not_found"


def test_pack_substantial_ignores_toc_dot_leaders() -> None:
    assert pack_has_substantial_hits(["目录 .......... 1", "目 录"]) is False
    assert pack_has_substantial_hits(["合同编号 HT-2024-001 金额 120 万元"]) is True


def test_pack_partial_content_is_short_status_not_snippets() -> None:
    """实质命中只落短状态，禁止把检索原文拼进用户可见 content。"""
    text = build_pack_partial_content(["第36页 业绩合同扫描件", "验收意见：合格"])
    assert text == HIT_UNCONFIRMED_HEADER
    assert "业绩合同" not in text
    assert "验收意见" not in text
    assert NOT_FOUND_CONTENT not in text
    assert not is_unconfirmed_response_content(text)


def test_pack_partial_content_scan_summary_also_stays_out() -> None:
    """同形态第二例：扫描件摘要包同样不得进入 content。"""
    text = build_pack_partial_content(
        ["[投标证明材料] 第59页提供了合同扫描件：甲方某某公司"]
    )
    assert text == HIT_UNCONFIRMED_HEADER
    assert "扫描件" not in text
    assert "甲方" not in text


def test_pack_partial_content_toc_only_is_insufficient() -> None:
    text = build_pack_partial_content(["目 录 一、投标函 ........................ 1"])
    assert text == EVIDENCE_INSUFFICIENT_CONTENT


def test_user_facing_response_prefers_summary_over_pack_dump() -> None:
    dump = HIT_UNCONFIRMED_HEADER + "\n目 录\n一、投标函 ........................ 1"
    assert is_hit_unconfirmed_content(dump)
    assert user_facing_response_text(
        content=dump,
        summary="投标文件未对分包管理要求作出明确响应",
    ) == "投标文件未对分包管理要求作出明确响应"


def test_user_facing_response_scan_dump_falls_back_to_status() -> None:
    dump = HIT_UNCONFIRMED_HEADER + "\n[投标证明材料] 第59页合同扫描件"
    assert user_facing_response_text(content=dump, summary=dump) == HIT_UNCONFIRMED_USER_LABEL


def test_user_facing_response_confirmed_prefers_summary() -> None:
    assert user_facing_response_text(
        content="投标人持有建筑工程施工总承包一级资质证书，编号 A123",
        summary="已提供一级资质证书",
    ) == "已提供一级资质证书"


def test_matcher_treats_insufficient_as_no_response() -> None:
    req = SimpleNamespace(id=1, category=None, is_mandatory=0)
    pair = MatchedPair(
        requirement=req,
        response=SimpleNamespace(id=9, content=EVIDENCE_INSUFFICIENT_CONTENT),
    )
    assert pair.has_response is False
    found = MatchedPair(
        requirement=req,
        response=SimpleNamespace(id=10, content="提供安全生产许可证"),
    )
    assert found.has_response is True
    missing = MatchedPair(
        requirement=req,
        response=SimpleNamespace(id=11, content=NOT_FOUND_CONTENT),
    )
    assert missing.has_response is False
    failed = MatchedPair(
        requirement=req,
        response=SimpleNamespace(id=12, content=EXTRACTION_FAILED_CONTENT),
    )
    assert failed.has_response is False
