""": 禁止项按违禁物/允许区/禁区泛化核验，不能只采信合规声明。"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.ai.gates.base import GateContext
from app.core.ai.gates.review.prohibition_leak import ProhibitionLeakGate
from app.core.ai.gates.review.prohibition_policy import (
    classify_locus,
    has_quote_object,
    is_prohibition,
    parse_prohibition,
    prohibition_search_terms,
    prohibition_supplement,
    scan_prohibition_leaks,
)
from app.core.ai.reviewer_parts.evidence_policy import (
    EvidenceAdequacy,
    should_search_independently,
)

_PRICE_REQ = (
    "严禁在技术标、商务标中出现任何价格相关内容（总价、单价、清单价等），"
    "具体价格必须单独编制报价文件放入第二信封，否则废标。"
)
_IDENTITY_REQ = "技术标中不得出现投标人名称、标识。"
_SUBCONTRACT_REQ = "投标人不得将本工程分包，否则废标。"


def test_parses_price_isolation_as_quote_prohibition() -> None:
    spec = parse_prohibition(_PRICE_REQ)
    assert spec is not None
    assert spec.detect_kind == "quote"
    assert "价格" in spec.objects or "总价" in spec.objects
    assert "第二信封" in spec.allowed_loci
    assert "技术标" in spec.forbidden_loci
    terms = prohibition_search_terms(_PRICE_REQ)
    assert "投标函" in terms
    assert "投标书" in terms


def test_parses_identity_and_subcontract_as_other_kinds() -> None:
    identity = parse_prohibition(_IDENTITY_REQ)
    assert identity is not None
    assert identity.detect_kind == "identity"
    assert any("名称" in obj or "标识" in obj for obj in identity.objects)
    assert "技术标" in identity.forbidden_loci

    subcontract = parse_prohibition(_SUBCONTRACT_REQ)
    assert subcontract is not None
    assert subcontract.detect_kind == "literal"
    assert "分包" in subcontract.objects


def test_must_have_and_behavior_are_not_file_scan_prohibitions() -> None:
    assert is_prohibition("投标人须提供有效期内的营业执照副本。") is False
    assert prohibition_search_terms("投标人须提供有效期内的营业执照副本。") == ()
    assert is_prohibition("投标人不得相互串通投标。") is False
    assert is_prohibition("逾期送达的投标文件不予接收。") is False
    assert is_prohibition("投标报价不得高于最高限价。") is False


def test_comparative_limit_unrelated_paren_is_not_file_scan_prohibition() -> None:
    """比较性「不得」的邻句括号不是出现谓词的论元。"""
    tax_ceiling = (
        "本项目预算控制价/限价为人民币100万元（含税），"
        "投标报价不得超过该限价，超出者作无效报价处理。"
    )
    vat_ceiling = (
        "最高投标限价为人民币800万元（含增值税），投标总价不得超过该限价。"
    )
    assert is_prohibition(tax_ceiling) is False
    assert prohibition_search_terms(tax_ceiling) == ()
    assert is_prohibition(vat_ceiling) is False
    spec = parse_prohibition(tax_ceiling)
    assert spec is None


def test_appear_clause_does_not_absorb_later_comparative_paren() -> None:
    """出现子句论元不得吞掉后半段比较性不得的括号注释。"""
    spec = parse_prohibition(
        "严禁在技术标中出现任何价格相关内容（总价、单价），"
        "投标报价不得超过最高限价（含税）。"
    )
    assert spec is not None
    assert "总价" in spec.objects or "价格" in spec.objects
    assert "含税" not in spec.objects


def test_locus_allows_second_envelope_and_forbids_bid_letter() -> None:
    spec = parse_prohibition(_PRICE_REQ)
    assert spec is not None
    assert classify_locus("商务标/二信封开标一览表", "下浮9%", spec) == "allowed"
    assert classify_locus("一、投标函", "我方投标报价下浮9%", spec) == "forbidden"
    assert classify_locus("技术方案", "实施计划", spec) == "unknown"


def test_quote_object_ignores_deposit_and_placeholder() -> None:
    assert has_quote_object("投标保证金人民币十万元整") is False
    assert has_quote_object(
        "本投标报价不允许出现具体价格，(此价格详见第二信封) X%"
    ) is False
    assert has_quote_object("我方投标报价下浮9%") is True
    assert has_quote_object("投标总价人民币128.5万元") is True


def test_scan_price_keeps_letter_and_drops_envelope_and_echo() -> None:
    spec = parse_prohibition(_PRICE_REQ)
    assert spec is not None
    hits = scan_prohibition_leaks(
        [
            {
                "page": 9,
                "section": "二信封开标一览表",
                "content": "开标一览表 投标报价下浮9%",
            },
            {
                "page": 15,
                "section": "投标报价声明",
                "content": "本投标报价不允许出现具体价格，具体价格详见第二信封。",
            },
            {
                "page": 3,
                "section": "一、投标函",
                "content": "我方投标报价下浮9%，详见投标函正文。",
            },
            {
                "page": 4,
                "section": "",
                "content": "投标函\n我方投标总价人民币88万元整。",
            },
        ],
        spec,
    )
    assert [hit.page for hit in hits] == [3, 4]
    assert all(hit.locus == "forbidden" for hit in hits)


def test_scan_identity_hits_tech_bid_company_not_echo_or_qualify() -> None:
    spec = parse_prohibition(_IDENTITY_REQ)
    assert spec is not None
    hits = scan_prohibition_leaks(
        [
            {
                "page": 2,
                "section": "技术标/施工组织",
                "content": "我公司<ORG_10>承诺按期完工。",
            },
            {
                "page": 6,
                "section": "资格证明",
                "content": "营业执照：<ORG_10>",
            },
            {
                "page": 1,
                "section": "投标须知",
                "content": "技术标中不得出现投标人名称、标识。",
            },
        ],
        spec,
    )
    assert [hit.page for hit in hits] == [2]


def test_scan_subcontract_needs_positive_voice() -> None:
    spec = parse_prohibition(_SUBCONTRACT_REQ)
    assert spec is not None
    hits = scan_prohibition_leaks(
        [
            {
                "page": 8,
                "section": "实施方案",
                "content": "我方将本工程分包给具备资质的第三方实施。",
            },
            {
                "page": 9,
                "section": "承诺函",
                "content": "我方不进行分包，全部自行施工。",
            },
            {
                "page": 1,
                "section": "招标要求",
                "content": "投标人不得将本工程分包，否则废标。",
            },
        ],
        spec,
    )
    assert [hit.page for hit in hits] == [8]


def test_complete_prohibition_item_still_triggers_independent_search() -> None:
    state = EvidenceAdequacy(
        has_response=True,
        is_reviewable=True,
        is_mandatory=True,
        has_source_pages=True,
        completeness="full",
        missing_elements=(),
        evidence_anchor="二信封开标一览表",
        facet_missing_keys=(),
        has_local_image_evidence=True,
        is_prohibition=True,
    )
    assert should_search_independently(state) is True


def test_gate_forces_not_compliant_when_forbidden_object_leaks() -> None:
    gate = ProhibitionLeakGate()
    ctx = GateContext(
        flow="investigate",
        stage="post_llm",
        target=SimpleNamespace(comment="完全符合，价格已隔离至第二信封。", score=100),
        evidence={
            "requirement_text": _PRICE_REQ,
            "prohibition_leak_hits": [
                {
                    "page": 3,
                    "section": "投标函",
                    "snippet": "我方投标报价下浮9%",
                    "locus": "forbidden",
                    "obj": "价格",
                }
            ],
        },
    )
    assert gate.applies(ctx) is True
    verdict = gate.evaluate(ctx)
    assert verdict.verdict == "enforce_patch"
    patch = verdict.suggested_patch or {}
    assert patch["compliance_level"] == "not"
    assert patch["is_compliant"] is False
    assert patch["score"] == 0
    assert patch["risk_level"] == "high"
    assert "第3页" in patch["comment"]
    assert "下浮9%" in patch["comment"]


def test_gate_accepts_legacy_price_leak_hits_key() -> None:
    gate = ProhibitionLeakGate()
    ctx = GateContext(
        flow="investigate",
        stage="post_llm",
        target=SimpleNamespace(comment="完全符合", score=100),
        evidence={
            "requirement_text": _PRICE_REQ,
            "price_leak_hits": [
                {
                    "page": 3,
                    "section": "投标函",
                    "snippet": "投标总价人民币88万元",
                    "locus": "forbidden",
                }
            ],
        },
    )
    assert gate.applies(ctx) is True


def test_gate_skips_when_no_forbidden_hit() -> None:
    gate = ProhibitionLeakGate()
    ctx = GateContext(
        flow="investigate",
        stage="post_llm",
        target=SimpleNamespace(comment="完全符合", score=100),
        evidence={"requirement_text": _PRICE_REQ, "prohibition_leak_hits": []},
    )
    assert gate.applies(ctx) is False


def test_gate_skips_comparative_limit_even_with_literal_hits() -> None:
    gate = ProhibitionLeakGate()
    ctx = GateContext(
        flow="investigate",
        stage="post_llm",
        target=SimpleNamespace(comment="报价未超限价", score=100),
        evidence={
            "requirement_text": (
                "本项目预算控制价/限价为人民币100万元（含税），"
                "投标报价不得超过该限价，超出者作无效报价处理。"
            ),
            "prohibition_leak_hits": [
                {
                    "page": 3,
                    "section": "开标一览表",
                    "snippet": "含税总报价99万元",
                    "locus": "forbidden",
                    "obj": "含税",
                }
            ],
        },
    )
    assert gate.applies(ctx) is False


def test_supplement_only_for_parsed_prohibition() -> None:
    isolation_pair = SimpleNamespace(
        requirement=SimpleNamespace(description=_PRICE_REQ)
    )
    identity_pair = SimpleNamespace(
        requirement=SimpleNamespace(description=_IDENTITY_REQ)
    )
    other_pair = SimpleNamespace(
        requirement=SimpleNamespace(description="须提供营业执照")
    )
    price_text = prohibition_supplement(isolation_pair)
    assert "禁止项·反向核验" in price_text
    assert "价格" in price_text or "总价" in price_text
    identity_text = prohibition_supplement(identity_pair)
    assert "禁止项·反向核验" in identity_text
    assert prohibition_supplement(other_pair) == ""
