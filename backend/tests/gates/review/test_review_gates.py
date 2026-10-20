from tests.synthetic_data import synthetic_text
"""投标侧 4 Gate 单元测试（B3.6）。

PageAnchorVerifyGate / ClaimResponseConsistencyGate /
CrossGroupConflictGate / RelevanceAnomalyBackfillGate
"""

import pytest
from types import SimpleNamespace

from app.core.ai.gates.base import GateContext
from app.core.ai.gates.review.page_anchor_verify import PageAnchorVerifyGate
from app.core.ai.gates.review.claim_response_consistency import ClaimResponseConsistencyGate
from app.core.ai.gates.review.cross_group_conflict import CrossGroupConflictGate
from app.core.ai.gates.review.relevance_anomaly_backfill import RelevanceAnomalyBackfillGate


# ============================================================================
# PageAnchorVerifyGate
# ============================================================================

class TestPageAnchorVerifyGate:
    gate = PageAnchorVerifyGate()

    def _ctx(self, source_pages, chunk_pages):
        return GateContext(
            flow="investigate", stage="post_llm",
            target=SimpleNamespace(),
            evidence={"source_pages": source_pages, "chunk_pages": chunk_pages},
        )

    def test_not_applicable_no_evidence(self):
        ctx = GateContext(flow="investigate", stage="post_llm",
                          target=SimpleNamespace())
        assert self.gate.applies(ctx) is False

    def test_all_verified_pass(self):
        v = self.gate.evaluate(self._ctx([1, 2, 3], {1, 2, 3, 4}))
        assert v.verdict == "pass"

    def test_unverified_pages_patched(self):
        v = self.gate.evaluate(self._ctx([1, 5, 99], {1, 2, 3}))
        assert v.verdict == "enforce_patch"
        assert 5 in v.suggested_patch["source_pages_unverified"]

    def test_no_source_pages(self):
        v = self.gate.evaluate(self._ctx(None, {1, 2}))
        assert v.verdict == "pass"

    def test_no_chunk_pages(self):
        v = self.gate.evaluate(self._ctx([1, 2], set()))
        assert v.verdict == "pass"

    def test_fallback_to_chunk_pages(self):
        v = self.gate.evaluate(self._ctx([99], {5, 10}))
        assert v.verdict == "enforce_patch"
        assert 5 in v.suggested_patch["source_pages_verified"] or 10 in v.suggested_patch["source_pages_verified"]


# ============================================================================
# ClaimResponseConsistencyGate
# ============================================================================

class TestClaimResponseConsistencyGate:
    gate = ClaimResponseConsistencyGate()

    def _ctx(self, compliance_level="not", comment="投标方未提供相关证明",
             has_response=True, response_content="这是一段很长的实质内容回复" * 3):
        target = SimpleNamespace(
            compliance_level=compliance_level,
            comment=comment,
        )
        return GateContext(
            flow="investigate", stage="post_llm",
            target=target,
            evidence={"has_response": has_response, "response_content": response_content},
        )

    def test_not_applicable_fully(self):
        ctx = self._ctx(compliance_level="fully", comment="响应完整，判定符合。")
        assert self.gate.applies(ctx) is False

    def test_applies_when_evidence_pack_present_without_has_response(self):
        ctx = self._ctx(
            has_response=False,
            response_content="[未找到响应]",
        )
        ctx.evidence["evidence_pack_text"] = synthetic_text('营业执照扫描件编号', '911100', '00MA00', '00001X')
        assert self.gate.applies(ctx) is True
        verdict = self.gate.evaluate(ctx)
        assert verdict.verdict == "enforce_patch"
        patch = verdict.suggested_patch or {}
        comment = patch.get("comment") or ""
        assert "未提供" in comment
        assert "待核验是否具备" not in comment
        assert "已检索到相关材料" not in comment
        assert "检索已命中" in comment
        assert patch.get("failure_state") == "needs_review"
        assert "compliance_level" not in patch

    def test_conflict_detected(self):
        v = self.gate.evaluate(self._ctx())
        assert v.verdict == "enforce_patch"
        assert "系统提示" in v.suggested_patch["comment"]

    def test_no_not_provided_claim(self):
        v = self.gate.evaluate(self._ctx(comment="部分符合但不完整"))
        assert v.verdict == "pass"

    def test_short_response_pass(self):
        """: 实质响应下限降到 5 字后，1-4 字碎片仍视为无实质内容放行。"""
        v = self.gate.evaluate(self._ctx(response_content="短"))
        assert v.verdict == "pass"

    def test_no_response_pass(self):
        v = self.gate.evaluate(self._ctx(has_response=False))
        assert v.verdict == "pass"

    def test_patch_includes_enforce_fields(self):
        """: 冲突 patch 只转 needs_review，不得写 compliance_level
 （闸门不替 LLM 提升符合度，判定方向归人工复核）。"""
        v = self.gate.evaluate(self._ctx())
        assert v.suggested_patch["failure_state"] == "needs_review"
        assert "compliance_level" not in v.suggested_patch

    def test_bug995_ambiguity_denial_keyword_hits(self):
        """: "未明确"类否认措辞必须命中词表触发冲突检测。"""
        v = self.gate.evaluate(self._ctx(
            comment="投标文件仅承诺有效期内不撤销投标，未明确具体有效期时长，判定不符合。",
        ))
        assert v.verdict == "enforce_patch"
        assert v.suggested_patch["failure_state"] == "needs_review"

    def test_bug995_short_explicit_fact_triggers(self):
        """: 16 字明确事实响应不得再被 20 字下限放行。"""
        v = self.gate.evaluate(self._ctx(
            comment="未明确具体有效期时长，判定不符合。",
            response_content="投标有效期：开标之日起90日历天",
        ))
        assert v.verdict == "enforce_patch"
        assert "compliance_level" not in v.suggested_patch

    def test_bug1010_not_found_body_kept_overlay_only(self):
        """: 未找到原文保留, 不得句内替换; 检索命中只写后缀。"""
        body = (
            "但均未找到主管单位或相关部门出具的“2025年度业绩考核评价结果”证明文件。"
            "因缺乏直接的年度业绩考核评价材料。"
        )
        v = self.gate.evaluate(self._ctx(
            comment=body,
            response_content="2025年度财务决算专项审计报告及近三年项目业绩验收意见。",
        ))
        comment = (v.suggested_patch or {}).get("comment") or ""
        assert v.verdict == "enforce_patch"
        assert comment.startswith(body.rstrip("。"))
        assert "未找到" in comment
        assert "因缺乏" in comment
        assert "已检索到相关材料" not in comment
        assert "待核验是否具备" not in comment
        assert "检索已命中" in comment
        assert "符合性待复核" in comment
        assert v.suggested_patch["failure_state"] == "needs_review"
        assert "compliance_level" not in v.suggested_patch

    def test_bug1010_missing_object_body_kept_overlay_only(self):
        """ 同形态第二例: 缺少结算发票的原文保留, 只追加复核注记。"""
        body = (
            "投标文件仅提供了合同扫描件，未找到结算发票证明材料，证明材料不完整。"
            "因缺少结算发票导致证明效力不足。"
        )
        v = self.gate.evaluate(self._ctx(
            comment=body,
            response_content="2024年类似项目合同扫描件及项目名称清单。",
        ))
        comment = (v.suggested_patch or {}).get("comment") or ""
        assert v.verdict == "enforce_patch"
        assert comment.startswith(body.rstrip("。"))
        assert "未找到" in comment
        assert "缺少" in comment
        assert "证明材料不完整" in comment
        assert "证明效力不足" in comment
        assert "已检索到相关材料" not in comment
        assert "待核验是否具备" not in comment
        assert "检索已命中" in comment
        assert v.suggested_patch["failure_state"] == "needs_review"
        assert "compliance_level" not in v.suggested_patch

    def test_bug1010_overlay_not_duplicated_on_second_pass(self):
        """原文保留后否认词仍在, 二次评估不得叠两层注记。"""
        first = self.gate.evaluate(self._ctx())
        patched = (first.suggested_patch or {}).get("comment") or ""
        second = self.gate.evaluate(self._ctx(comment=patched))
        comment = (second.suggested_patch or {}).get("comment") or patched
        assert comment.count("检索已命中") == 1
        assert comment.count("系统提示") == 1


# ============================================================================
# CrossGroupConflictGate
# ============================================================================

class TestCrossGroupConflictGate:
    gate = CrossGroupConflictGate()

    def _make_rr(self, compliance_level, comment="", score=50,
                 relevance_score=None, consistency_flag="unchecked",
                 reconcile_note=None, source_pages=None):
        return SimpleNamespace(
            compliance_level=SimpleNamespace(value=compliance_level),
            comment=comment, score=score,
            relevance_score=relevance_score,
            consistency_flag=consistency_flag,
            reconcile_note=reconcile_note,
        )

    def _make_req(self, description="test requirement", category=None):
        return SimpleNamespace(description=description, category=category)

    def _make_br(self, content="response content", source_pages="1,2"):
        return SimpleNamespace(content=content, source_pages=source_pages)

    def test_not_applicable_wrong_stage(self):
        ctx = GateContext(flow="investigate", stage="post_llm", target={})
        assert self.gate.applies(ctx) is False

    def test_conflict_detected(self):
        rr_a = self._make_rr("not", comment="缺少营业执照")
        rr_b = self._make_rr("fully")
        br_b = self._make_br(content="营业执照有效期至2025年")

        grouped = {
            "key_b": [(rr_b, self._make_req("营业执照"), br_b)],
            "key_a": [(rr_a, self._make_req(), None)],
        }

        def _extract(comment):
            return ["营业执照"] if "营业执照" in comment else []

        def _covers(br, rr, kws):
            return any(kw in (br.content or "") for kw in kws)

        ctx = GateContext(
            flow="investigate", stage="post_pipeline",
            target=grouped,
            evidence={
                "extract_missing_keywords": _extract,
                "evidence_covers_missing": _covers,
            },
        )
        v = self.gate.evaluate(ctx)
        assert v.verdict == "enforce_patch"
        assert rr_a.consistency_flag == "cross_conflict"

    def test_no_conflict(self):
        rr_a = self._make_rr("not", comment="缺少营业执照")
        rr_b = self._make_rr("fully")
        br_b = self._make_br(content="ISO认证")

        grouped = {
            "key_a": [(rr_a, self._make_req(), None)],
            "key_b": [(rr_b, self._make_req(), br_b)],
        }

        ctx = GateContext(
            flow="investigate", stage="post_pipeline",
            target=grouped,
            evidence={
                "extract_missing_keywords": lambda c: ["营业执照"] if "营业执照" in c else [],
                "evidence_covers_missing": lambda br, rr, kws: any(kw in br.content for kw in kws),
            },
        )
        v = self.gate.evaluate(ctx)
        assert v.verdict == "pass"

    def test_no_helpers_pass(self):
        ctx = GateContext(flow="investigate", stage="post_pipeline",
                          target={"k": []}, evidence={})
        v = self.gate.evaluate(ctx)
        assert v.verdict == "pass"

    def test_only_fully_no_check(self):
        rr = self._make_rr("fully")
        grouped = {"key": [(rr, self._make_req(), None)]}
        ctx = GateContext(
            flow="investigate", stage="post_pipeline",
            target=grouped,
            evidence={
                "extract_missing_keywords": lambda c: [],
                "evidence_covers_missing": lambda br, rr, kws: False,
            },
        )
        v = self.gate.evaluate(ctx)
        assert v.verdict == "pass"


# ============================================================================
# RelevanceAnomalyBackfillGate
# ============================================================================

class TestRelevanceAnomalyBackfillGate:
    gate = RelevanceAnomalyBackfillGate()

    def _make_rr(self, score=90, relevance_score=None, comment=""):
        return SimpleNamespace(
            score=score, relevance_score=relevance_score,
            comment=comment, relevance_note=None,
            consistency_flag="unchecked", reconcile_note=None,
        )

    def _make_req(self, cat="performance"):
        """Fix-03: category 统一为全称，Gate 白名单不再接受缩写。"""
        return SimpleNamespace(
            description="test requirement",
            category=SimpleNamespace(value=cat),
        )

    def _make_br(self, content="投标内容"):
        return SimpleNamespace(content=content)

    def test_not_applicable_wrong_stage(self):
        ctx = GateContext(flow="investigate", stage="post_llm", target={})
        assert self.gate.applies(ctx) is False

    def test_anomaly_detected(self):
        rr = self._make_rr(score=90)
        br = self._make_br(content="完全不相关的内容")
        grouped = {"key": [(rr, self._make_req(), br)]}
        ctx = GateContext(
            flow="investigate", stage="post_pipeline",
            target=grouped,
            evidence={"estimate_keyword_relevance": lambda d, c: 15},
        )
        v = self.gate.evaluate(ctx)
        assert v.verdict == "enforce_patch"
        assert rr.relevance_score == 15
        assert rr.consistency_flag == "needs_review"

    def test_no_anomaly_high_relevance(self):
        rr = self._make_rr(score=90)
        br = self._make_br()
        grouped = {"key": [(rr, self._make_req(), br)]}
        ctx = GateContext(
            flow="investigate", stage="post_pipeline",
            target=grouped,
            evidence={"estimate_keyword_relevance": lambda d, c: 80},
        )
        v = self.gate.evaluate(ctx)
        assert v.verdict == "pass"

    def test_skip_low_score(self):
        rr = self._make_rr(score=50)
        br = self._make_br()
        grouped = {"key": [(rr, self._make_req(), br)]}
        ctx = GateContext(
            flow="investigate", stage="post_pipeline",
            target=grouped,
            evidence={"estimate_keyword_relevance": lambda d, c: 10},
        )
        v = self.gate.evaluate(ctx)
        assert v.verdict == "pass"

    def test_skip_has_relevance_score(self):
        rr = self._make_rr(score=90, relevance_score=80)
        br = self._make_br()
        grouped = {"key": [(rr, self._make_req(), br)]}
        ctx = GateContext(
            flow="investigate", stage="post_pipeline",
            target=grouped,
            evidence={"estimate_keyword_relevance": lambda d, c: 10},
        )
        v = self.gate.evaluate(ctx)
        assert v.verdict == "pass"

    def test_skip_wrong_category(self):
        rr = self._make_rr(score=90)
        br = self._make_br()
        grouped = {"key": [(rr, self._make_req("OTHER"), br)]}
        ctx = GateContext(
            flow="investigate", stage="post_pipeline",
            target=grouped,
            evidence={"estimate_keyword_relevance": lambda d, c: 10},
        )
        v = self.gate.evaluate(ctx)
        assert v.verdict == "pass"

    def test_no_estimator_pass(self):
        ctx = GateContext(flow="investigate", stage="post_pipeline",
                          target={"k": []}, evidence={})
        v = self.gate.evaluate(ctx)
        assert v.verdict == "pass"
