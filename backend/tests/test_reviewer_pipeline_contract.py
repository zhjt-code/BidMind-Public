"""ReviewOrchestrator 拆分（R2-3）Phase 1 快照契约测试

锁定以下契约，防止 R2-3 拆分破坏外部测试 / 调用方依赖：

A. 模块级符号导出（COMPLIANCE_LEVEL_MAP / RISK_LEVEL_MAP / _get_review_prompts /
   get_registry namespace patch 点 / get_review_orchestrator / ReviewOrchestrator）
B. ReviewOrchestrator.__init__ 9 个必备实例字段
C. 4 个 ClassVar 常量内容稳定（_GATE_REPLAY_COVER_FIELDS 18 字段 byte-equal /
   _DOMAIN_KEYWORDS / _MISSING_SIGNALS / _GENERIC_STOPWORDS）
D. 43 个 _xxx 私有方法名 + 3 个 public 入口稳定性（外部测试通过 inspect.getsource /
   getattr 访问）
E. _create_failure_result / _create_failure_result_safe 失败合同字段 byte-equal
F. summarize_results 返回结构稳定
G. _review_project_impl 三条主线：skip_existing 空结果 + 空 pairs 触发 post_check +
   _retry_tracker 初始化
H. _post_review_consistency_check 调用 _build_fact_graph 与 _judge_fact_then_project 锚点
I. inspect.getsource(_call_llm_review) / _resolve_cd_schema 可获取且包含锚点字符串
J. _get_review_prompts 返回 (system, user_tpl, no_resp_tpl) 三元组
"""

from __future__ import annotations

import inspect
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.ai import reviewer as reviewer_module
from app.core.ai.reviewer import (
    COMPLIANCE_LEVEL_MAP,
    RISK_LEVEL_MAP,
    ReviewOrchestrator,
    _get_review_prompts,
    get_review_orchestrator,
)
from app.core.matcher.requirement_matcher import MatchedPair
from app.models.bid_response import BidResponse
from app.models.requirement import Requirement
from app.models.review_result import ComplianceLevel, RiskLevel

# ---------------------------------------------------------------------------
# A. 模块级符号导出（拆分后 reviewer.py 必须保留这些符号供外部 patch / import）
# ---------------------------------------------------------------------------


class TestModuleLevelSymbols:
    """A 类: 模块级符号导出契约。"""

    def test_module_exports_required_top_level_names(self):
        """reviewer 模块顶层必须保留所有外部依赖的符号。"""
        required = {
            "ReviewOrchestrator",
            "get_review_orchestrator",
            "COMPLIANCE_LEVEL_MAP",
            "RISK_LEVEL_MAP",
            "_get_review_prompts",
            "get_registry",
        }
        missing = required - set(vars(reviewer_module).keys())
        assert not missing, f"reviewer 模块顶层缺失符号: {missing}"

    def test_compliance_level_map_byte_equal(self):
        """COMPLIANCE_LEVEL_MAP 4 项映射 byte-equal。"""
        assert COMPLIANCE_LEVEL_MAP == {
            "fully": ComplianceLevel.FULLY,
            "partially": ComplianceLevel.PARTIALLY,
            "not": ComplianceLevel.NOT,
            "missing": ComplianceLevel.MISSING,
        }

    def test_risk_level_map_byte_equal(self):
        """RISK_LEVEL_MAP 4 项映射 byte-equal。"""
        assert RISK_LEVEL_MAP == {
            "none": RiskLevel.NONE,
            "low": RiskLevel.LOW,
            "medium": RiskLevel.MEDIUM,
            "high": RiskLevel.HIGH,
        }

    def test_get_registry_namespace_patchable(self):
        """app.core.ai.reviewer.get_registry 必须可被 monkeypatch.setattr 替换。

        测试 test_bid_response_chunks.py 用 `patch("app.core.ai.reviewer.get_registry")`，
        拆分后该命名空间必须仍存在。
        """
        with patch("app.core.ai.reviewer.get_registry") as mock_get_registry:
            mock_get_registry.return_value = MagicMock()
            assert reviewer_module.get_registry is mock_get_registry

    def test_factory_returns_orchestrator_instance(self):
        """get_review_orchestrator 工厂返回 ReviewOrchestrator 实例。"""
        db = MagicMock()
        with patch("app.core.ai.reviewer.get_review_client") as mock_client:
            mock_client.return_value = MagicMock()
            orchestrator = get_review_orchestrator(db=db, project_id=123)
        assert isinstance(orchestrator, ReviewOrchestrator)
        assert orchestrator._project_id == 123


# ---------------------------------------------------------------------------
# B. ReviewOrchestrator.__init__ 必备实例字段
# ---------------------------------------------------------------------------


class TestInitFields:
    """B 类: __init__ 后实例必须具备的 9 个字段。"""

    def _build(self) -> ReviewOrchestrator:
        db = MagicMock()
        with patch("app.core.ai.reviewer.get_review_client") as mock_client:
            mock_client.return_value = MagicMock()
            return ReviewOrchestrator(db=db, project_id=42)

    def test_init_required_fields_present(self):
        """9 个 __init__ 字段 + 默认初始化的类内缓存。"""
        orch = self._build()
        for field in (
            "db",
            "_project_id",
            "llm_client",
            "max_retries",
            "item_timeout",
            "_retry_tracker",
            "_chunk_pages_cache",
            "_project_owner_id_cache",
            "_project_owner_id_loaded",
        ):
            assert hasattr(orch, field), f"__init__ 后实例缺失字段: {field}"

    def test_init_defaults(self):
        """默认 max_retries=3、owner_id 缓存初始为未加载状态。"""
        orch = self._build()
        assert orch.max_retries == 3
        assert orch._project_owner_id_cache is None
        assert orch._project_owner_id_loaded is False
        assert orch._chunk_pages_cache == {}


# ---------------------------------------------------------------------------
# C. ClassVar 常量内容（拆分后必须以 ClassVar re-export 形式保留主类访问）
# ---------------------------------------------------------------------------


class TestClassVars:
    """C 类: 4 个 ClassVar 常量内容 byte-equal。"""

    def test_gate_replay_cover_fields_18_byte_equal(self):
        """retry 覆盖字段白名单 18 项 byte-equal（/ Fix-09 防御）。"""
        expected = frozenset({
            "is_compliant",
            "compliance_level",
            "score",
            "comment",
            "risk_level",
            "relevance_score",
            "relevance_note",
            "consistency_flag",
            "reconcile_note",
            "evidence_relation",
            "attribution_type",
            "main_requirement_id",
            "fact_key",
            "canonical_key",
            "family_type",
            "response_summary",
            "requirement_summary",
            "failure_state",
            "failure_reason",
            "confidence",
            "gate_trace",
            "review_required",
        })
        assert ReviewOrchestrator._GATE_REPLAY_COVER_FIELDS == expected
        assert isinstance(ReviewOrchestrator._GATE_REPLAY_COVER_FIELDS, frozenset)

    def test_domain_keywords_subset(self):
        """跨类别证据索引关键词必须包含资质/合同等核心词。"""
        kw = ReviewOrchestrator._DOMAIN_KEYWORDS
        assert isinstance(kw, list)
        for must_have in ("业绩", "合同", "证书", "资质", "失信", "授权委托书", "审计报告"):
            assert must_have in kw, f"_DOMAIN_KEYWORDS 缺少: {must_have}"

    def test_missing_signals_subset(self):
        """缺失语义信号必须包含「未提供 / 缺失 / 未见」等核心句式。"""
        signals = ReviewOrchestrator._MISSING_SIGNALS
        assert isinstance(signals, list)
        for must_have in ("未提供", "未发现", "缺失", "未见", "未提交"):
            assert must_have in signals, f"_MISSING_SIGNALS 缺少: {must_have}"

    def test_generic_stopwords_subset(self):
        """领域关键词通用停用词必须包含「项目 / 文件 / 满足」等。"""
        stopwords = ReviewOrchestrator._GENERIC_STOPWORDS
        assert isinstance(stopwords, frozenset)
        for must_have in ("项目", "文件", "满足", "应当", "证明", "提供"):
            assert must_have in stopwords, f"_GENERIC_STOPWORDS 缺少: {must_have}"


# ---------------------------------------------------------------------------
# D. 43 个 _xxx 私有方法 + 3 个 public 入口稳定性
# ---------------------------------------------------------------------------


# 与 reviewer.py 当前 grep 一致的方法清单（外部测试通过 inspect.getsource / getattr
# 访问，拆分后必须保留同名 thin forwarder）。
PRIVATE_METHODS_EXPECTED = frozenset({
    # 编排闭环
    "_review_project_impl",
    "_get_existing_review_requirement_ids",
    "_get_default_timeout",
    # 证据预取
    "_prefetch_evidence",
    "_build_evidence_index",
    "_lookup_cross_category_evidence",
    "_get_project_owner_id",
    # 单对审查
    "_review_single_pair",
    "_try_pre_llm_skip",
    "_call_llm_review",
    "_fetch_image_evidence_for_pair",
    "_independent_evidence_search",
    "_collect_chunk_pages_for_pair",
    "_build_review_prompt",
    "_build_no_response_prompt",
    "_resolve_cd_schema",
    # CoVe / Judge
    "_maybe_run_cove_investigate",
    "_maybe_run_judge_investigate",
    # 结果构造
    "_create_review_result",
    "_create_failure_result",
    "_create_failure_result_safe",
    "_compute_algorithm_evidence",
    "_maybe_enforce_algorithmic_risk_band",
    "_summarize_text",
    # 事实图
    "_build_fact_graph",
    "_judge_fact_then_project",
    "_select_fact_primary_judgement",
    "_derive_evidence_based_key",
    "_compute_evidence_relation",
    "_parse_source_pages",
    "_extract_anchor_tokens",
    # 一致性后检查
    "_post_review_consistency_check",
    "_estimate_keyword_relevance",
    "_extract_domain_keywords",
    # family rules
    "_apply_family_rules",
    "_detect_family_type",
    "_extract_missing_keywords",
    "_evidence_covers_missing",
    "_normalize_view_category",
    "_derive_attribution_type",
})


class TestMethodInventory:
    """D 类: 类方法稳定性（getattr 可定位）。"""

    def test_all_private_methods_present(self):
        """40 个 _xxx 私有方法名稳定（部分允许迁出至子模块但主类必须保留 forwarder）。"""
        missing = {
            name for name in PRIVATE_METHODS_EXPECTED
            if not hasattr(ReviewOrchestrator, name)
        }
        assert not missing, f"ReviewOrchestrator 缺失私有方法: {sorted(missing)}"

    def test_public_entry_points_present(self):
        """3 个 public 入口 + 工厂函数稳定。"""
        for name in ("__init__", "review_project", "summarize_results"):
            assert hasattr(ReviewOrchestrator, name)
        assert callable(get_review_orchestrator)


# ---------------------------------------------------------------------------
# E. 失败合同 byte-equal（BUG 防御 + 任务恢复依赖）
# ---------------------------------------------------------------------------


def _make_pair(
    *,
    is_mandatory: bool = True,
    requirement_id: int = 1,
    response_id: Optional[int] = 2,
    has_response: bool = True,
    canonical_key: Optional[str] = "ck-1",
    main_requirement_id: Optional[int] = None,
) -> MatchedPair:
    """构造一个最小可用的 MatchedPair（不触发 ORM 加载）。

    MatchedPair 是 dataclass，仅含 `requirement` / `response` 字段；
    `requirement_id` / `response_id` / `category` / `is_mandatory` 都通过 property 派生。
    """
    requirement = MagicMock(spec=Requirement)
    requirement.id = requirement_id
    requirement.description = "投标人应具备建造业资质"
    requirement.canonical_key = canonical_key
    requirement.main_requirement_id = main_requirement_id
    requirement.is_mandatory = is_mandatory
    requirement.category = MagicMock()
    requirement.category.value = "QUAL"
    requirement.subject_scope = "bidder"

    response: Optional[BidResponse] = None
    if has_response:
        response = MagicMock(spec=BidResponse)
        response.id = response_id
        response.content = "投标人具备一级建造业资质"
        response.source_pages = "1,2"
        response.evidence_anchor = None

    return MatchedPair(requirement=requirement, response=response)


class TestFailureContract:
    """E 类: 失败兜底结果的 13 个字段语义 byte-equal。"""

    def _build_orch(self) -> ReviewOrchestrator:
        db = MagicMock()
        with patch("app.core.ai.reviewer.get_review_client") as mock_client:
            mock_client.return_value = MagicMock()
            return ReviewOrchestrator(db=db, project_id=42)

    def test_create_failure_result_fields_byte_equal(self):
        """_create_failure_result：高风险否决项字段稳定。"""
        orch = self._build_orch()
        pair = _make_pair(is_mandatory=True)
        result = orch._create_failure_result(project_id=42, pair=pair)
        assert result.project_id == 42
        assert result.requirement_id == 1
        assert result.bid_response_id == 2
        assert result.is_compliant == 0
        assert result.compliance_level == ComplianceLevel.MISSING
        assert result.score == 0
        assert result.risk_level == RiskLevel.HIGH
        assert "LLM_REVIEW_FAILURE" in result.comment
        assert result.canonical_key == "ck-1"
        assert result.fact_key == "ck-1"
        assert result.evidence_relation == "undetermined"
        assert result.consistency_flag == "unchecked"
        assert result.reconcile_note == "自动兜底结果，待复核"

    def test_create_failure_result_low_risk_for_non_mandatory(self):
        """非否决项的兜底风险等级降为 MEDIUM。"""
        orch = self._build_orch()
        pair = _make_pair(is_mandatory=False)
        result = orch._create_failure_result(project_id=42, pair=pair)
        assert result.risk_level == RiskLevel.MEDIUM

    def test_create_failure_result_safe_handles_response_detach(self):
        """_create_failure_result_safe 对 response.content 访问异常的容错。

        Safe 版本对 `_summarize_text(pair.response.content)` 调用用 try 包裹，
        异常时 response_summary 退化为 None 而不抛出。
        """
        orch = self._build_orch()
        pair = _make_pair(is_mandatory=True)
        # response.content 访问抛异常,模拟 DetachedInstanceError
        type(pair.response).content = property(
            lambda _self: (_ for _ in ()).throw(Exception("Detached"))
        )
        result = orch._create_failure_result_safe(
            project_id=99, requirement_id=1, response_id=2, pair=pair,
        )
        # 不应抛异常,response_summary 兜底为 None
        assert result.project_id == 99
        assert result.requirement_id == 1
        assert result.bid_response_id == 2
        assert result.response_summary is None
        assert result.is_compliant == 0
        assert result.compliance_level == ComplianceLevel.MISSING
        assert "LLM_REVIEW_FAILURE" in result.comment


# ---------------------------------------------------------------------------
# F. summarize_results 字段稳定
# ---------------------------------------------------------------------------


class TestSummarizeResults:
    """F 类: summarize_results 返回 dict 关键字段。"""

    def _build_orch(self) -> ReviewOrchestrator:
        db = MagicMock()
        with patch("app.core.ai.reviewer.get_review_client") as mock_client:
            mock_client.return_value = MagicMock()
            return ReviewOrchestrator(db=db, project_id=42)

    def test_summarize_empty_results(self):
        """空 results → total_count=0, conclusion=pending, conclusion_reason 文案稳定。"""
        orch = self._build_orch()
        summary = orch.summarize_results([])
        assert summary == {
            "total_count": 0,
            "conclusion": "pending",
            "conclusion_reason": "无审查结果",
        }

    def test_summarize_results_high_risk_items_field(self):
        """非空 results 返回字典必须包含 high_risk_items 字段（list 形态）。"""
        orch = self._build_orch()
        from app.models.review_result import ReviewResult

        rr = MagicMock(spec=ReviewResult)
        rr.is_compliant = 0
        rr.compliance_level = ComplianceLevel.MISSING
        rr.risk_level = RiskLevel.HIGH
        rr.score = 0
        rr.requirement = None
        summary = orch.summarize_results([rr])
        assert "high_risk_items" in summary
        assert isinstance(summary["high_risk_items"], list)
        assert len(summary["high_risk_items"]) == 1


# ---------------------------------------------------------------------------
# G. _review_project_impl 三条主线
# ---------------------------------------------------------------------------


class TestReviewProjectImplMainPaths:
    """G 类: 编排闭环关键路径。"""

    def _build_orch(self) -> ReviewOrchestrator:
        db = MagicMock()
        with patch("app.core.ai.reviewer.get_review_client") as mock_client:
            mock_client.return_value = MagicMock()
            return ReviewOrchestrator(db=db, project_id=42)

    @pytest.mark.asyncio
    async def test_empty_matched_pairs_returns_empty_and_post_check(self):
        """skip_existing=True + 全部已审查 → 短路返回 [] 且调用 _post_review_consistency_check。"""
        orch = self._build_orch()
        orch._get_existing_review_requirement_ids = AsyncMock(return_value={1, 2, 3})
        orch._post_review_consistency_check = AsyncMock()

        pairs = [
            _make_pair(requirement_id=1),
            _make_pair(requirement_id=2),
            _make_pair(requirement_id=3),
        ]
        result = await orch._review_project_impl(
            project_id=42,
            matched_pairs=pairs,
            save_to_db=True,
            skip_existing=True,
        )
        assert result == []
        orch._post_review_consistency_check.assert_awaited_once_with(42)

    @pytest.mark.asyncio
    async def test_no_pairs_short_circuit(self):
        """空 matched_pairs + save_to_db=True 同样触发 post_check 后短路。"""
        orch = self._build_orch()
        orch._get_existing_review_requirement_ids = AsyncMock(return_value=set())
        orch._post_review_consistency_check = AsyncMock()

        result = await orch._review_project_impl(
            project_id=42,
            matched_pairs=[],
            save_to_db=True,
            skip_existing=True,
        )
        assert result == []
        orch._post_review_consistency_check.assert_awaited_once_with(42)

    def test_retry_tracker_is_llm_retry_tracker_instance(self):
        """_retry_tracker 必须是 LLMRetryTracker 实例（retry 链路依赖）。"""
        from app.core.ai.retry_tracker import LLMRetryTracker

        orch = self._build_orch()
        assert isinstance(orch._retry_tracker, LLMRetryTracker)


# ---------------------------------------------------------------------------
# H. _post_review_consistency_check 调用链锚点
# ---------------------------------------------------------------------------


class TestPostCheckCallChain:
    """H 类: 一致性后检查的关键方法调用链（源码字面量锚定）。"""

    def test_post_check_source_contains_build_fact_graph_call(self):
        """_post_review_consistency_check 源码必须含 _build_fact_graph 调用。"""
        src = inspect.getsource(ReviewOrchestrator._post_review_consistency_check)
        assert "_build_fact_graph" in src

    def test_post_check_source_contains_judge_fact_then_project_call(self):
        """_post_review_consistency_check 源码必须含 _judge_fact_then_project 调用。"""
        src = inspect.getsource(ReviewOrchestrator._post_review_consistency_check)
        assert "_judge_fact_then_project" in src


# ---------------------------------------------------------------------------
# I. inspect.getsource 锚点字符串（test_critic_quality_fix 静态契约）
# ---------------------------------------------------------------------------


class TestInspectSourceAnchors:
    """I 类: 外部测试通过 inspect.getsource 获取源码做静态字面量检查。"""

    def test_call_llm_review_source_obtainable(self):
        """ReviewOrchestrator._call_llm_review 必须可被 inspect.getsource 获取。"""
        src = inspect.getsource(ReviewOrchestrator._call_llm_review)
        # 源码必须含 max_retries 引用（critic_quality_fix 测试依赖）
        assert "max_retries" in src

    def test_resolve_cd_schema_source_obtainable(self):
        """ReviewOrchestrator._resolve_cd_schema 必须可被 inspect.getsource 获取。"""
        src = inspect.getsource(ReviewOrchestrator._resolve_cd_schema)
        assert "def _resolve_cd_schema" in src

    def test_compute_algorithm_evidence_static_callable(self):
        """ReviewOrchestrator._compute_algorithm_evidence 必须可类路径静态调用。

        test_reviewer_algorithm_evidence 直接调用 `ReviewOrchestrator._compute_algorithm_evidence(pair)`，
        拆分后类路径必须保留。
        """
        method = inspect.getattr_static(ReviewOrchestrator, "_compute_algorithm_evidence")
        # @staticmethod 装饰器下，getattr_static 返回 staticmethod 包装对象
        assert isinstance(method, staticmethod) or callable(method)


# ---------------------------------------------------------------------------
# J. _get_review_prompts 函数三元组返回
# ---------------------------------------------------------------------------


class TestGetReviewPrompts:
    """J 类: 模块级 _get_review_prompts 必须保留三元组返回结构。"""

    def test_get_review_prompts_returns_triple(self):
        """_get_review_prompts() 返回 (system, user_tpl, no_resp_tpl)。"""
        # 不 patch registry, 走真实 fallback 路径
        result = _get_review_prompts()
        assert isinstance(result, tuple)
        assert len(result) == 3
        for item in result:
            assert isinstance(item, str)
            assert item, "prompt fallback 内容不应为空"
