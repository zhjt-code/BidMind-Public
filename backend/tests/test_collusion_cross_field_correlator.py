"""跨字段串联引擎单元测试

覆盖 9 条规则的真阳性 / 假阳性 / 误报抑制场景。
"""

from app.core.collusion.cross_field_correlator import (
    CrossFieldCorrelator,
)
from app.core.collusion.identifier_index import build_index


def _make_extraction(
    *,
    company_name: str = "",
    legal_rep: str = "",
    contact_phone: str = "",
    bank: str = "",
    credit: str = "",
    personnel: list | None = None,
    subcontractors: list | None = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "company_info": {
            "company_name": company_name,
            "legal_representative": legal_rep,
            "contact_phone": contact_phone,
            "bank_account": bank,
            "unified_social_credit_code": credit,
        },
        "personnel_list": personnel or [],
        "price_info": {},
        "qualifications": [],
        "past_projects": [],
        "subcontractors": subcontractors or [],
        "metadata_info": metadata or {},
    }


def _doc_names(ids: list[int]) -> dict[int, str]:
    return {did: f"投标{did}.pdf" for did in ids}


# ── 真阳性 ─────────────────────────────────────────────────────────────────

def test_author_eq_other_legal_rep_first_level() -> None:
    """问题三典型场景：A的PDF作者 = B的法定代表人"""
    ext_a = _make_extraction(
        company_name="<ORG_23>",
        legal_rep="<PERSON_A>",
        metadata={"author": "<PERSON_B>"},
    )
    ext_b = _make_extraction(
        company_name="<ORG_3>",
        legal_rep="<PERSON_B>",
        metadata={"author": "<PERSON_E>"},
    )
    indices = {
        1: build_index(1, "投标1.pdf", ext_a),
        2: build_index(2, "投标2.pdf", ext_b),
    }
    correlator = CrossFieldCorrelator(indices, doc_names=_doc_names([1, 2]))
    hits = correlator.correlate()
    assert any(
        h.risk_pattern == "author_eq_other_legal_rep" and h.level == "first"
        for h in hits
    ), f"应识别 author=他方法人为一级证据，实际命中: {[h.risk_pattern for h in hits]}"


def test_author_eq_other_company_user_problem_scenario() -> None:
    """问题三举例的核心场景：元数据author='123' 与 投标公司名='123'"""
    ext_a = _make_extraction(
        company_name="<ORG_SAMPLE>",
        metadata={"author": "<ORG_23>"},
    )
    ext_b = _make_extraction(
        company_name="<ORG_23>",
        metadata={"author": "其他人"},
    )
    indices = {
        1: build_index(1, "A.pdf", ext_a),
        2: build_index(2, "B.pdf", ext_b),
    }
    correlator = CrossFieldCorrelator(indices, doc_names=_doc_names([1, 2]))
    hits = correlator.correlate()
    assert any(
        h.risk_pattern == "author_eq_other_company"
        for h in hits
    )


def test_author_eq_other_personnel() -> None:
    ext_a = _make_extraction(
        company_name="<ORG_A>",
        metadata={"author": "<PERSON_D>"},
    )
    ext_b = _make_extraction(
        company_name="<ORG_B>",
        personnel=[{"name": "<PERSON_D>", "role": "项目经理"}],
    )
    indices = {
        1: build_index(1, "A.pdf", ext_a),
        2: build_index(2, "B.pdf", ext_b),
    }
    hits = CrossFieldCorrelator(indices, doc_names=_doc_names([1, 2])).correlate()
    assert any(h.risk_pattern == "author_eq_other_person" for h in hits)


def test_legal_rep_eq_other_personnel_law40_6() -> None:
    """法规第40条第6项：法人=他方项目人员"""
    ext_a = _make_extraction(
        company_name="<ORG_A>",
        legal_rep="<PERSON_C>",
    )
    ext_b = _make_extraction(
        company_name="<ORG_B>",
        personnel=[{"name": "<PERSON_C>", "role": "技术负责人"}],
    )
    indices = {
        1: build_index(1, "A.pdf", ext_a),
        2: build_index(2, "B.pdf", ext_b),
    }
    hits = CrossFieldCorrelator(indices, doc_names=_doc_names([1, 2])).correlate()
    rule_hits = [h for h in hits if h.risk_pattern == "legal_rep_eq_other_personnel"]
    assert rule_hits, "法规第40条第6项情形未识别"
    assert rule_hits[0].level == "first"
    assert "第40条" in (rule_hits[0].legal_reference or "")


def test_subcontractor_is_other_bidder() -> None:
    ext_a = _make_extraction(
        company_name="<ORG_A>",
        subcontractors=["<ORG_3>"],
    )
    ext_b = _make_extraction(
        company_name="<ORG_3>",
    )
    indices = {
        1: build_index(1, "A.pdf", ext_a),
        2: build_index(2, "B.pdf", ext_b),
    }
    hits = CrossFieldCorrelator(indices, doc_names=_doc_names([1, 2])).correlate()
    assert any(h.risk_pattern == "subcontractor_is_other_bidder" for h in hits)


# ── 假阳性抑制 ─────────────────────────────────────────────────────────────

def test_stopword_person_should_be_suppressed() -> None:
    '常见停用名占位符不应触发命中'
    ext_a = _make_extraction(
        legal_rep='<PERSON_1>',
        metadata={"author": '<PERSON_1>'},
    )
    ext_b = _make_extraction(
        legal_rep='<PERSON_2>',
        metadata={"author": '<PERSON_1>'},
    )
    indices = {
        1: build_index(1, "A.pdf", ext_a),
        2: build_index(2, "B.pdf", ext_b),
    }
    hits = CrossFieldCorrelator(indices, doc_names=_doc_names([1, 2])).correlate()
    # author_eq_other_legal_rep 不应命中（"<PERSON_1>"是停用名）
    assert not any(
        h.risk_pattern == "author_eq_other_legal_rep" for h in hits
    )


def test_short_string_should_be_suppressed() -> None:
    """短串（< 4 字符的公司名）不应触发命中"""
    ext_a = _make_extraction(
        company_name="<ORG_A>",
        metadata={"author": "ABC"},
    )
    ext_b = _make_extraction(
        company_name="<ORG_C>",
    )
    indices = {
        1: build_index(1, "A.pdf", ext_a),
        2: build_index(2, "B.pdf", ext_b),
    }
    hits = CrossFieldCorrelator(indices, doc_names=_doc_names([1, 2])).correlate()
    assert not any(h.risk_pattern == "author_eq_other_company" for h in hits)


def test_same_doc_no_self_match() -> None:
    """同一文档内的字段一致不应触发命中"""
    ext = _make_extraction(
        company_name="<ORG_23>",
        legal_rep="<PERSON_C>",
        metadata={"author": "<PERSON_C>"},
    )
    indices = {
        1: build_index(1, "A.pdf", ext),
    }
    hits = CrossFieldCorrelator(indices, doc_names=_doc_names([1])).correlate()
    assert hits == []


def test_no_extraction_data() -> None:
    """空数据不应崩溃"""
    indices: dict = {
        1: build_index(1, "A.pdf", {}),
        2: build_index(2, "B.pdf", {}),
    }
    hits = CrossFieldCorrelator(indices, doc_names=_doc_names([1, 2])).correlate()
    assert hits == []


# ── 输出契约 ──────────────────────────────────────────────────────────────

def test_to_findings_format() -> None:
    ext_a = _make_extraction(metadata={"author": "<PERSON_C>"}, company_name="<ORG_A>")
    ext_b = _make_extraction(legal_rep="<PERSON_C>", company_name="<ORG_B>")
    indices = {
        1: build_index(1, "A.pdf", ext_a),
        2: build_index(2, "B.pdf", ext_b),
    }
    correlator = CrossFieldCorrelator(indices, doc_names=_doc_names([1, 2]))
    hits = correlator.correlate()
    findings = correlator.to_findings(hits)
    assert findings
    f = findings[0]
    assert f["type"] == "cross_field_correlation"
    assert "involved_doc_ids" in f
    assert "risk_pattern" in f
    assert "legal_reference" in f


def test_to_pair_findings_format() -> None:
    ext_a = _make_extraction(metadata={"author": "<PERSON_C>"}, company_name="<ORG_A>")
    ext_b = _make_extraction(legal_rep="<PERSON_C>", company_name="<ORG_B>")
    indices = {
        1: build_index(1, "A.pdf", ext_a),
        2: build_index(2, "B.pdf", ext_b),
    }
    correlator = CrossFieldCorrelator(indices, doc_names=_doc_names([1, 2]))
    hits = correlator.correlate()
    pair_findings = CrossFieldCorrelator.to_pair_findings(hits, 1, 2)
    assert pair_findings
    f = pair_findings[0]
    assert f["dimension"]
    assert f["risk_pattern"]
    assert f["_cross_field"] is True
    assert f["evidences"]
