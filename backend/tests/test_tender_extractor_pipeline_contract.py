"""TenderExtractor 拆分前 Phase 1 快照契约测试（R2-2）

锁定 6 类契约，作为后续按业务阶段拆分到 tender_extractor_parts/ 子包时的"行为锚点"：

| 类别 | 用例数 | 锁定内容 |
|---|---|---|
| A. 模块级出口契约 | 4 | TenderExtractor / get_tender_extractor / 4 个 infer_* / CATEGORY_MAP / 55 个 private method |
| B. 单轮合并契约 | 3 | _merge_category 路由 / _merge_category_single_pass LLM 调用 / 失败回退 + fallback_events 写入 |
| C. 分批合并契约 | 3 | _merge_category 分批路由 / _build_category_batches / _select_category_high_risk_indices 优先级 |
| D. 二次合并契约 | 3 | _estimate_secondary_max_tokens / _normalize_merge_decision_response / _reduce_secondary_input_for_retry |
| E. 跨类去重契约 | 2 | _is_similar 默认阈值 / _handle_local_issues 按 canonical_key+subject_scope 分组保留最长 |
| F. 失败/精度元数据契约 | 3 | _record_stage_failure / _record_fallback / _build_precision_meta 6 键 |

注：本测试通过 importlib + 假 app.core.ai 模块加载主文件，避免触发完整 AI 栈，
与 test_tender_extractor_merge.py / _precision_guard.py / _category_merge.py 加载模式一致。
"""

from __future__ import annotations

import inspect
import os
import sys
import types
from pathlib import Path

import importlib.util
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://user:pass@localhost:5432/testdb",
)

fake_ai_pkg = types.ModuleType("app.core.ai")
fake_ai_pkg.__path__ = []
fake_ai_pkg.get_review_client = lambda **kwargs: None
fake_ai_pkg.LLMClient = object

fake_prompts_pkg = types.ModuleType("app.core.ai.prompts")
fake_prompts_pkg.__path__ = []

fake_tender_prompts = types.ModuleType("app.core.ai.prompts.tender_extract")
fake_tender_prompts.get_tender_extract_prompt = lambda **kwargs: ("system", "user")
fake_tender_prompts.get_category_merge_prompt = lambda **kwargs: ("system", "user")
fake_tender_prompts.get_category_merge_decision_prompt = lambda **kwargs: ("system", "user")
fake_tender_prompts.get_reflection_prompt = lambda **kwargs: ("system", "user")
fake_tender_prompts.get_secondary_merge_prompt = lambda **kwargs: ("system", "user")
fake_tender_prompts.get_secondary_merge_decision_prompt = lambda **kwargs: ("system", "user")

sys.modules.setdefault("app.core.ai", fake_ai_pkg)
sys.modules.setdefault("app.core.ai.prompts", fake_prompts_pkg)
sys.modules.setdefault("app.core.ai.prompts.tender_extract", fake_tender_prompts)

from app.core.config import settings
from app.models.requirement import RequirementCategory
from app.schemas.requirement import ExtractedRequirement

_MODULE_PATH = Path(__file__).parent.parent / "app" / "core" / "extractor" / "tender_extractor.py"
_SPEC = importlib.util.spec_from_file_location(
    "tender_extractor_pipeline_contract_under_test", _MODULE_PATH
)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_MODULE)

TenderExtractor = _MODULE.TenderExtractor
get_tender_extractor = _MODULE.get_tender_extractor
CATEGORY_MAP = _MODULE.CATEGORY_MAP
infer_subject_scope = _MODULE.infer_subject_scope
infer_topic = _MODULE.infer_topic
extract_fact_signature = _MODULE.extract_fact_signature
infer_canonical_key = _MODULE.infer_canonical_key


# 拆分后必须仍然全部存在于 TenderExtractor 类上的 55 个 private/public method
# （S-11 删除死方法 extract_from_project 后由 56 减为 55）
EXPECTED_METHODS = (
    # __init__ + 监控辅助 (10)
    "__init__",
    "_report_progress",
    "_record_stage_failure",
    "_record_fallback",
    "_sanitize_text",
    "_sanitize_requirements_for_prompt",
    "_merge_issue_lists",
    "_build_precision_meta",
    "_should_emit_baseline_snapshot",
    "_emit_precision_baseline_snapshot",
    # 主流程 + 文档加载 (3)
    "extract_from_document",
    "_get_tender_document",
    "_get_document_chunks",
    # Chunk 提取 + salvage (8)
    "_normalize_raw_item",
    "_infer_category_from_content",
    "_summarize_invalid_raw_item",
    "_parse_items_with_salvage",
    "_merge_provenance",
    "_align_chunk_source_pages",
    "_extract_from_chunk",
    "_local_pre_dedupe",
    # 合并编排 (1)
    "_merge_requirements",
    # 类别合并三套 (6)
    "_merge_category",
    "_estimate_category_max_tokens",
    "_build_category_batches",
    "_select_category_high_risk_indices",
    "_merge_category_single_pass",
    "_merge_category_batched",
    # 跨类去重 + 质量检查 (2)
    "_cross_category_dedup",
    "_check_quality",
    # 二次合并三套 + 工具 (13)
    "_secondary_merge",
    "_estimate_secondary_max_tokens",
    "_is_json_like_error",
    "_is_truncation_error",
    "_parse_truncation_char_pos",
    "_normalize_secondary_response",
    "_apply_merge_decisions",
    "_normalize_merge_decision_response",
    "_build_secondary_batches_by_budget",
    "_build_secondary_batches",
    "_reduce_secondary_input_for_retry",
    "_secondary_merge_single_pass",
    "_secondary_merge_batched",
    # 本地去重 + 兜底 (3)
    "_handle_local_issues",
    "_simple_dedupe",
    "_is_similar",
    # 推断字段 (5)
    "_infer_subject_scope",
    "_infer_topic",
    "_extract_fact_signature",
    "_infer_canonical_key",
    "_infer_facet_scope",
    # 拆分 + 业绩对齐 (2)
    "_split_compound_requirements",
    "_apply_performance_alignment",
    # 持久化 (1)
    "_save_requirements",
)


class FakeLLMClient:
    def __init__(self, scripted_results=None):
        self.scripted_results = list(scripted_results or [])
        self.calls = []

    async def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        if not self.scripted_results:
            raise RuntimeError("missing scripted result")
        current = self.scripted_results.pop(0)
        if isinstance(current, Exception):
            raise current
        return current


def _build_requirement(idx: int, category: str = "QUAL", description: str = "") -> ExtractedRequirement:
    return ExtractedRequirement(
        category=category,
        description=description or f"测试要求-{category}-{idx}",
        is_mandatory=bool(idx % 2),
        keywords=[f"kw{idx}"],
    )


# ---------------------------------------------------------------------------
# A. 模块级出口契约
# ---------------------------------------------------------------------------


def test_A1_module_level_symbols_importable():
    """A1: TenderExtractor / get_tender_extractor / CATEGORY_MAP 主模块级符号可访问。

    拆分后这些符号必须仍从 app.core.extractor.tender_extractor 直接 import。
    """
    assert inspect.isclass(TenderExtractor)
    assert callable(get_tender_extractor)
    assert isinstance(CATEGORY_MAP, dict)


def test_A2_four_inference_functions_callable():
    """A2: 4 个模块级推断函数签名、可调用性、返回类型契约。

    requirement_service.py:329 lazy import 这 4 个函数用于编辑后重算。
    """
    scope = infer_subject_scope("投标人须持有营业执照", ["营业执照"])
    assert scope in {"enterprise", "personnel", "both", "generic"}

    topic = infer_topic("项目经理须有10年经验", ["项目经理"])
    assert isinstance(topic, str)
    assert len(topic) > 0

    sig = extract_fact_signature("合同金额不少于50万元的信息化项目业绩")
    assert isinstance(sig, str)

    canonical = infer_canonical_key("营业执照A级", ["营业执照"])
    assert isinstance(canonical, str)
    assert len(canonical) > 0


def test_A3_category_map_has_all_six_categories():
    """A3: CATEGORY_MAP 含 6 个分类映射到 RequirementCategory 枚举。"""
    assert set(CATEGORY_MAP.keys()) == {"QUAL", "TECH", "COMM", "PERF", "STAFF", "FORMAT"}
    for code, enum_val in CATEGORY_MAP.items():
        assert isinstance(enum_val, RequirementCategory), f"{code} -> {enum_val}"


def test_A4_all_expected_methods_exist_on_class():
    """A4: 55 个 private/public method 全部存在于 TenderExtractor 类上。

    拆分后这些方法必须保留为 thin forwarder（用于外部测试 monkey patch 锚点）。
    """
    missing = [name for name in EXPECTED_METHODS if not hasattr(TenderExtractor, name)]
    assert not missing, f"以下方法在 TenderExtractor 类上缺失: {missing}"


# ---------------------------------------------------------------------------
# B. 单轮合并契约
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_B1_merge_category_routes_to_single_pass_when_small(monkeypatch):
    """B1: _merge_category 当输入条数 < batch_trigger 时路由到 _merge_category_single_pass。"""
    monkeypatch.setattr(settings, "TENDER_CATEGORY_BATCH_TRIGGER", 99)

    extractor = TenderExtractor(db=None, llm_client=FakeLLMClient([]))

    called = {"single": 0, "batched": 0}

    async def fake_single(category, requirements, **kwargs):
        called["single"] += 1
        return list(requirements)

    async def fake_batched(category, requirements, *, on_batch_progress=None):
        called["batched"] += 1
        return list(requirements)

    extractor._merge_category_single_pass = fake_single
    extractor._merge_category_batched = fake_batched

    reqs = [_build_requirement(i, "QUAL") for i in range(3)]
    result = await extractor._merge_category("QUAL", reqs)
    assert called["single"] == 1
    assert called["batched"] == 0
    assert len(result) == 3


@pytest.mark.asyncio
async def test_B2_merge_category_single_pass_returns_extracted_requirement_list():
    """B2: _merge_category_single_pass 成功路径返回 ExtractedRequirement 列表。"""
    fake_llm = FakeLLMClient(
        scripted_results=[
            [
                {
                    "category": "QUAL",
                    "description": "合并后描述A",
                    "is_mandatory": True,
                    "keywords": ["A"],
                }
            ]
        ]
    )
    extractor = TenderExtractor(db=None, llm_client=fake_llm)

    reqs = [_build_requirement(1, "QUAL"), _build_requirement(2, "QUAL")]
    merged = await extractor._merge_category_single_pass(
        category="QUAL",
        requirements=reqs,
        final_pass=False,
        allow_retry=False,
        pass_label="contract-B2",
    )
    assert merged is not None
    assert isinstance(merged, list)
    assert all(isinstance(r, ExtractedRequirement) for r in merged)
    assert len(fake_llm.calls) == 1


@pytest.mark.asyncio
async def test_B3_merge_category_falls_back_to_simple_dedupe_on_failure(monkeypatch):
    """B3: _merge_category single_pass 返回 None 时回退 _simple_dedupe 并写 fallback_events。

 防御：fallback_events 必须含 stage='category_merge' + category + input_count。
 """
    monkeypatch.setattr(settings, "TENDER_CATEGORY_BATCH_TRIGGER", 99)

    extractor = TenderExtractor(db=None, llm_client=FakeLLMClient([]))

    async def fake_single(**kwargs):
        return None

    fallback_called = {"count": 0}
    original_simple = extractor._simple_dedupe

    def tracking_simple(requirements):
        fallback_called["count"] += 1
        return original_simple(requirements)

    extractor._merge_category_single_pass = fake_single
    extractor._simple_dedupe = tracking_simple

    reqs = [_build_requirement(i, "QUAL") for i in range(3)]
    await extractor._merge_category("QUAL", reqs)
    assert fallback_called["count"] == 1
    fallback_events = extractor._fallback_events
    assert any(
        ev["stage"] == "category_merge" and ev.get("category") == "QUAL"
        for ev in fallback_events
    )


# ---------------------------------------------------------------------------
# C. 分批合并契约
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_C1_merge_category_routes_to_batched_when_large(monkeypatch):
    """C1: _merge_category 当输入条数 >= batch_trigger 时路由到 _merge_category_batched。"""
    monkeypatch.setattr(settings, "TENDER_CATEGORY_BATCH_TRIGGER", 3)

    extractor = TenderExtractor(db=None, llm_client=FakeLLMClient([]))

    called = {"single": 0, "batched": 0}

    async def fake_single(**kwargs):
        called["single"] += 1
        return []

    async def fake_batched(category, requirements, *, on_batch_progress=None):
        called["batched"] += 1
        return list(requirements)

    extractor._merge_category_single_pass = fake_single
    extractor._merge_category_batched = fake_batched

    reqs = [_build_requirement(i, "QUAL") for i in range(5)]
    result = await extractor._merge_category("QUAL", reqs)
    assert called["batched"] == 1
    assert called["single"] == 0
    assert len(result) == 5


def test_C2_build_category_batches_respects_batch_size(monkeypatch):
    """C2: _build_category_batches 按 TENDER_CATEGORY_BATCH_SIZE 切分。"""
    monkeypatch.setattr(settings, "TENDER_CATEGORY_BATCH_SIZE", 3)

    extractor = TenderExtractor(db=None, llm_client=FakeLLMClient([]))
    reqs = [_build_requirement(i, "QUAL") for i in range(7)]
    batches = extractor._build_category_batches(reqs)
    assert len(batches) == 3
    assert sum(len(b) for b in batches) == 7
    assert all(len(b) <= 3 for b in batches)


def test_C3_select_high_risk_indices_keeps_at_least_six():
    """C3: _select_category_high_risk_indices 至少保留 6 条；≤6 时全保留，>6 时按打分裁剪到 0.7 比例。"""
    extractor = TenderExtractor(db=None, llm_client=FakeLLMClient([]))

    small = [_build_requirement(i, "QUAL") for i in range(5)]
    assert extractor._select_category_high_risk_indices("QUAL", small) == list(range(5))

    large = [_build_requirement(i, "QUAL") for i in range(20)]
    indices = extractor._select_category_high_risk_indices("QUAL", large)
    assert isinstance(indices, list)
    assert len(indices) >= 6
    assert all(0 <= idx < len(large) for idx in indices)
    assert len(indices) == len(set(indices))


# ---------------------------------------------------------------------------
# D. 二次合并契约
# ---------------------------------------------------------------------------


def test_D1_estimate_secondary_max_tokens_returns_positive_int():
    """D1: _estimate_secondary_max_tokens 返回正整数（决定 LLM 输出 token 上限）。"""
    extractor = TenderExtractor(db=None, llm_client=FakeLLMClient([]))
    reqs = [_build_requirement(i, "QUAL") for i in range(8)]
    result = extractor._estimate_secondary_max_tokens(reqs)
    assert isinstance(result, int)
    assert result > 0


def test_D2_normalize_merge_decision_response_passthrough_empty_list():
    """D2: _normalize_merge_decision_response 输入空列表时返回原始输入（兜底契约）。"""
    extractor = TenderExtractor(db=None, llm_client=FakeLLMClient([]))
    reqs = [_build_requirement(i, "QUAL") for i in range(2)]
    normalized = extractor._normalize_merge_decision_response([], original_requirements=reqs)
    assert isinstance(normalized, list)
    assert len(normalized) == len(reqs)


def test_D3_reduce_secondary_input_returns_priority_and_rest_tuple():
    """D3: _reduce_secondary_input_for_retry 返回 (priority, rest, reduced) 三元组。"""
    extractor = TenderExtractor(db=None, llm_client=FakeLLMClient([]))
    reqs = [_build_requirement(i, "QUAL") for i in range(10)]
    issues = [{"type": "keyword_excess", "affected_indices": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]}]
    result = extractor._reduce_secondary_input_for_retry(reqs, issues, attempt=1)
    assert isinstance(result, tuple)
    assert len(result) == 3
    priority, rest, reduced = result
    assert isinstance(priority, list)
    assert isinstance(rest, list)
    assert isinstance(reduced, bool)


# ---------------------------------------------------------------------------
# E. 跨类去重 / 本地去重契约
# ---------------------------------------------------------------------------


def test_E1_is_similar_default_threshold_is_zero_point_five():
    """E1: _is_similar 默认阈值=0.5（_simple_dedupe 调用时使用）。"""
    extractor = TenderExtractor(db=None, llm_client=FakeLLMClient([]))
    sig = inspect.signature(extractor._is_similar)
    threshold = sig.parameters["threshold"].default
    assert threshold == 0.5


def test_E2_handle_local_issues_keeps_longest_in_group():
    """E2: _handle_local_issues 按 canonical_key+subject_scope 分组保留最长描述。

    直接设置同 canonical_key + subject_scope，确保两条进入同一分组以触发分组保留逻辑。
    """
    extractor = TenderExtractor(db=None, llm_client=FakeLLMClient([]))

    short = ExtractedRequirement(
        category="QUAL",
        description="持有营业执照证书",
        is_mandatory=True,
        keywords=["营业执照"],
        canonical_key="qualification:fixed_key",
        subject_scope="enterprise",
    )
    long_req = ExtractedRequirement(
        category="QUAL",
        description="投标人须持有有效的营业执照证书，经营范围覆盖招标项目要求",
        is_mandatory=True,
        keywords=["营业执照"],
        canonical_key="qualification:fixed_key",
        subject_scope="enterprise",
    )
    issues = [{"type": "duplicate", "affected_indices": [0, 1]}]
    result = extractor._handle_local_issues([short, long_req], issues)
    descriptions = {r.description for r in result}
    assert long_req.description in descriptions
    assert short.description not in descriptions


# ---------------------------------------------------------------------------
# F. 失败/精度元数据契约（防御）
# ---------------------------------------------------------------------------


def test_F1_record_stage_failure_writes_full_meta():
    """F1: _record_stage_failure 写入 _stage_failures 含 stage + reason + 自定义 meta。

 防御：chunk_extraction 失败必须保留 chunk_id / page_num 供二次补救识别。
 """
    extractor = TenderExtractor(db=None, llm_client=FakeLLMClient([]))
    extractor._record_stage_failure(
        "chunk_extraction",
        "LLM 调用超时",
        chunk_id=42,
        page_num=7,
    )
    assert len(extractor._stage_failures) == 1
    entry = extractor._stage_failures[0]
    assert entry["stage"] == "chunk_extraction"
    assert entry["reason"] == "LLM 调用超时"
    assert entry["chunk_id"] == 42
    assert entry["page_num"] == 7


def test_F2_record_fallback_writes_full_meta():
    """F2: _record_fallback 写入 _fallback_events 含 stage + reason + 自定义 meta。"""
    extractor = TenderExtractor(db=None, llm_client=FakeLLMClient([]))
    extractor._record_fallback(
        "category_merge",
        "category_single_failed",
        category="TECH",
        input_count=5,
    )
    assert len(extractor._fallback_events) == 1
    entry = extractor._fallback_events[0]
    assert entry["stage"] == "category_merge"
    assert entry["reason"] == "category_single_failed"
    assert entry["category"] == "TECH"
    assert entry["input_count"] == 5


def test_F3_build_precision_meta_returns_six_required_keys():
    """F3: _build_precision_meta 返回 6 个键且类型符合契约。

 防御：前端 RequirementReview 依赖 mandatory_ratio / category_distribution
 等字段；fallback_path / stage_failures 暴露给精度闸门日志。
 """
    extractor = TenderExtractor(db=None, llm_client=FakeLLMClient([]))
    extractor._record_stage_failure("chunk_extraction", "x", chunk_id=1)
    extractor._record_fallback("category_merge", "x", category="QUAL")

    reqs = [
        _build_requirement(1, "QUAL"),
        _build_requirement(2, "TECH"),
        _build_requirement(3, "QUAL"),
    ]
    meta = extractor._build_precision_meta(reqs, duplicate_issues=[])
    expected_keys = {
        "total",
        "mandatory_ratio",
        "category_distribution",
        "duplicate_signals",
        "fallback_path",
        "stage_failures",
    }
    assert set(meta.keys()) == expected_keys
    assert meta["total"] == 3
    assert isinstance(meta["mandatory_ratio"], float)
    assert isinstance(meta["category_distribution"], dict)
    assert isinstance(meta["duplicate_signals"], dict)
    assert isinstance(meta["fallback_path"], list)
    assert isinstance(meta["stage_failures"], list)
    assert len(meta["fallback_path"]) == 1
    assert len(meta["stage_failures"]) == 1
