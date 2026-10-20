"""主体覆盖与共享证据修复 -- 回归测试

覆盖三类同类问题:
1. 信用主体覆盖: 企业 vs 法定代表人 / 单位负责人 / 授权代表
2. 授权链条覆盖: 法定代表人证明 / 授权委托书 / 委托代理人身份
3. 共享材料复用: 纳税证明 / 资信证明 / 审计报告 / 财务报表
"""

import os
import sys
from pathlib import Path

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/bidmind_lite",
)

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai.reviewer import ReviewOrchestrator
from app.core.extractor.tender_extractor import (
    infer_subject_scope,
    infer_topic,
)
from app.core.retriever.query_decomposer import (
    QueryDecomposer,
    _expand_theme_synonyms,
)
from app.models.bid_response import BidResponse
from app.models.requirement import Requirement
from app.models.review_result import ComplianceLevel, ReviewResult, RiskLevel


class _DummyLLM:
    async def chat(self, *args, **kwargs):
        return "{}"


# =====================================================================
# 1. 主体推断 (infer_subject_scope)
# =====================================================================

class TestSubjectScopeInference:

    def test_legal_representative_is_personnel(self):
        assert infer_subject_scope("法定代表人无失信被执行人记录") == "personnel"

    def test_legal_rep_variant_is_personnel(self):
        assert infer_subject_scope("法人代表身份证明") == "personnel"

    def test_unit_head_is_personnel(self):
        assert infer_subject_scope("单位负责人无重大违法记录") == "personnel"

    def test_authorized_representative_is_personnel(self):
        assert infer_subject_scope("授权代表身份证明") == "personnel"

    def test_entrusted_agent_is_personnel(self):
        assert infer_subject_scope("委托代理人授权委托书") == "personnel"

    def test_enterprise_and_legal_rep_is_both(self):
        assert infer_subject_scope("投标人及法定代表人无失信记录") == "both"

    def test_enterprise_and_unit_head_is_both(self):
        assert infer_subject_scope("投标人及单位负责人无重大违法") == "both"

    def test_pure_legal_entity_still_enterprise(self):
        assert infer_subject_scope("投标人应为境内注册独立法人") == "enterprise"

    def test_faren_without_representative_is_enterprise(self):
        """'法人'单独出现(非'法定代表人')仍归企业"""
        assert infer_subject_scope("须为独立法人资格") == "enterprise"

    def test_business_license_is_enterprise(self):
        assert infer_subject_scope("投标人须提供营业执照") == "enterprise"

    def test_project_manager_is_personnel(self):
        assert infer_subject_scope("项目经理须提供社保证明") == "personnel"

    def test_bidder_and_pm_is_both(self):
        assert infer_subject_scope("投标人及项目经理须提供业绩证明") == "both"


# =====================================================================
# 2. 主题推断 (infer_topic) -- 信用/授权归入 qualification
# =====================================================================

class TestTopicInference:

    def test_credit_dishonesty_is_qualification(self):
        assert infer_topic("法定代表人无失信被执行人记录") == "qualification"

    def test_credit_china_is_qualification(self):
        assert infer_topic("信用中国无不良行为") == "qualification"

    def test_major_violation_is_qualification(self):
        assert infer_topic("单位负责人无重大违法记录") == "qualification"

    def test_blacklist_is_qualification(self):
        assert infer_topic("投标人不在黑名单之列") == "qualification"

    def test_authorization_is_qualification(self):
        assert infer_topic("法定代表人授权委托书") == "qualification"

    def test_business_license_is_qualification(self):
        assert infer_topic("营业执照复印件") == "qualification"

    def test_performance_remains_performance(self):
        assert infer_topic("近三年类似项目业绩") == "performance"


# =====================================================================
# 3. 查询扩展 (QueryDecomposer + theme synonyms)
# =====================================================================

class TestQueryDecomposerThemeExpansion:

    def test_credit_expansion_adds_synonyms(self):
        extras = _expand_theme_synonyms("法定代表人无失信被执行人记录")
        assert len(extras) > 0
        combined = " ".join(extras)
        assert any(kw in combined for kw in ["信用中国", "信用记录", "未被列入"])

    def test_blacklist_expansion(self):
        extras = _expand_theme_synonyms("投标人不在黑名单之列")
        assert len(extras) > 0
        combined = " ".join(extras)
        assert any(kw in combined for kw in ["重大违法", "不良行为", "失信被执行人"])

    def test_authorization_expansion(self):
        extras = _expand_theme_synonyms("法定代表人授权委托书")
        assert len(extras) > 0

    def test_no_expansion_for_generic(self):
        extras = _expand_theme_synonyms("投标人须提供技术方案")
        assert len(extras) == 0

    def test_decomposer_includes_theme_expansions(self):
        decomposer = QueryDecomposer()
        queries = decomposer.decompose("法定代表人无失信被执行人记录")
        assert len(queries) > 1

    def test_extract_keywords_includes_synonyms(self):
        decomposer = QueryDecomposer()
        kws = decomposer.extract_keywords("投标人及法定代表人无失信记录")
        assert any("信用" in kw or "失信被执行人" in kw for kw in kws)


# =====================================================================
# 4. 跨条目证据复用 (reviewer _DOMAIN_KEYWORDS coverage)
# =====================================================================

class TestCrossEvidenceKeywords:

    def test_credit_keywords_in_domain_keywords(self):
        reviewer = ReviewOrchestrator(db=None, llm_client=_DummyLLM())  # type: ignore[arg-type]
        for kw in ["失信", "信用", "信用中国", "重大违法", "黑名单", "不良行为"]:
            assert kw in reviewer._DOMAIN_KEYWORDS, f"{kw} missing"

    def test_representative_keywords_in_domain_keywords(self):
        reviewer = ReviewOrchestrator(db=None, llm_client=_DummyLLM())  # type: ignore[arg-type]
        for kw in ["法定代表人", "单位负责人", "授权代表", "委托代理人", "授权委托书"]:
            assert kw in reviewer._DOMAIN_KEYWORDS, f"{kw} missing"

    def test_shared_financial_keywords_in_domain_keywords(self):
        reviewer = ReviewOrchestrator(db=None, llm_client=_DummyLLM())  # type: ignore[arg-type]
        for kw in ["纳税证明", "资信证明", "审计报告", "财务报表"]:
            assert kw in reviewer._DOMAIN_KEYWORDS, f"{kw} missing"


# =====================================================================
# 5. 信用主体事实组收敛 (qualification dual-scope)
# =====================================================================

class TestCreditFactGroupConvergence:

    def test_qualification_dual_scope_promotes_to_shared(self):
        """企业信用 + 法定代表人信用 同事实组无冲突时应收敛为 shared"""
        reviewer = ReviewOrchestrator(db=None, llm_client=_DummyLLM())  # type: ignore[arg-type]
        req_ent = Requirement(id=10, description="投标人无失信记录", subject_scope="enterprise")
        req_per = Requirement(id=11, description="法定代表人无失信记录", subject_scope="personnel")
        rr_ent = ReviewResult(
            id=10, requirement_id=10,
            compliance_level=ComplianceLevel.FULLY, score=95,
            is_compliant=1, comment="", risk_level=RiskLevel.NONE,
            family_type="qualification", fact_key="qualification:credit_test",
        )
        rr_per = ReviewResult(
            id=11, requirement_id=11,
            compliance_level=ComplianceLevel.PARTIALLY, score=60,
            is_compliant=0, comment="", risk_level=RiskLevel.MEDIUM,
            family_type="qualification", fact_key="qualification:credit_test",
        )
        grouped = {"qualification:credit_test": [(rr_ent, req_ent, None), (rr_per, req_per, None)]}
        reviewer._judge_fact_then_project(grouped)  # noqa: SLF001
        assert rr_ent.evidence_relation == "shared"
        assert rr_per.evidence_relation == "shared"

    def test_qualification_dual_scope_conflict_flags_needs_review(self):
        """企业信用 fully + 法定代表人 not -> 冲突 -> needs_review"""
        reviewer = ReviewOrchestrator(db=None, llm_client=_DummyLLM())  # type: ignore[arg-type]
        req_ent = Requirement(id=20, description="投标人无失信记录", subject_scope="enterprise")
        req_per = Requirement(id=21, description="法定代表人无失信记录", subject_scope="personnel")
        rr_ent = ReviewResult(
            id=20, requirement_id=20,
            compliance_level=ComplianceLevel.FULLY, score=95,
            is_compliant=1, comment="", risk_level=RiskLevel.NONE,
            family_type="qualification", fact_key="qualification:credit_conflict",
        )
        rr_per = ReviewResult(
            id=21, requirement_id=21,
            compliance_level=ComplianceLevel.NOT, score=0,
            is_compliant=0, comment="", risk_level=RiskLevel.HIGH,
            family_type="qualification", fact_key="qualification:credit_conflict",
        )
        grouped = {"qualification:credit_conflict": [(rr_ent, req_ent, None), (rr_per, req_per, None)]}
        reviewer._judge_fact_then_project(grouped)  # noqa: SLF001
        assert rr_per.consistency_flag == "needs_review"


# =====================================================================
# 6. 跨组冲突检测 -- 信用类 cross_conflict
# =====================================================================

class TestCreditCrossGroupConflict:

    def test_cross_group_detects_credit_evidence_in_other_group(self):
        """A组声称缺失授权委托书, B组证据包含授权委托书 -> 标记跨组冲突"""
        reviewer = ReviewOrchestrator(db=None, llm_client=_DummyLLM())  # type: ignore[arg-type]
        req_a = Requirement(id=30, description="法定代表人授权委托书", subject_scope="personnel")
        rr_a = ReviewResult(
            id=30, requirement_id=30,
            compliance_level=ComplianceLevel.NOT, score=0,
            is_compliant=0, comment="缺失授权委托书", risk_level=RiskLevel.HIGH,
            family_type="qualification", fact_key="qual:auth_a",
        )
        req_b = Requirement(id=31, description="投标人资格审查", subject_scope="enterprise")
        rr_b = ReviewResult(
            id=31, requirement_id=31,
            compliance_level=ComplianceLevel.FULLY, score=95,
            is_compliant=1, comment="资格审查通过,含授权委托书", risk_level=RiskLevel.NONE,
            family_type="qualification", fact_key="qual:ent_qual",
        )
        br_b = BidResponse(
            content='法定代表人授权委托书,委托代理人<PERSON_A>代理投标事宜',
            evidence_anchor=None, source_pages="5",
        )
        # B 组必须在 A 组之前（现有跨组检测遍历顺序限制）
        grouped = {
            "qual:ent_qual": [(rr_b, req_b, br_b)],
            "qual:auth_a": [(rr_a, req_a, None)],
        }
        # Fix-01: 跨组冲突检测迁移至 CrossGroupConflictGate，
        # 通过 Pipeline 驱动，helper 由 evidence 注入。
        from app.core.ai.gates import build_pipeline_for
        from app.core.ai.gates.base import GateContext
        _ctx_pp = GateContext(
            flow="investigate", stage="post_pipeline",
            target=grouped,
            evidence={
                "extract_missing_keywords": reviewer._extract_missing_keywords,
                "evidence_covers_missing": reviewer._evidence_covers_missing,
                "estimate_keyword_relevance": reviewer._estimate_keyword_relevance,
            },
            config={"project_id": 0},
        )
        build_pipeline_for("investigate").run(_ctx_pp, apply_patch_to=grouped)
        assert rr_a.consistency_flag == "cross_conflict"
        assert "跨类别冲突" in (rr_a.comment or "")


# =====================================================================
# 7. 复合要求拆分 -- QUAL 信用/授权子面
# =====================================================================

class TestCompoundSplitCreditAuthorization:

    def test_qual_credit_authorization_splits(self):
        """同时命中 qual_credit 和 qual_authorization 应拆分"""
        from app.schemas.requirement import ExtractedRequirement

        extractor = object.__new__(type(
            "_FakeExtractor", (),
            {
                "_COMPOUND_SPLIT_RULES": ReviewOrchestrator.__dict__.get(
                    "_COMPOUND_SPLIT_RULES", {}
                ),
            },
        ))
        from app.core.extractor.tender_extractor import TenderExtractor
        extractor = TenderExtractor(db=None, llm_client=_DummyLLM())  # type: ignore[arg-type]

        req = ExtractedRequirement(
            category="QUAL",
            description="法定代表人无失信被执行人记录,并提供法定代表人授权委托书",
            is_mandatory=True,
            keywords=["失信", "法定代表人", "授权委托"],
        )
        result = extractor._split_compound_requirements([req])  # noqa: SLF001
        assert len(result) >= 2
        scopes = {r.subject_scope for r in result}
        assert "both" in scopes or "personnel" in scopes
