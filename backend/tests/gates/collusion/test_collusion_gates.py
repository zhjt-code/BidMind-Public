from tests.synthetic_data import synthetic_text

"""串标侧 5 道 Gate 单元测试（B3.3）。

每 Gate ≥ 6 条：主路径 / 边界 / applies=False / patch 结果。
"""



from app.core.ai.gates.base import GateContext
from app.core.ai.gates.collusion.d1_personnel_claim import D1PersonnelClaimGate
from app.core.ai.gates.collusion.d2_company_claim import D2CompanyClaimGate
from app.core.ai.gates.collusion.d5_business_relation import D5BusinessRelationGate
from app.core.ai.gates.collusion.d5_qual_type_exemption import D5QualTypeExemptionGate
from app.core.ai.gates.collusion.finding_page_quote_verify import FindingPageQuoteVerifyGate


def _ctx(target, info_a=None, info_b=None, **extra_evidence):
    ev = {"info_a": info_a or {}, "info_b": info_b or {}}
    ev.update(extra_evidence)
    return GateContext(flow="collusion", stage="post_llm", target=target, evidence=ev)


# ============================================================================
# D1PersonnelClaimGate
# ============================================================================

class TestD1PersonnelClaimGate:
    gate = D1PersonnelClaimGate()

    def test_not_applicable_no_d1(self):
        ctx = _ctx({"d2_company": {"score": 1}})
        assert self.gate.applies(ctx) is False

    def test_not_applicable_d1_zero_score(self):
        ctx = _ctx({"d1_personnel": {"score": 0}})
        assert self.gate.applies(ctx) is False

    def test_shared_names_pass(self):
        target = {"d1_personnel": {
            "score": 0.8,
            "findings": [{"summary": '人员重叠: <PERSON_A>', "detail": ""}],
        }}
        info_a = {"personnel_list": [{"name": '<PERSON_A>'}]}
        info_b = {"personnel_list": [{"name": '<PERSON_A>'}]}
        v = self.gate.evaluate(_ctx(target, info_a, info_b))
        assert v.verdict == "pass"

    def test_no_shared_names_reject(self):
        target = {"d1_personnel": {
            "score": 0.8,
            "findings": [{"summary": '人员重叠: <PERSON_A>', "detail": ""}],
        }}
        info_a = {"personnel_list": [{"name": '<PERSON_A>'}]}
        info_b = {"personnel_list": [{"name": '<PERSON_B>'}]}
        v = self.gate.evaluate(_ctx(target, info_a, info_b))
        assert v.verdict == "enforce_patch"
        assert target["d1_personnel"]["gate_rejected"] is True
        assert target["d1_personnel"]["score"] == 0.0

    def test_multiple_findings_partial(self):
        target = {"d1_personnel": {
            "score": 0.8,
            "findings": [
                {"summary": '人员重叠: <PERSON_A>', "detail": ""},
                {"summary": "文档质量好", "detail": ""},
            ],
        }}
        info_a = {"personnel_list": [{"name": '<PERSON_B>'}]}
        info_b = {"personnel_list": [{"name": "<PERSON_3>"}]}
        v = self.gate.evaluate(_ctx(target, info_a, info_b))
        assert v.verdict == "enforce_patch"
        assert target["d1_personnel"]["findings"][0]["verified"] is False
        assert target["d1_personnel"]["findings"][1]["verified"] is True

    def test_empty_personnel_lists(self):
        target = {"d1_personnel": {
            "score": 0.5,
            "findings": [{"summary": "人员重叠", "detail": ""}],
        }}
        v = self.gate.evaluate(_ctx(target, {}, {}))
        assert v.verdict == "enforce_patch"

    def test_wrong_flow(self):
        ctx = GateContext(flow="investigate", stage="post_llm",
                          target={"d1_personnel": {"score": 1}})
        assert self.gate.applies(ctx) is False


# ============================================================================
# D2CompanyClaimGate
# ============================================================================

class TestD2CompanyClaimGate:
    gate = D2CompanyClaimGate()

    def test_not_applicable_no_d2(self):
        assert self.gate.applies(_ctx({})) is False

    def test_legal_rep_one_side_missing_reject(self):
        target = {"d2_company": {
            "score": 0.7,
            "findings": [{"summary": "法定代表人相同", "detail": ""}],
        }}
        info_a = {"company_info": {"legal_representative": '<PERSON_A>'}}
        info_b = {"company_info": {}}
        v = self.gate.evaluate(_ctx(target, info_a, info_b))
        assert v.verdict == "enforce_patch"
        assert target["d2_company"]["findings"][0]["verified"] is False
        assert target["d2_company"]["score"] == 0.0

    def test_phone_one_side_missing_reject(self):
        """同形态第二例: 成对声称缺少对侧结构化字段。"""
        target = {"d2_company": {
            "score": 0.7,
            "findings": [{"summary": "联系电话完全相同", "detail": ""}],
        }}
        info_a = {"company_info": {"contact_phone": synthetic_text('138', '0000', '1111')}}
        info_b = {"company_info": {}}
        v = self.gate.evaluate(_ctx(target, info_a, info_b))
        assert v.verdict == "enforce_patch"
        assert target["d2_company"]["findings"][0]["verified"] is False

    def test_legal_rep_same_pass(self):
        target = {"d2_company": {
            "score": 0.7,
            "findings": [{"summary": "法定代表人相同", "detail": ""}],
        }}
        info_a = {"company_info": {"legal_representative": '<PERSON_A>'}}
        info_b = {"company_info": {"legal_representative": '<PERSON_A>'}}
        v = self.gate.evaluate(_ctx(target, info_a, info_b))
        assert v.verdict == "pass"

    def test_legal_rep_different_reject(self):
        target = {"d2_company": {
            "score": 0.7,
            "findings": [{"summary": "法定代表人相同", "detail": ""}],
        }}
        info_a = {"company_info": {"legal_representative": '<PERSON_A>'}}
        info_b = {"company_info": {"legal_representative": '<PERSON_B>'}}
        v = self.gate.evaluate(_ctx(target, info_a, info_b))
        assert v.verdict == "enforce_patch"
        assert target["d2_company"]["findings"][0]["verified"] is False

    def test_address_different_reject(self):
        target = {"d2_company": {
            "score": 0.6,
            "findings": [{"summary": "注册地址相同", "detail": ""}],
        }}
        info_a = {"company_info": {"registered_address": "北京市朝阳区"}}
        info_b = {"company_info": {"registered_address": "上海市浦东新区"}}
        v = self.gate.evaluate(_ctx(target, info_a, info_b))
        assert v.verdict == "enforce_patch"

    def test_all_rejected_score_zero(self):
        target = {"d2_company": {
            "score": 0.7,
            "findings": [{"summary": "法定代表人相同", "detail": ""}],
        }}
        info_a = {"company_info": {"legal_representative": '<PERSON_A>'}}
        info_b = {"company_info": {"legal_representative": '<PERSON_B>'}}
        self.gate.evaluate(_ctx(target, info_a, info_b))
        assert target["d2_company"]["score"] == 0.0
        assert target["d2_company"]["gate_rejected"] is True

    def test_no_company_info_pass(self):
        target = {"d2_company": {"score": 0.7, "findings": []}}
        v = self.gate.evaluate(_ctx(target, {}, {}))
        assert v.verdict == "pass"


# ============================================================================
# D5BusinessRelationGate
# ============================================================================

class TestD5BusinessRelationGate:
    gate = D5BusinessRelationGate()

    def test_not_applicable_no_d5(self):
        assert self.gate.applies(_ctx({})) is False

    def test_empty_quals_score_zero(self):
        target = {"d5_qualification": {
            "score": 0.5,
            "findings": [{"summary": "test", "detail": ""}],
        }}
        info_a = {"qualifications": []}
        info_b = {"qualifications": []}
        v = self.gate.evaluate(_ctx(target, info_a, info_b))
        assert target["d5_qualification"]["score"] == 0.0
        assert v.verdict == "enforce_patch"

    def test_client_relationship_detected(self):
        target = {"d5_qualification": {
            "score": 0.6,
            "findings": [{"summary": "A公司作为甲方委托B公司", "detail": ""}],
        }}
        info_a = {"company_info": {"company_name": "A公司"}, "qualifications": ["ISO9001"]}
        info_b = {"company_info": {"company_name": "B公司"}, "qualifications": ["ISO9001"]}
        v = self.gate.evaluate(_ctx(target, info_a, info_b))
        assert v.verdict == "enforce_patch"
        f = target["d5_qualification"]["findings"][0]
        assert f["verified"] is False
        assert f["business_relationship"] is True

    def test_no_relationship_pass(self):
        target = {"d5_qualification": {
            "score": 0.6,
            "findings": [{"summary": "资质相同", "detail": "两家都有建筑资质"}],
        }}
        info_a = {"company_info": {"company_name": "X公司"}, "qualifications": ["ISO"]}
        info_b = {"company_info": {"company_name": "Y公司"}, "qualifications": ["ISO"]}
        v = self.gate.evaluate(_ctx(target, info_a, info_b))
        assert v.verdict == "pass"

    def test_past_project_relationship(self):
        target = {"d5_qualification": {
            "score": 0.5,
            "findings": [{"summary": "B公司业主委托", "detail": ""}],
        }}
        info_a = {"company_info": {"company_name": "A公司"},
                   "qualifications": ["x"], "past_projects": ["B公司某项目"]}
        info_b = {"company_info": {"company_name": "B公司"},
                   "qualifications": ["x"], "past_projects": []}
        v = self.gate.evaluate(_ctx(target, info_a, info_b))
        assert v.verdict == "enforce_patch"

    def test_missing_info_not_applicable(self):
        target = {"d5_qualification": {"score": 0.5}}
        ctx = GateContext(flow="collusion", stage="post_llm", target=target,
                          evidence={"info_a": None, "info_b": None})
        assert self.gate.applies(ctx) is False


# ============================================================================
# D5QualTypeExemptionGate
# ============================================================================

class TestD5QualTypeExemptionGate:
    gate = D5QualTypeExemptionGate()

    def test_not_applicable_gate_already_rejected(self):
        target = {"d5_qualification": {"score": 0, "gate_rejected": True}}
        assert self.gate.applies(_ctx(target)) is False

    def test_same_type_different_id_rejected(self):
        target = {"d5_qualification": {
            "score": 0.5,
            "findings": [{"summary": "相同资质证书编号不同", "detail": ""}],
        }}
        v = self.gate.evaluate(_ctx(target))
        assert v.verdict == "enforce_patch"
        f = target["d5_qualification"]["findings"][0]
        assert f["verified"] is False
        assert f["certainty"] <= 0.15

    def test_unrelated_finding_pass(self):
        target = {"d5_qualification": {
            "score": 0.5,
            "findings": [{"summary": "业绩项目重复", "detail": ""}],
        }}
        v = self.gate.evaluate(_ctx(target))
        assert v.verdict == "pass"

    def test_already_rejected_finding_skipped(self):
        target = {"d5_qualification": {
            "score": 0.5,
            "findings": [{"summary": "相同资质", "detail": "", "verified": False}],
        }}
        v = self.gate.evaluate(_ctx(target))
        assert v.verdict == "pass"

    def test_all_rejected_score_zero(self):
        target = {"d5_qualification": {
            "score": 0.5,
            "findings": [
                {"summary": "同类资质证书", "detail": "编号不同"},
            ],
        }}
        self.gate.evaluate(_ctx(target))
        assert target["d5_qualification"]["score"] == 0.0
        assert target["d5_qualification"]["gate_rejected"] is True

    def test_partial_rejection(self):
        target = {"d5_qualification": {
            "score": 0.5,
            "findings": [
                {"summary": "同类资质", "detail": ""},
                {"summary": "项目经验相似", "detail": ""},
            ],
        }}
        self.gate.evaluate(_ctx(target))
        assert target["d5_qualification"]["score"] == 0.5  # not zeroed


# ============================================================================
# FindingPageQuoteVerifyGate
# ============================================================================

class TestFindingPageQuoteVerifyGate:
    gate = FindingPageQuoteVerifyGate()

    def _make_evidence(self, page_sets=None, chunk_texts=None):
        return {
            "page_sets": page_sets or {},
            "chunk_texts": chunk_texts or {},
            "doc_a_id": 1,
            "doc_b_id": 2,
            "info_a": {},
            "info_b": {},
        }

    def test_not_applicable_without_evidence(self):
        ctx = _ctx({})
        assert self.gate.applies(ctx) is False

    def test_page_verified(self):
        target = {"d1_personnel": {
            "score": 0.5,
            "findings": [{"summary": "test", "page_a": "第5页", "page_b": "10"}],
        }}
        ev = self._make_evidence(
            page_sets={1: {5, 6}, 2: {10, 11}},
        )
        ctx = GateContext(flow="collusion", stage="post_llm", target=target, evidence=ev)
        self.gate.evaluate(ctx)
        f = target["d1_personnel"]["findings"][0]
        assert f["page_a_verified"] is True
        assert f["page_b_verified"] is True

    def test_page_and_quote_not_found_reject(self):
        target = {"d1_personnel": {
            "score": 0.5,
            "findings": [{
                "summary": "test",
                "page_a": "第99页", "page_b": "88",
                "quote_a": "这是一段完全不匹配的引文内容ABC",
                "quote_b": "这是另一段完全不匹配的引文内容XYZ",
            }],
        }}
        ev = self._make_evidence(
            page_sets={1: {1, 2}, 2: {1, 2}},
            chunk_texts={1: [(1, "无关内容")], 2: [(1, "无关内容")]},
        )
        ctx = GateContext(flow="collusion", stage="post_llm", target=target, evidence=ev)
        self.gate.evaluate(ctx)
        f = target["d1_personnel"]["findings"][0]
        assert f["page_a_verified"] is False
        assert f["page_b_verified"] is False
        assert f.get("verified") is False

    def test_covers_d4_price_findings(self):
        """S.2.7 回归：D4 维度 key 统一为 d4_price 后 Gate 必须覆盖。

        历史缺陷（A6）：Gate 内白名单错写 d4_pricing，导致 D4 findings 页码回查
        从未触发。修正后 Gate 必须对 d4_price 正常执行 page_verified 标记。
        """
        target = {"d4_price": {
            "score": 0.7,
            "findings": [{
                "summary": "报价呈固定比例",
                "page_a": "第5页", "page_b": "10",
            }],
        }}
        ev = self._make_evidence(
            page_sets={1: {5, 6}, 2: {10, 11}},
        )
        ctx = GateContext(flow="collusion", stage="post_llm", target=target, evidence=ev)
        self.gate.evaluate(ctx)
        f = target["d4_price"]["findings"][0]
        assert f["page_a_verified"] is True
        assert f["page_b_verified"] is True

    def test_quote_verified_by_similarity(self):
        target = {"d3_text": {
            "score": 0.8,
            "findings": [{
                "summary": "test",
                "quote_a": "这是一段完全相同的文本内容",
                "quote_b": "这是一段完全相同的文本内容",
            }],
        }}
        ev = self._make_evidence(
            chunk_texts={
                1: [(1, "这是一段完全相同的文本内容")],
                2: [(1, "这是一段完全相同的文本内容")],
            },
        )
        ctx = GateContext(flow="collusion", stage="post_llm", target=target, evidence=ev)
        self.gate.evaluate(ctx)
        f = target["d3_text"]["findings"][0]
        assert f.get("quote_a_verified") is True

    def test_already_rejected_not_overwritten(self):
        target = {"d1_personnel": {
            "score": 0.5,
            "findings": [{
                "summary": "test",
                "page_a": "第99页", "page_b": "88",
                "verified": False, "rejection_reason": "prior rejection",
            }],
        }}
        ev = self._make_evidence(page_sets={1: {1}, 2: {1}})
        ctx = GateContext(flow="collusion", stage="post_llm", target=target, evidence=ev)
        self.gate.evaluate(ctx)
        f = target["d1_personnel"]["findings"][0]
        assert f["rejection_reason"] == "prior rejection"

    def test_partial_verification_rejects_under_b1_contract(self):
        """B1 重写后契约：单边失败一律 verified=False（不再 warn 放行）。

 历史 a/363 复发的根因之一是单边失败仅 warn 放行，导致
 LLM 给出错误页码也能展示为正式证据。B1 重写后此处期望被修正。
 """
        target = {"d1_personnel": {
            "score": 0.5,
            "findings": [{"summary": "test", "page_a": "第5页", "page_b": "99"}],
        }}
        ev = self._make_evidence(page_sets={1: {5}, 2: {1}})
        ctx = GateContext(flow="collusion", stage="post_llm", target=target, evidence=ev)
        self.gate.evaluate(ctx)
        f = target["d1_personnel"]["findings"][0]
        # B1: 一侧 page 不在文档集合 → verified=False，转入 attention_items
        assert f.get("verified") is False
        assert f.get("rejection_reason") is not None
