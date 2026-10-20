""": 成对证据必须分侧锚定，禁止把一侧引文/检索词写成双侧命中。

问题形态: 两侧正式证据身份未独立锚定。
夹具:
- 本案例: D2 法人/地址引文逐字相同、页码不同
- 同形态第二例: D1 人员姓名引文克隆到对侧
"""

from __future__ import annotations

from app.core.collusion.analyzer_parts.algorithmic import _algorithm_compare
from app.core.collusion.analyzer_parts.dimension_signals import (
    synthesize_dimension_signal_findings,
)
from app.core.collusion.paired_evidence_identity import (
    IDENTITY_DIM_KEYS,
    doc_id_for_quote_key,
    iter_side_quote_searches,
    keyword_from_quote,
    quote_fingerprint,
    quotes_collide,
    retract_dim_if_all_unverified,
    side_has_subject,
    side_subject_tokens,
)


SHARED_LICENSE_LINE = (
    '法定代表人:<PERSON_D> 住所地:山西省大同市平城区魏都大道1868号'
    + "御东世纪花园3号楼1单元17层1701室"
)


def test_doc_id_for_quote_key_never_crosses_documents() -> None:
    assert doc_id_for_quote_key("quote_a", 11, 22) == 11
    assert doc_id_for_quote_key("quote_b", 11, 22) == 22
    assert doc_id_for_quote_key("quote_c", 11, 22) is None


def test_keyword_from_quote_uses_own_side_only() -> None:
    finding = {
        "quote_a": '法定代表人：<PERSON_D>，住所地：大同市',
        "quote_b": '法定代表人：<PERSON_E>，住所地：太原市',
    }
    searches = list(iter_side_quote_searches(finding))
    by_key = {item[0]: item[2] for item in searches}
    assert '<PERSON_D>' in by_key["quote_a"]
    assert '<PERSON_E>' in by_key["quote_b"]
    assert by_key["quote_a"] != by_key["quote_b"]


def test_missing_quote_b_does_not_reuse_quote_a_keyword() -> None:
    finding = {"quote_a": '法定代表人：<PERSON_D> 住所地：大同', "quote_b": None}
    searches = list(iter_side_quote_searches(finding))
    keys = [item[0] for item in searches]
    assert keys == ["quote_a"]
    assert keyword_from_quote(None) == ""


def test_quotes_collide_ignores_punctuation() -> None:
    assert quotes_collide(
        '法定代表人：<PERSON_D> 住所地：大同',
        '法定代表人:<PERSON_D>住所地:大同',
    )
    assert not quotes_collide('法定代表人：<PERSON_D>', '法定代表人：<PERSON_E>')
    assert quote_fingerprint("  ") == ""


def test_side_subject_tokens_come_from_company_or_filename() -> None:
    tokens = side_subject_tokens("<ORG_13>", "皇和.pdf")
    assert any("皇和" in t or t == "皇和" for t in tokens)
    assert not any('<PERSON_D>' in t for t in tokens)


def test_side_has_subject_requires_owner_on_that_page() -> None:
    chunks = [
        (30, '<ORG_12> 法定代表人:<PERSON_D> 住所地:大同'),
        (106, '法定代表人:<PERSON_D> 住所地:大同'),
    ]
    tokens = side_subject_tokens("<ORG_12>", "心悦.pdf")
    assert side_has_subject(chunks, 30, tokens) is True
    assert side_has_subject(chunks, 106, tokens) is False


def test_retract_dim_if_all_unverified() -> None:
    dim = {
        "score": 0.95,
        "certainty": 0.95,
        "findings": [{"verified": False, "summary": "法定代表人相同"}],
    }
    assert retract_dim_if_all_unverified(dim) is True
    assert dim["score"] == 0.0
    assert dim["certainty"] == 0.0
    assert dim["gate_rejected"] is True


def test_identity_dim_keys_cover_d1_d2_not_d3() -> None:
    assert "d1_personnel" in IDENTITY_DIM_KEYS
    assert "d2_company" in IDENTITY_DIM_KEYS
    assert "d3_text" not in IDENTITY_DIM_KEYS


def test_algorithm_compare_builds_quotes_from_each_side_fields() -> None:
    info_a = {
        "company_info": {
            "company_name": "<ORG_23>",
            "legal_representative": '<PERSON_B>',
            "registered_address": "北京市朝阳区某街1号",
            "source_page": 3,
        }
    }
    info_b = {
        "company_info": {
            "company_name": "<ORG_3>",
            "legal_representative": '<PERSON_B>',
            "registered_address": "北京市朝阳区某街1号",
            "source_page": 9,
        }
    }
    result = _algorithm_compare(info_a, info_b, "甲.pdf", "乙.pdf")
    lr_findings = [
        f for f in result["d2_company"]["findings"]
        if "法定代表人" in (f.get("summary") or "")
    ]
    assert lr_findings
    f = lr_findings[0]
    assert '<PERSON_B>' in f["quote_a"]
    assert '<PERSON_B>' in f["quote_b"]
    # 分侧取值：即使值相同，也必须分别来自 lr_a / lr_b 字段，而不是共享单一变量后无法区分来源
    assert f["quote_a"].startswith("法定代表人：")
    assert f["quote_b"].startswith("法定代表人：")


def test_dimension_signal_excludes_unverified_and_collision_quotes() -> None:
    compare_result = {
        "pair_id": "1:2",
        "pair_ref": {"doc_a_name": "心悦.pdf", "doc_b_name": "皇和.pdf"},
        "d2_company": {
            "score": 0.95,
            "certainty": 0.95,
            "findings": [{
                "summary": "两家投标企业共用同一法定代表人及注册地址",
                "detail": SHARED_LICENSE_LINE,
                "quote_a": SHARED_LICENSE_LINE,
                "page_a": "第30页",
                "quote_b": SHARED_LICENSE_LINE,
                "page_b": "第106页",
                "verified": False,
                "_quote_collision": True,
            }],
        },
    }
    signals = synthesize_dimension_signal_findings(compare_result, [], 1, 2)
    assert signals == []
