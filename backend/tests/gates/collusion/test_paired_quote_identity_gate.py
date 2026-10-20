""": 成对身份引文必须落在本侧文档主体页，禁止把一份执照混成两家证据。

问题形态: 两侧正式证据未独立锚定。
夹具1 D2: 相同法人/地址引文贴到两份文件的不同页，对侧页没有本公司名。
夹具2 D1: 相同人员姓名引文贴到两份文件，对侧页没有该人员所属公司。
"""

from __future__ import annotations

from app.core.ai.gates.base import GateContext
from app.core.ai.gates.collusion.finding_page_quote_verify import (
    FindingPageQuoteVerifyGate,
)
from app.core.ai.gates.collusion.paired_quote_identity import (
    PairedQuoteIdentityGate,
)

SHARED_LICENSE = (
    '法定代表人:<PERSON_D> 住所地:山西省大同市平城区魏都大道1868号'
    + "御东世纪花园3号楼1单元17层1701室"
)
SHARED_PERSON = "项目经理<PERSON_J> 证书编号 ABC-2020-001"


def _ctx(target: dict, evidence: dict) -> GateContext:
    return GateContext(
        flow="collusion",
        stage="post_llm",
        target=target,
        evidence=evidence,
    )


def _evidence(
    *,
    chunks_a: list[tuple[int, str]],
    chunks_b: list[tuple[int, str]],
    info_a: dict,
    info_b: dict,
    doc_a_name: str = "心悦.pdf",
    doc_b_name: str = "皇和.pdf",
) -> dict:
    page_sets = {
        1: {p for p, _ in chunks_a},
        2: {p for p, _ in chunks_b},
    }
    page_to_contents: dict[int, dict[int, list[str]]] = {1: {}, 2: {}}
    for pn, content in chunks_a:
        page_to_contents[1].setdefault(pn, []).append(content)
    for pn, content in chunks_b:
        page_to_contents[2].setdefault(pn, []).append(content)
    return {
        "page_sets": page_sets,
        "chunk_texts": {1: chunks_a, 2: chunks_b},
        "page_to_contents": page_to_contents,
        "doc_a_id": 1,
        "doc_b_id": 2,
        "doc_a_name": doc_a_name,
        "doc_b_name": doc_b_name,
        "info_a": info_a,
        "info_b": info_b,
    }


class TestPairedQuoteIdentityGateD2:
    gate = PairedQuoteIdentityGate()

    def test_cloned_license_quote_without_company_b_rejected(self) -> None:
        target = {"d2_company": {
            "score": 0.95,
            "certainty": 0.95,
            "findings": [{
                "summary": '法定代表人相同：<PERSON_D>',
                "detail": "两家公司法定代表人和注册地址完全一致",
                "quote_a": SHARED_LICENSE,
                "page_a": "第30页",
                "quote_b": SHARED_LICENSE,
                "page_b": "第106页",
            }],
        }}
        ev = _evidence(
            chunks_a=[(30, f"<ORG_12> {SHARED_LICENSE}")],
            chunks_b=[(106, f"{SHARED_LICENSE} （招标文件示例）")],
            info_a={"company_info": {
                "company_name": "<ORG_12>",
                "legal_representative": '<PERSON_D>',
            }},
            info_b={"company_info": {
                "company_name": "<ORG_13>",
                "legal_representative": '<PERSON_D>',
            }},
        )
        ctx = _ctx(target, ev)
        assert self.gate.applies(ctx) is True
        verdict = self.gate.evaluate(ctx)
        assert verdict.verdict == "enforce_patch"
        finding = target["d2_company"]["findings"][0]
        assert finding["verified"] is False
        assert finding.get("_quote_collision") is True
        assert target["d2_company"]["score"] == 0.0
        assert target["d2_company"]["gate_rejected"] is True

    def test_each_side_page_has_own_company_and_same_rep_passes(self) -> None:
        target = {"d2_company": {
            "score": 0.95,
            "certainty": 0.95,
            "findings": [{
                "summary": '法定代表人相同：<PERSON_B>',
                "quote_a": '法定代表人：<PERSON_B>',
                "page_a": "第3页",
                "quote_b": '法定代表人：<PERSON_B>',
                "page_b": "第9页",
            }],
        }}
        ev = _evidence(
            chunks_a=[(3, '<ORG_23> 法定代表人：<PERSON_B> 住所地：朝阳区某街1号')],
            chunks_b=[(9, '<ORG_3> 法定代表人：<PERSON_B> 住所地：朝阳区某街1号')],
            info_a={"company_info": {
                "company_name": "<ORG_23>",
                "legal_representative": '<PERSON_B>',
            }},
            info_b={"company_info": {
                "company_name": "<ORG_3>",
                "legal_representative": '<PERSON_B>',
            }},
            doc_a_name="甲.pdf",
            doc_b_name="乙.pdf",
        )
        verdict = self.gate.evaluate(_ctx(target, ev))
        assert verdict.verdict == "pass"
        assert target["d2_company"]["findings"][0].get("verified") is not False
        assert target["d2_company"]["score"] == 0.95


class TestPairedQuoteIdentityGateD1:
    gate = PairedQuoteIdentityGate()

    def test_cloned_personnel_quote_without_company_b_rejected(self) -> None:
        """同形态第二例: 人员姓名引文被复用到对侧文档。"""
        target = {"d1_personnel": {
            "score": 0.9,
            "certainty": 0.9,
            "findings": [{
                "summary": "关键人员重叠：<PERSON_J>",
                "quote_a": SHARED_PERSON,
                "page_a": "第12页",
                "quote_b": SHARED_PERSON,
                "page_b": "第40页",
            }],
        }}
        ev = _evidence(
            chunks_a=[(12, f"<ORG_23> {SHARED_PERSON}")],
            chunks_b=[(40, f"{SHARED_PERSON} 投标文件格式")],
            info_a={
                "company_info": {"company_name": "<ORG_23>"},
                "personnel_list": [{"name": "<PERSON_J>", "role": "项目经理"}],
            },
            info_b={
                "company_info": {"company_name": "<ORG_3>"},
                "personnel_list": [{"name": "<PERSON_J>", "role": "项目经理"}],
            },
            doc_a_name="甲.pdf",
            doc_b_name="乙.pdf",
        )
        verdict = self.gate.evaluate(_ctx(target, ev))
        assert verdict.verdict == "enforce_patch"
        assert target["d1_personnel"]["findings"][0]["verified"] is False
        assert target["d1_personnel"]["score"] == 0.0


class TestFindingPageQuoteVerifyRetractsScore:
    gate = FindingPageQuoteVerifyGate()

    def test_all_findings_unverified_zeroes_identity_score(self) -> None:
        target = {"d2_company": {
            "score": 0.95,
            "certainty": 0.95,
            "findings": [{
                "summary": "法定代表人相同",
                "quote_a": SHARED_LICENSE,
                "page_a": "第30页",
                "quote_b": SHARED_LICENSE,
                "page_b": "第106页",
            }],
        }}
        ev = _evidence(
            chunks_a=[(1, "无关")],
            chunks_b=[(2, "无关")],
            info_a={"company_info": {"company_name": "<ORG_23>"}},
            info_b={"company_info": {"company_name": "<ORG_3>"}},
        )
        self.gate.evaluate(_ctx(target, ev))
        assert target["d2_company"]["findings"][0]["verified"] is False
        assert target["d2_company"]["score"] == 0.0
        assert target["d2_company"]["gate_rejected"] is True

    def test_d2_does_not_fuzzy_match_similar_form_on_other_doc(self) -> None:
        target = {"d2_company": {
            "score": 0.95,
            "certainty": 0.95,
            "findings": [{
                "summary": '法定代表人相同：<PERSON_D>',
                "quote_a": SHARED_LICENSE,
                "page_a": "第30页",
                "quote_b": SHARED_LICENSE,
                "page_b": "第106页",
            }],
        }}
        other_form = (
            '法定代表人:<PERSON_E> 住所地:山西省太原市小店区长风街1号'
            + "某写字楼3号楼1单元17层1701室"
        )
        ev = _evidence(
            chunks_a=[(30, f"<ORG_12> {SHARED_LICENSE}")],
            chunks_b=[(106, f"<ORG_13> {other_form}")],
            info_a={"company_info": {"company_name": "<ORG_12>"}},
            info_b={"company_info": {"company_name": "<ORG_13>"}},
        )
        self.gate.evaluate(_ctx(target, ev))
        finding = target["d2_company"]["findings"][0]
        assert finding.get("quote_b_verified") is False
        assert finding.get("verified") is False
