"""Fix-02 / 问题 2 单元测试：bid_response_chunks 持久化 + PageAnchorVerifyGate 新能力。

测试覆盖点：
1. ``BidExtractor._create_response`` 正确把 SearchResult 多路命中挂载为
   ``response._pending_retrieval_chunks``（承载 chunk_id/page_num/similarity/
   matched_sub_query/matched_keywords/role）。
2. 空 ``search_results`` 场景下挂载集合为空（不阻断创建）。
3. ``PageAnchorVerifyGate`` 在"LLM 编造页码"场景（source_pages 超出 chunk_pages
   参照集）返回 ``enforce_patch`` 且 ``source_pages_unverified`` 非空——这是
   Fix-02 的关键新能力（此前 chunk_pages 会退化为文档全集，无法检出幻觉）。
"""

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://x:x@localhost/x",
)


def _make_search_result(chunk_id, document_id, page_num, similarity, role, sub_query=None, keywords=None):
    from app.core.retriever.semantic_retriever import SearchResult
    return SearchResult(
        chunk_id=chunk_id,
        document_id=document_id,
        content="stub content",
        page_num=page_num,
        similarity=similarity,
        matched_sub_query=sub_query,
        matched_keywords=keywords,
        role=role,
    )


def _make_extracted_bid_response(source_pages=None):
    from app.schemas.bid_response import ExtractedBidResponse
    return ExtractedBidResponse(
        requirement_id=1,
        content="投标人提交了资质证明。",
        evidence_type="certificate",
        source_pages=source_pages or [5, 6],
        found=True,
        completeness="full",
        missing_elements=None,
        linked_to=None,
        evidence_anchor="ANCHOR-001",
        relevance_score=85,
        relevance_note=None,
    )


def test_create_response_attaches_retrieval_chunks_payload():
    """1. _create_response 挂载 _pending_retrieval_chunks，字段齐全。"""
    from app.core.extractor.bid_extractor import BidExtractor

    extractor = object.__new__(BidExtractor)
    extracted = _make_extracted_bid_response(source_pages=[5, 6])
    search_results = [
        _make_search_result(101, 77, 5, 0.88, "primary", sub_query="资质证书"),
        _make_search_result(102, 77, 6, 0.70, "primary", sub_query="营业执照"),
        _make_search_result(103, 77, 9, 0.0, "toc"),
        _make_search_result(104, 77, 12, 0.30, "image"),
    ]

    response = extractor._create_response(  # noqa: SLF001
        requirement_id=1,
        bid_document_id=77,
        extracted=extracted,
        search_results=search_results,
    )

    pending = getattr(response, "_pending_retrieval_chunks", None)
    assert isinstance(pending, list)
    assert len(pending) == 4

    by_role = {}
    for hit in pending:
        by_role.setdefault(hit["role"], []).append(hit)
    assert set(by_role.keys()) == {"primary", "toc", "image"}
    assert {h["chunk_id"] for h in by_role["primary"]} == {101, 102}
    # 子查询透传
    assert {h["matched_sub_query"] for h in by_role["primary"]} == {"资质证书", "营业执照"}
    # 所有记录 document_id 一致
    assert {h["document_id"] for h in pending} == {77}
    # page_num / similarity 透传
    primary_by_chunk = {h["chunk_id"]: h for h in by_role["primary"]}
    assert primary_by_chunk[101]["page_num"] == 5
    assert primary_by_chunk[101]["similarity"] == 0.88


def test_create_response_with_empty_search_results():
    """2. 空 search_results -> pending 列表为空，不阻断 BidResponse 创建。"""
    from app.core.extractor.bid_extractor import BidExtractor

    extractor = object.__new__(BidExtractor)
    extracted = _make_extracted_bid_response(source_pages=[])

    response = extractor._create_response(  # noqa: SLF001
        requirement_id=2,
        bid_document_id=77,
        extracted=extracted,
        search_results=[],
    )
    assert response is not None
    assert getattr(response, "_pending_retrieval_chunks", None) == []


def test_page_anchor_verify_gate_detects_llm_hallucinated_pages():
    """3. Fix-02 关键新能力：LLM 编造的页码不在真实检索命中页集内 → Gate enforce_patch。"""
    from app.core.ai.gates.base import GateContext
    from app.core.ai.gates.review.page_anchor_verify import PageAnchorVerifyGate

    class FakeExtracted:
        risk_level = "none"
        compliance_level = "fully"
        score = 95

    target = FakeExtracted()
    # chunk_pages 仅来自真实检索命中（Fix-02 精确集合），不是文档全集
    ctx = GateContext(
        flow="investigate",
        stage="post_llm",
        target=target,
        evidence={
            "chunk_pages": {5, 6, 7},
            "source_pages": [99, 100],
        },
    )
    gate = PageAnchorVerifyGate()
    assert gate.applies(ctx) is True

    verdict = gate.evaluate(ctx)
    assert verdict.verdict == "enforce_patch", (
        f"应检出幻觉页码，verdict={verdict.verdict}, reason={verdict.reason}"
    )
    assert verdict.suggested_patch is not None
    unverified = verdict.suggested_patch.get("source_pages_unverified") or []
    assert 99 in unverified and 100 in unverified


def test_page_anchor_verify_gate_passes_on_valid_pages():
    """3b. 所有 source_pages 都在真实命中集内 -> pass。"""
    from app.core.ai.gates.base import GateContext
    from app.core.ai.gates.review.page_anchor_verify import PageAnchorVerifyGate

    class FakeExtracted:
        risk_level = "none"

    ctx = GateContext(
        flow="investigate",
        stage="post_llm",
        target=FakeExtracted(),
        evidence={
            "chunk_pages": {5, 6, 7},
            "source_pages": [5, 6],
        },
    )
    verdict = PageAnchorVerifyGate().evaluate(ctx)
    assert verdict.verdict == "pass"


def test_review_evidence_bundle_retrieval_fields_default_empty():
    """4. 新合同字段默认值：retrieval_* 三字段在未装配时为空集合/空列表。"""
    from app.core.review_contracts import ReviewEvidenceBundle

    bundle = ReviewEvidenceBundle(
        response_content="",
        source_pages=[],
        completeness=None,
        missing_elements=[],
        linked_to=None,
        evidence_type=None,
        evidence_anchor=None,
        relevance_score=None,
        relevance_note=None,
        image_evidence="",
        cross_category_evidence="",
        quality_flags=[],
    )
    assert bundle.retrieval_chunk_pages == set()
    assert bundle.retrieval_primary_pages == set()
    assert bundle.retrieval_hit_sub_queries == []


def test_extracted_review_result_category_abbreviation_normalized():
    """Fix-03 / 问题 5: LLM 输出的 QUAL/TECH/COMM/PERF/STAFF/FORMAT 归一化为全称。"""
    from app.schemas.review import ExtractedReviewResult

    payload = {
        "requirement_id": 1,
        "category": "QUAL",
        "requirement_summary": "资质要求",
        "response_summary": "已提供营业执照",
        "is_compliant": True,
        "compliance_level": "fully",
        "score": 95,
        "comment": "符合要求，附证据页 5",
        "risk_level": "none",
    }
    obj = ExtractedReviewResult.model_validate(payload)
    assert obj.category == "qualification"

    # 全称输入保持不变
    payload2 = dict(payload, category="performance")
    obj2 = ExtractedReviewResult.model_validate(payload2)
    assert obj2.category == "performance"

    # 未知值原样透传，交给下游处理
    payload3 = dict(payload, category="unknown_cat")
    obj3 = ExtractedReviewResult.model_validate(payload3)
    assert obj3.category == "unknown_cat"


def test_review_prompt_removes_merge_verdict_sections():
    """Fix-04 / 问题 6: Prompt 合并判定章节与 Step 0 去重检查已下线。"""
    import app.core.ai.prompts.review as prompt_mod

    system_text = prompt_mod.REVIEW_SYSTEM_PROMPT
    user_text = prompt_mod.REVIEW_PROMPT
    no_resp_text = prompt_mod.NO_RESPONSE_PROMPT

    # 正向引用（示例/鼓励合并）必须全部消失
    for keyword in ("【合并判定", "【集中判定", "输出类型A", "输出类型B", "输出类型C"):
        assert keyword not in system_text, f"system 仍含 {keyword}"
        assert keyword not in user_text, f"user 仍含 {keyword}"

    # Step 0 去重检查移除（user + no_response 均已改为不含）
    assert "Step 0:去重检查" not in user_text
    assert "Step 0:去重检查" not in no_resp_text
    # Schema 约束在 Prompt 中明示。 起 requirement_id 不再由 LLM 输出，
    # 防合并约束改为直接禁止数组/多条判定（旧锚点「当前条目的单一整数」会与
    # 「请勿输出 requirement_id」构成矛盾指令）；schema 层拒绝逗号分隔 ID 的防线
    # 仍由 test_extracted_review_result_rejects_comma_separated_requirement_id 覆盖。
    assert "只输出一条 JSON 判定对象" in user_text
    assert "不要输出数组" in user_text
    assert "Fix-04" in system_text  # 新章节标记


def test_extracted_review_result_rejects_comma_separated_requirement_id():
    """Fix-04: 即使 LLM 违抗 Prompt 输出逗号分隔 requirement_id，schema 仍拒绝。"""
    import pytest as _pytest
    from pydantic import ValidationError

    from app.schemas.review import ExtractedReviewResult

    payload = {
        "requirement_id": "1,2,3",  # 非法：新 Prompt 明令禁止
        "category": "qualification",
        "requirement_summary": "保证金",
        "response_summary": "提供了 0 元证明",
        "is_compliant": False,
        "compliance_level": "not",
        "score": 0,
        "comment": "0 元保证金，需澄清。",
        "risk_level": "high",
    }
    with _pytest.raises(ValidationError):
        ExtractedReviewResult.model_validate(payload)


def test_build_review_result_item_includes_retrieval_chunks():
    """Fix-06 / 问题 3: API 结果项包含 retrieval_chunks 字段（空列表 or 真实命中）。"""
    from types import SimpleNamespace

    from app.models.review_result import ComplianceLevel, RiskLevel
    from app.services.review_service import _build_review_result_item

    rr = SimpleNamespace(
        id=1, project_id=1, requirement_id=1, bid_response_id=7,
        requirement_summary="", response_summary="", is_compliant=0,
        compliance_level=ComplianceLevel.NOT, score=0, comment="",
        estimated_score=None,
        risk_level=RiskLevel.HIGH,
        main_requirement_id=None,
        relevance_score=None, relevance_note=None,
        consistency_flag=None, evidence_relation=None, reconcile_note=None,
        canonical_key=None, fact_key=None, family_type=None, attribution_type=None,
        # stub 字段集必须与 _build_review_result_item 实际读取的字段保持同步，
        # 缺字段会直接 AttributeError 而非断言失败，难以定位
        rule_assessments=None,
    )

    # 默认空
    item_empty = _build_review_result_item(rr)
    assert item_empty["retrieval_chunks"] == []
    assert "rule_assessments" in item_empty, "评分项逐规则判定属于结果项 API 契约字段"

    # 有命中
    chunks = [
        {"chunk_id": 101, "page_num": 5, "similarity": 0.88, "role": "primary",
         "matched_sub_query": "资质"},
        {"chunk_id": 102, "page_num": 6, "similarity": 0.70, "role": "primary",
         "matched_sub_query": None},
    ]
    item = _build_review_result_item(rr, retrieval_chunks=chunks)
    assert item["retrieval_chunks"] == chunks


def test_call_llm_review_consumes_prompt_registry_params():
    """Fix-07 / 问题 4: _call_llm_review 的 temperature/max_tokens 从 PromptRegistry 读取。"""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from app.core.ai.reviewer import ReviewOrchestrator

    captured_kwargs: dict = {}

    async def fake_chat(user_message, system_message=None, temperature=0.1, max_tokens=2000):
        captured_kwargs["temperature"] = temperature
        captured_kwargs["max_tokens"] = max_tokens
        return (
            '{"requirement_id":1,"category":"qualification",'
            '"requirement_summary":"资质","response_summary":"已提供",'
            '"is_compliant":true,"compliance_level":"fully","score":95,'
            '"comment":"符合要求，附证据页 5","risk_level":"none"}'
        )

    orchestrator = object.__new__(ReviewOrchestrator)
    orchestrator.max_retries = 1
    orchestrator.llm_client = SimpleNamespace(chat=fake_chat, chat_json=AsyncMock())
    orchestrator._project_id = None

    # Mock PromptRegistry: 返回自定义 temperature=0.33 / max_tokens=777
    class _FakeResolved:
        temperature = 0.33
        max_tokens = 777
        system_message = "FAKE_SYS"
        user_template = "FAKE_USER"
        system_version_id = None
        user_version_id = None
        stability_class = "semi"

    with patch("app.core.ai.reviewer.get_registry") as mock_reg:
        mock_reg.return_value.get_prompt.return_value = _FakeResolved()
        # 同时把 cd_schema 关掉，避免走 chat_json
        orchestrator._resolve_cd_schema = lambda: None
        result = asyncio.run(orchestrator._call_llm_review("test prompt"))

    assert result is not None
    assert captured_kwargs["temperature"] == 0.33, (
        f"应从 PromptRegistry 读取 temperature，实际 {captured_kwargs}"
    )
    assert captured_kwargs["max_tokens"] == 777, (
        f"应从 PromptRegistry 读取 max_tokens，实际 {captured_kwargs}"
    )


def test_project_bound_dispatcher_accepts_node_key_kwarg():
    """Fix-07: ProjectBoundDispatcher.chat/chat_json 增 node_key 可选入参，不破坏老调用方。"""
    import inspect

    from app.core.ai.ai_factory import ProjectBoundDispatcher

    for method_name in ("chat", "chat_json"):
        sig = inspect.signature(getattr(ProjectBoundDispatcher, method_name))
        assert "node_key" in sig.parameters, f"{method_name} 缺少 node_key 参数"
        param = sig.parameters["node_key"]
        # 必须为 keyword-only 且默认 None（向后兼容）
        assert param.default is None
        assert param.kind == inspect.Parameter.KEYWORD_ONLY


def test_cross_evidence_to_prompt_line_includes_source_metadata():
    """Fix-08 / 问题 8: CrossEvidence 渲染带来源类别、页码、锚点。"""
    from app.core.review_contracts import CrossEvidence

    ce = CrossEvidence(
        category="qualification",
        snippet="投标人具备 ISO9001 质量管理体系",
        pages="5,6",
        anchor="ISO-CERT-2025",
    )
    line = ce.to_prompt_line()
    # 类别经 translate_token 译为中文并加「类响应」后缀；未命中译表时才回退英文原值
    assert "来自资质要求类响应" in line, f"类别未按 translate_token 译出中文: {line}"
    assert "qualification" not in line, f"译表失效导致回退英文原值: {line}"
    # pages "5,6" 必须规范化为「第…页」而非原样透出
    assert "第5、6页" in line, f"页码未规范化为「第…页」: {line}"
    assert "锚点:ISO-CERT-2025" in line
    assert "投标人具备" in line

    # 无页/锚时也能回退
    ce2 = CrossEvidence(category="", snippet="摘要", pages="", anchor=None)
    line2 = ce2.to_prompt_line()
    assert "未知" in line2 or "未知类别" in line2


def test_review_evidence_bundle_has_cross_category_injection_flag():
    """Fix-08: ReviewEvidenceBundle.has_cross_category_injection 正确反映跨证据注入状态。"""
    from app.core.review_contracts import CrossEvidence, ReviewEvidenceBundle

    empty = ReviewEvidenceBundle(
        response_content="", source_pages=[], completeness=None,
        missing_elements=[], linked_to=None, evidence_type=None,
        evidence_anchor=None, relevance_score=None, relevance_note=None,
        image_evidence="", cross_category_evidence="", quality_flags=[],
    )
    assert empty.has_cross_category_injection is False

    with_text = ReviewEvidenceBundle(
        response_content="", source_pages=[], completeness=None,
        missing_elements=[], linked_to=None, evidence_type=None,
        evidence_anchor=None, relevance_score=None, relevance_note=None,
        image_evidence="", cross_category_evidence="some cross text",
        quality_flags=[],
    )
    assert with_text.has_cross_category_injection is True

    with_sources = ReviewEvidenceBundle(
        response_content="", source_pages=[], completeness=None,
        missing_elements=[], linked_to=None, evidence_type=None,
        evidence_anchor=None, relevance_score=None, relevance_note=None,
        image_evidence="", cross_category_evidence="",
        quality_flags=[],
        cross_category_evidence_sources=[
            CrossEvidence(category="qualification", snippet="s", pages="1"),
        ],
    )
    assert with_sources.has_cross_category_injection is True


def test_retry_tracker_gate_replay_fields_whitelist_is_sane():
    """Fix-09 / 问题 9: retry 覆盖的 Gate 产出字段白名单应包含关键 Gate 字段。"""
    from app.core.ai.reviewer import ReviewOrchestrator

    fields = ReviewOrchestrator._GATE_REPLAY_COVER_FIELDS

    # 核心 Gate 产出字段必须在白名单中
    required = {
        "compliance_level", "score", "risk_level", "comment",
        "consistency_flag", "reconcile_note",
        "relevance_score", "relevance_note",
        "failure_state",
    }
    missing = required - fields
    assert not missing, f"Gate 产出字段白名单缺失: {missing}"

    # 不应包含结构类字段（id / created_at / project_id 等）
    forbidden = {"id", "created_at", "updated_at", "project_id", "requirement_id"}
    leaked = forbidden & fields
    assert not leaked, f"结构字段误入白名单: {leaked}"


def test_retry_gate_replay_overrides_stale_consistency_flag_via_whitelist():
    """Fix-09: 模拟 retry_result 经 Gate 产出 consistency_flag=None,
    existing 有陈旧 'cross_conflict'，覆盖后必须变为 None（Gate 结论优先）。"""
    from types import SimpleNamespace

    from app.core.ai.reviewer import ReviewOrchestrator

    # 模拟 ReviewResult 列（子集）
    columns = [
        SimpleNamespace(name="id"),
        SimpleNamespace(name="created_at"),
        SimpleNamespace(name="updated_at"),
        SimpleNamespace(name="project_id"),
        SimpleNamespace(name="requirement_id"),
        SimpleNamespace(name="compliance_level"),
        SimpleNamespace(name="consistency_flag"),
        SimpleNamespace(name="reconcile_note"),
        SimpleNamespace(name="comment"),
    ]

    existing = SimpleNamespace(
        id=1, project_id=1, requirement_id=1,
        compliance_level="fully",
        consistency_flag="cross_conflict",  # 陈旧标记
        reconcile_note="旧复核备注",
        comment="original comment",
    )
    retry_result = SimpleNamespace(
        id=None, project_id=1, requirement_id=1,
        compliance_level="fully",
        consistency_flag=None,  # Gate 重判为"无冲突"
        reconcile_note=None,
        comment="retry comment 含证据",
    )

    whitelist = ReviewOrchestrator._GATE_REPLAY_COVER_FIELDS
    for column in columns:
        name = column.name
        if name in {"id", "created_at", "updated_at"}:
            continue
        new_val = getattr(retry_result, name, None)
        if name in whitelist:
            setattr(existing, name, new_val)
        elif new_val is not None:
            setattr(existing, name, new_val)

    # 关键断言：陈旧 consistency_flag 已被 None 覆盖
    assert existing.consistency_flag is None, (
        "Gate 白名单覆盖应把 consistency_flag 从 cross_conflict 更新为 None"
    )
    assert existing.reconcile_note is None
    # 结构字段不变
    assert existing.id == 1
    # 非白名单但非 None 的字段照常覆盖
    assert existing.comment == "retry comment 含证据"


def test_check_extraction_quality_returns_structured_warning():
    """Fix-10 / 问题 10: _check_extraction_quality 返回结构化 {has_issue, reasons, raw_warnings}。"""
    from types import SimpleNamespace

    from app.services.review_service import ReviewService

    # 场景1: 无告警
    project_clean = SimpleNamespace(id=1, extraction_warnings=None)
    r1 = ReviewService._check_extraction_quality(project_clean)
    assert r1["has_issue"] is False
    assert r1["reasons"] == []

    # 场景2: 列表为空
    project_empty = SimpleNamespace(id=2, extraction_warnings=[])
    r2 = ReviewService._check_extraction_quality(project_empty)
    assert r2["has_issue"] is False

    # 场景3: 有非关键告警（不含关键词）
    project_noise = SimpleNamespace(
        id=3, extraction_warnings=["某页字体渲染一般"],
    )
    r3 = ReviewService._check_extraction_quality(project_noise)
    assert r3["has_issue"] is False
    assert r3["raw_warnings"] == ["某页字体渲染一般"]

    # 场景4: 有严重告警 -> has_issue=True + reasons 包含该条
    project_bad = SimpleNamespace(
        id=4, extraction_warnings=[
            "第 12-18 页提取质量异常（OCR 置信度极低）",
            "另一个无关的小告警",
        ],
    )
    r4 = ReviewService._check_extraction_quality(project_bad)
    assert r4["has_issue"] is True
    assert len(r4["reasons"]) == 1
    assert "提取质量异常" in r4["reasons"][0]
    assert len(r4["raw_warnings"]) == 2


def test_relevance_anomaly_backfill_uses_full_names_only():
    """Fix-03: RelevanceAnomalyBackfillGate 的类别白名单收敛为纯全称。"""
    from app.core.ai.gates.review.relevance_anomaly_backfill import (
        _HIGH_SCORE_CATEGORIES,
    )
    assert _HIGH_SCORE_CATEGORIES == {"performance", "qualification", "staffing"}
    # 缩写不再被接受（由 ExtractedReviewResult validator 统一归一）
    assert "QUAL" not in _HIGH_SCORE_CATEGORIES
    assert "PERF" not in _HIGH_SCORE_CATEGORIES
    assert "STAFF" not in _HIGH_SCORE_CATEGORIES


if __name__ == "__main__":
    test_create_response_attaches_retrieval_chunks_payload()
    test_create_response_with_empty_search_results()
    test_page_anchor_verify_gate_detects_llm_hallucinated_pages()
    test_page_anchor_verify_gate_passes_on_valid_pages()
    test_review_evidence_bundle_retrieval_fields_default_empty()
    test_extracted_review_result_category_abbreviation_normalized()
    test_review_prompt_removes_merge_verdict_sections()
    test_extracted_review_result_rejects_comma_separated_requirement_id()
    test_build_review_result_item_includes_retrieval_chunks()
    test_call_llm_review_consumes_prompt_registry_params()
    test_project_bound_dispatcher_accepts_node_key_kwarg()
    test_cross_evidence_to_prompt_line_includes_source_metadata()
    test_review_evidence_bundle_has_cross_category_injection_flag()
    test_retry_tracker_gate_replay_fields_whitelist_is_sane()
    test_retry_gate_replay_overrides_stale_consistency_flag_via_whitelist()
    test_check_extraction_quality_returns_structured_warning()
    test_relevance_anomaly_backfill_uses_full_names_only()
    print("ALL 17 TESTS PASSED")
