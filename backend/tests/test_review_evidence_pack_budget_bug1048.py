""" 防御测试：审查证据包全局预算。

职责: 锁定"广召回候选留库、Prompt 只携带每分面最小充分证据"这一机制——
 统一总预算、分面保底、去重、禁止项不被裁、可见覆盖缺口显形、
 发送前硬上限预检与超限待判定兜底。
不做: 不连真实 DB、不调用真实 LLM、不测检索召回口径(归)。

历史教训映射:
 (a) 只封顶了项目账本, 图片/独立补搜/原文/知识库仍各自累加;
 10| (b) 收缩只在 provider 返回超长 400 之后触发, 超时类过载完全不受控;
 (c) 证据过载曾把单条款失败放大为整批审查中断。
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.synthetic_data import synthetic_text

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.ai.reviewer_parts.evidence_pack_budget import (
    SECTION_CROSS,
    SECTION_IMAGE,
    SECTION_INDEPENDENT,
    SECTION_KNOWLEDGE,
    SECTION_PROHIBITION,
    SECTION_RAW,
    EvidenceItem,
    assemble_evidence_pack,
    estimate_prompt_tokens,
    prompt_hard_cap_violation,
    resolve_pack_budget,
)
from app.core.ai.reviewer_parts.pair_review import build_review_prompt
from app.core.config import settings
from app.core.retriever.evidence_coverage import EvidenceFacet
from app.core.review_contracts import ReviewEvidenceBundle

# ---------------------------------------------------------------------------
# 夹具: 两个不同实体的同形态场景（形态覆盖，非案例专用）
# ---------------------------------------------------------------------------


def _image_item(idx: int, content: str, *, doc_id: int = 2638) -> EvidenceItem:
    return EvidenceItem(
        section=SECTION_IMAGE,
        text=f"▶ (第{idx}页)\n{content}\n",
        content=content,
        label=f"image:{idx}",
        page=idx,
        chunk_id=idx,
        document_id=doc_id,
        role="image",
        similarity=0.4,
    )


def _raw_item(
    chunk_id: int,
    content: str,
    sub_query: str | None = None,
    *,
    doc_id: int = 2638,
    similarity: float = 0.8,
) -> EvidenceItem:
    return EvidenceItem(
        section=SECTION_RAW,
        text=f"▶ [正文命中·第{chunk_id}页·相似度={similarity:.2f}] {content}",
        content=content,
        label=f"raw:{chunk_id}",
        page=chunk_id,
        chunk_id=chunk_id,
        document_id=doc_id,
        role="primary",
        similarity=similarity,
        matched_sub_query=sub_query,
    )


def _performance_fixture() -> tuple[list[EvidenceItem], tuple[EvidenceFacet, ...]]:
    """夹具 A: 业绩合同类（分卷技术标），33 图片 + 18 深搜 + 15 正文。"""
    facets = (
        EvidenceFacet(key="同类项目业绩", query="投标人 同类项目业绩"),
        EvidenceFacet(key="结算发票", query="投标人 结算发票"),
        EvidenceFacet(key="项目合同", query="投标人 项目合同"),
    )
    items: list[EvidenceItem] = []
    for i in range(1, 34):
        items.append(_image_item(i, "扫描件内容与本要求弱相关" * 12))
    for i in range(100, 118):
        items.append(EvidenceItem(
            section=SECTION_INDEPENDENT,
            text=f"▶ (第{i}页)\n" + "独立补搜命中的合同条款正文" * 12,
            content="独立补搜命中的合同条款正文",
            label=f"independent:{i}",
            page=i, chunk_id=i, document_id=2638, role="deep", similarity=0.3,
        ))
    items.append(_raw_item(59, "同类项目业绩一览表：宁东油田防腐药剂性能测试",
                           "投标人 同类项目业绩", similarity=0.9))
    items.append(_raw_item(61, "结算发票明细：发票号 34550000-26-FW2022-0003",
                           "投标人 结算发票", similarity=0.5))
    items.append(_raw_item(63, "项目合同扫描件：委托技术服务合同",
                           "投标人 项目合同", similarity=0.45))
    for i in range(200, 212):
        items.append(_raw_item(i, "与本要求无关的通用条款正文" * 10, similarity=0.35))
    return items, facets


def _personnel_fixture() -> tuple[list[EvidenceItem], tuple[EvidenceFacet, ...]]:
    """夹具 B: 人员社保/职称证书类（整本单卷），同形态不同实体。"""
    facets = (
        EvidenceFacet(key="社保缴费证明", query="项目经理 社保缴费证明"),
        EvidenceFacet(key="职称证书", query="技术负责人 职称证书"),
    )
    items: list[EvidenceItem] = []
    for i in range(1, 31):
        items.append(_image_item(i, "人员照片与无关扫描件" * 12, doc_id=2637))
    items.append(_raw_item(41, "社保缴费证明：2025-10 至 2026-03 连续缴纳",
                           "项目经理 社保缴费证明", doc_id=2637, similarity=0.88))
    items.append(_raw_item(108, "技术负责人职称证书：高级工程师",
                           "技术负责人 职称证书", doc_id=2637, similarity=0.42))
    return items, facets


# ---------------------------------------------------------------------------
# 1. 预算解析（迁移自 _review_evidence_budgets 的既有语义）
# ---------------------------------------------------------------------------


def test_pack_budget_decreases_monotonically_with_evidence_floor() -> None:
    b0, b1, b2, b3 = (resolve_pack_budget(r) for r in range(4))
    assert b0.pack_chars >= b1.pack_chars >= b2.pack_chars >= b3.pack_chars
    assert b3.pack_chars >= 800, "证据总帽必须保底，不得压到 0"
    assert b0.knowledge_chars >= b1.knowledge_chars >= b2.knowledge_chars
    assert b2.knowledge_chars == 0, "知识库衰减更快，round2 应整块放弃"


def test_pack_budget_round0_matches_configured_cap() -> None:
    budget = resolve_pack_budget(0)
    assert budget.pack_chars == int(settings.REVIEW_EVIDENCE_PACK_CHARS)
    assert budget.item_max == int(settings.REVIEW_EVIDENCE_ITEM_MAX)


# ---------------------------------------------------------------------------
# 2. 组包: 总预算 + 条目上限 + 分面保底（两实体夹具）
# ---------------------------------------------------------------------------


def test_performance_fixture_caps_total_and_keeps_every_facet() -> None:
    """夹具 A: 66 条候选收敛到预算内，三个分面各保留代表证据。"""
    items, facets = _performance_fixture()
    budget = resolve_pack_budget(0)
    result = assemble_evidence_pack(items, facets, budget)

    assert result.candidate_count == len(items)
    assert result.used_chars <= budget.pack_chars, "证据区合计不得超过总预算"
    assert len(result.kept_labels) <= budget.item_max, "条目数不得超过上限"
    assert result.dropped_labels, "广召回候选必须有被挡在 Prompt 之外的部分"
    assert result.visible_facet_missing == (), "每个分面都应有代表证据进入可见集"

    visible = "\n".join(result.sections.values())
    assert "同类项目业绩一览表" in visible
    assert "结算发票明细" in visible
    assert "项目合同扫描件" in visible


def test_personnel_fixture_same_mechanism_second_entity() -> None:
    """夹具 B: 同形态不同实体（人员社保/职称证书）同样成立。"""
    items, facets = _personnel_fixture()
    budget = resolve_pack_budget(0)
    result = assemble_evidence_pack(items, facets, budget)

    assert result.used_chars <= budget.pack_chars
    assert len(result.kept_labels) <= budget.item_max
    assert result.visible_facet_missing == ()

    visible = "\n".join(result.sections.values())
    assert "社保缴费证明" in visible
    assert "职称证书" in visible


def test_weak_facet_survives_dominant_section_flood() -> None:
    """强势区的大量候选不得挤掉弱势分面的唯一证据。"""
    items, facets = _performance_fixture()
    result = assemble_evidence_pack(items, facets, resolve_pack_budget(0))
    # 弱势分面(相似度 0.45)的唯一证据必须在, 而 33 条高频图片不可能全进
    assert "项目合同扫描件" in "\n".join(result.sections.values())
    kept_images = [lb for lb in result.kept_labels if lb.startswith("image:")]
    assert len(kept_images) < 33


# ---------------------------------------------------------------------------
# 3. 去重 / 禁止项 / 可见覆盖缺口
# ---------------------------------------------------------------------------


def test_same_chunk_in_two_sections_rendered_once() -> None:
    """同一 chunk 同时出现在图片区与原文区时只渲染一次，且保留原文区表达。"""
    shared = synthetic_text('营业执照扫描件 ', '统一社会信用代码 ', '91320100M', 'A1ABCDE1X')
    items = [
        _image_item(7, shared),
        _raw_item(7, shared, "投标人 营业执照"),
    ]
    result = assemble_evidence_pack(items, (), resolve_pack_budget(0))
    assert result.candidate_count == 1, "(document_id, chunk_id) 相同必须去重"
    assert "\n".join(result.sections.values()).count(synthetic_text('913201', '00MA1A', 'BCDE1X')) == 1
    # 原文佐证带页码/角色/相似度标签，信息密度高于图片摘要，去重时应取优
    assert result.raw_evidence_excerpts, "同 chunk 去重必须保留区优先级更高的原文表达"
    assert result.section_text(SECTION_IMAGE) == ""


def test_dedupe_keeps_higher_priority_section_regardless_of_input_order() -> None:
    """去重取优不依赖输入顺序（图片先到或原文先到结果一致）。"""
    shared = "安全生产许可证 编号(豫)JZ安许证字0001"
    raw_first = assemble_evidence_pack(
        [_raw_item(9, shared, "投标人 安全生产许可证"), _image_item(9, shared)],
        (), resolve_pack_budget(0),
    )
    image_first = assemble_evidence_pack(
        [_image_item(9, shared), _raw_item(9, shared, "投标人 安全生产许可证")],
        (), resolve_pack_budget(0),
    )
    assert raw_first.raw_evidence_excerpts and image_first.raw_evidence_excerpts
    assert raw_first.section_text(SECTION_IMAGE) == ""
    assert image_first.section_text(SECTION_IMAGE) == ""


def test_prohibition_item_survives_tiny_budget() -> None:
    """禁止项反向核验是放行红线，预算极小也不得被裁掉。"""
    items = [
        EvidenceItem(
            section=SECTION_PROHIBITION,
            text="【禁止项反向核验】▶ (第5页 / 技术标) 报价 128 万元",
            content="报价 128 万元",
            label="prohibition_leak",
            clippable=False,
        ),
        _image_item(1, "无关扫描件" * 200),
        _image_item(2, "无关扫描件" * 200),
    ]
    budget = resolve_pack_budget(0).__class__(
        pack_chars=300, knowledge_chars=0, ledger_chars=0, item_chars=500, item_max=12,
    )
    result = assemble_evidence_pack(items, (), budget)
    assert "禁止项反向核验" in result.section_text(SECTION_PROHIBITION)


def test_visible_facet_gap_reported_when_budget_squeezes_facet() -> None:
    """候选已覆盖但可见集放不下时，必须报出可见覆盖缺口。"""
    items, facets = _performance_fixture()
    tight = resolve_pack_budget(0).__class__(
        pack_chars=200, knowledge_chars=0, ledger_chars=0, item_chars=500, item_max=1,
    )
    squeezed = assemble_evidence_pack(items, facets, tight)
    assert squeezed.visible_facet_missing, "预算不足时必须显形，不得静默声称已覆盖"

    ample = assemble_evidence_pack(items, facets, resolve_pack_budget(0))
    assert ample.visible_facet_missing == (), "预算充足时不应误报缺口"


def test_knowledge_yields_before_bid_evidence() -> None:
    """知识库属辅助区，预算紧张时先于投标原文证据被牺牲。"""
    items = [
        _raw_item(1, "投标原文关键证据"),
        EvidenceItem(
            section=SECTION_KNOWLEDGE,
            text="知识库参考内容" * 400,
            content="知识库参考",
            label="knowledge_context",
        ),
    ]
    budget = resolve_pack_budget(0).__class__(
        pack_chars=400, knowledge_chars=4000, ledger_chars=0, item_chars=500, item_max=12,
    )
    result = assemble_evidence_pack(items, (), budget)
    assert "投标原文关键证据" in result.raw_evidence_excerpts
    assert len(result.knowledge_context_text) < 400 * 7


def test_empty_items_returns_empty_pack() -> None:
    result = assemble_evidence_pack([], (), resolve_pack_budget(0))
    assert result.sections == {}
    assert result.used_chars == 0
    assert result.visible_facet_missing == ()


def test_section_headers_present_only_when_section_kept() -> None:
    items = [_raw_item(1, "原文证据内容")]
    result = assemble_evidence_pack(items, (), resolve_pack_budget(0))
    assert "投标原文佐证" in result.raw_evidence_excerpts
    assert "以原文为准" in result.raw_evidence_excerpts
    assert result.section_text(SECTION_IMAGE) == ""
    assert result.section_text(SECTION_CROSS) == ""


# ---------------------------------------------------------------------------
# 4. 发送前硬上限预检
# ---------------------------------------------------------------------------


def test_estimate_prompt_tokens_uses_chinese_heuristic() -> None:
    assert estimate_prompt_tokens("") == 0
    assert estimate_prompt_tokens("四个汉字") == 2


def test_hard_cap_passes_for_normal_prompt() -> None:
    assert prompt_hard_cap_violation("系统提示", "普通长度的审查 Prompt") is None


def test_hard_cap_blocks_oversized_prompt(monkeypatch) -> None:
    monkeypatch.setattr(settings, "REVIEW_PROMPT_HARD_CAP_TOKENS", 100)
    reason = prompt_hard_cap_violation("系统提示", "超" * 5000)
    assert reason is not None
    assert "超过上限" in reason


def test_hard_cap_byte_limit_independent_of_token_limit(monkeypatch) -> None:
    """token 上限关闭时字节上限仍必须生效（云端按请求体字节拒绝）。"""
    monkeypatch.setattr(settings, "REVIEW_PROMPT_HARD_CAP_TOKENS", 0)
    monkeypatch.setattr(settings, "REVIEW_PROMPT_HARD_CAP_BYTES", 1000)
    reason = prompt_hard_cap_violation("", "中" * 2000)
    assert reason is not None
    assert "字节" in reason


def test_hard_cap_disabled_when_both_limits_zero(monkeypatch) -> None:
    """两项上限置 0 即整体关闭预检（回滚开关）。"""
    monkeypatch.setattr(settings, "REVIEW_PROMPT_HARD_CAP_TOKENS", 0)
    monkeypatch.setattr(settings, "REVIEW_PROMPT_HARD_CAP_BYTES", 0)
    assert prompt_hard_cap_violation("", "超" * 100000) is None


# ---------------------------------------------------------------------------
# 5. review_single_pair 端到端: 超限不发请求、转待判定、不中断项目
# ---------------------------------------------------------------------------


def _minimal_pair(has_response: bool = True) -> MagicMock:
    requirement = SimpleNamespace(
        id=43569,
        description="投标人应具备较强的经营实力与履行合同能力",
        sub_requirements=None,
        canonical_key=None,
        main_requirement_id=None,
        requirement_kind="normal",
        is_key_review=0,
        score_weight=None,
        scoring_metadata=None,
        review_relevance="actionable",
    )
    pair = MagicMock()
    pair.requirement = requirement
    pair.requirement_id = 43569
    pair.response_id = 17784
    pair.category = "qualification"
    pair.is_mandatory = True
    pair.has_response = has_response
    pair.response = SimpleNamespace(
        id=17784, content="投标人提供了营业执照", document_id=2638,
        source_pages="11", completeness="partial", missing_elements=None,
        linked_to=None, evidence_type=None, evidence_anchor="cert#1",
        relevance_score=80, relevance_note=None,
    )
    return pair


def _bundle_with_items(items: list[EvidenceItem]) -> ReviewEvidenceBundle:
    """构造 bundle 并走与生产同一条 round 0 组包路径（回填各证据区）。"""
    from app.core.ai.reviewer_parts.evidence_prefetch import apply_round0_evidence_pack

    bundle = ReviewEvidenceBundle(
        response_content="投标人提供了营业执照",
        source_pages=[11],
        completeness="partial",
        missing_elements=[],
        linked_to=None,
        evidence_type="certificate",
        evidence_anchor="cert#1",
        relevance_score=80,
        relevance_note=None,
        image_evidence="",
        cross_category_evidence="",
        quality_flags=[],
        raw_evidence_excerpts="",
        evidence_items=items,
    )
    return apply_round0_evidence_pack(bundle, (), 43569)


def _prompt_stub() -> SimpleNamespace:
    """Prompt 模板桩: 必须含 response_content 占位符, 否则证据不进 Prompt,
    预检就永远测不到超限（本测试文件曾因此假绿）。"""
    return SimpleNamespace(
        system_message="SYS",
        user_template="要求:{requirement_description}\n响应:{response_content}",
        temperature=0.0,
        max_tokens=1500,
    )


def _orchestrator_with_recorder() -> tuple[object, list]:
    from app.core.ai.reviewer import ReviewOrchestrator

    llm_calls: list = []

    async def _llm(*args, **kwargs):
        llm_calls.append(kwargs)
        raise AssertionError("超限时禁止调用审查 LLM")

    orch = object.__new__(ReviewOrchestrator)
    orch.item_timeout = 5
    orch._call_llm_review = _llm
    orch._build_review_prompt = lambda p, b, k, **kw: build_review_prompt(
        self=MagicMock(), pair=p, evidence_bundle=b, knowledge_context=k, **kw
    )
    orch._create_failure_result = lambda project_id, pair: SimpleNamespace(
        comment="", reconcile_note="", consistency_flag="unchecked",
        requirement_id=pair.requirement_id,
    )
    return orch, llm_calls


def test_oversize_pack_skips_llm_and_returns_pending(monkeypatch) -> None:
    """round 0 超限且收缩到底仍超限 → 不调 LLM，落带失败标记的待判定结果。"""
    from app.core.ai.reviewer_parts.pair_review import review_single_pair
    from app.core.ai.reviewer_parts.result_builder import FAILURE_COMMENT_MARKER

    monkeypatch.setattr(settings, "REVIEW_PROMPT_HARD_CAP_TOKENS", 50)
    monkeypatch.setattr(settings, "REVIEW_INPUT_SHRINK_RETRIES", 2)

    items = [_raw_item(i, "超长证据正文" * 300) for i in range(1, 6)]
    orch, llm_calls = _orchestrator_with_recorder()

    with patch("app.core.ai.reviewer.get_registry") as mock_reg:
        mock_reg.return_value.get_prompt.return_value = _prompt_stub()
        result = asyncio.run(
            review_single_pair(orch, 3632, _minimal_pair(), _bundle_with_items(items))
        )

    assert llm_calls == [], "超限必须在发送前拦截，不得产生 LLM 调用"
    assert FAILURE_COMMENT_MARKER in result.comment
    assert "证据包超出模型输入预算" in result.comment
    assert result.consistency_flag == "needs_review"
    assert "不构成未响应推定" in result.reconcile_note
    assert "未提供" not in result.comment


def test_within_cap_proceeds_to_llm(monkeypatch) -> None:
    """预算内的证据包必须照常进入 LLM 审查（预检不误伤正常项）。"""
    from app.core.ai.reviewer_parts.pair_review import review_single_pair

    monkeypatch.setattr(settings, "REVIEW_PROMPT_HARD_CAP_TOKENS", 24000)
    called: list = []

    async def _llm(*args, **kwargs):
        called.append(kwargs)
        return None  # 让流程走到 failure 分支，断言只看是否调用

    orch, _ = _orchestrator_with_recorder()
    orch._call_llm_review = _llm

    with patch("app.core.ai.reviewer.get_registry") as mock_reg:
        mock_reg.return_value.get_prompt.return_value = _prompt_stub()
        asyncio.run(
            review_single_pair(
                orch, 3632, _minimal_pair(),
                _bundle_with_items([_raw_item(1, "简短证据")]),
            )
        )

    assert called, "预算内的条款必须正常调用审查 LLM"


# ---------------------------------------------------------------------------
# 6. 向后兼容: 旧式 bundle（无 evidence_items）
# ---------------------------------------------------------------------------


def test_bundle_without_evidence_items_is_constructible() -> None:
    bundle = ReviewEvidenceBundle(
        response_content="x", source_pages=[], completeness=None,
        missing_elements=[], linked_to=None, evidence_type=None,
        evidence_anchor=None, relevance_score=None, relevance_note=None,
        image_evidence="", cross_category_evidence="", quality_flags=[],
    )
    assert bundle.evidence_items == []


def test_legacy_bundle_still_injects_raw_evidence_into_prompt() -> None:
    """旧式 bundle 只带 raw_evidence_excerpts 时，Prompt 仍必须注入原文。"""
    bundle = ReviewEvidenceBundle(
        response_content="投标人承诺持有一级资质",
        source_pages=[12], completeness="full", missing_elements=[],
        linked_to=None, evidence_type="certificate", evidence_anchor="cert#1",
        relevance_score=85, relevance_note=None,
        image_evidence="", cross_category_evidence="", quality_flags=[],
        raw_evidence_excerpts="【投标原文佐证】\n▶ 资质证书编号 GC2024-001",
    )
    prompt = build_review_prompt(
        self=MagicMock(), pair=_minimal_pair(), evidence_bundle=bundle,
    )
    assert "GC2024-001" in prompt


def test_legacy_bundle_shrink_path_still_clips() -> None:
    """旧式 bundle 在收缩轮次退化为按段截断，不因缺条目而报错。"""
    bundle = ReviewEvidenceBundle(
        response_content="响应摘要", source_pages=[1], completeness="partial",
        missing_elements=[], linked_to=None, evidence_type=None,
        evidence_anchor=None, relevance_score=None, relevance_note=None,
        image_evidence="图片证据" * 3000, cross_category_evidence="",
        quality_flags=[], raw_evidence_excerpts="原文佐证" * 3000,
    )
    prompt = build_review_prompt(
        self=MagicMock(), pair=_minimal_pair(), evidence_bundle=bundle,
        reduction_round=2,
    )
    assert len(prompt) < len(bundle.image_evidence) + len(bundle.raw_evidence_excerpts)
