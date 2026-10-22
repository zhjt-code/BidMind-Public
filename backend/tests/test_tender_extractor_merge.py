"""TenderExtractor 二次合并稳态链路测试"""

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

# 测试环境最小化配置，避免导入Settings时报缺失
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://user:pass@localhost:5432/testdb",
)

# TenderExtractor 仅需少量AI接口，测试时注入最小桩模块避免加载完整AI栈
fake_ai_pkg = types.ModuleType("app.core.ai")
fake_ai_pkg.__path__ = []
fake_ai_pkg.get_review_client = lambda **kwargs: None  # 生产签名为 get_review_client(project_id=None)，桩须吃下 kwargs
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

sys.modules["app.core.ai"] = fake_ai_pkg
sys.modules["app.core.ai.prompts"] = fake_prompts_pkg
sys.modules["app.core.ai.prompts.tender_extract"] = fake_tender_prompts

from app.core.config import settings
from app.schemas.requirement import ExtractedRequirement

_MODULE_PATH = Path(__file__).parent.parent / "app" / "core" / "extractor" / "tender_extractor.py"
_SPEC = importlib.util.spec_from_file_location("tender_extractor_under_test", _MODULE_PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_MODULE)
TenderExtractor = _MODULE.TenderExtractor


class FakeLLMClient:
    """可编排返回结果的LLM桩。"""

    def __init__(self, scripted_results):
        self.scripted_results = list(scripted_results)
        self.calls = []

    async def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        if not self.scripted_results:
            raise RuntimeError("missing scripted result")

        current = self.scripted_results.pop(0)
        if isinstance(current, Exception):
            raise current
        return current


def build_requirement(index: int, category: str = "QUAL") -> ExtractedRequirement:
    return ExtractedRequirement(
        category=category,
        description=f"测试要求描述-{index}",
        is_mandatory=bool(index % 2),
        keywords=[f"关键词{index}"],
    )


def requirement_to_dict(req: ExtractedRequirement) -> dict:
    return req.model_dump()


@pytest.mark.asyncio
async def test_secondary_merge_single_pass_json_retry_success(monkeypatch):
    monkeypatch.setattr(settings, "TENDER_SECONDARY_JSON_RETRY", 1)
    monkeypatch.setattr(settings, "TENDER_SECONDARY_MAX_TOKENS", 12000)

    requirements = [build_requirement(1), build_requirement(2)]
    issues = [{"type": "keyword_excess", "affected_indices": [0, 1]}]

    fake_llm = FakeLLMClient(
        [
            json.JSONDecodeError("bad json", "{}", 1),
            [requirement_to_dict(requirements[0])],
        ]
    )
    extractor = TenderExtractor(db=None, llm_client=fake_llm)

    merged = await extractor._secondary_merge_single_pass(
        requirements=requirements,
        issues=issues,
        allow_retry=True,
        pass_label="unit-test",
    )

    assert merged is not None
    assert len(merged) == 1
    assert len(fake_llm.calls) == 2
    assert fake_llm.calls[1]["temperature"] == 0.0
    assert "输出格式强约束" in fake_llm.calls[1]["user_message"]


@pytest.mark.asyncio
async def test_secondary_merge_fallback_after_retry_failure(monkeypatch):
    monkeypatch.setattr(settings, "TENDER_SECONDARY_JSON_RETRY", 1)
    monkeypatch.setattr(settings, "TENDER_SECONDARY_BATCH_TRIGGER", 99)

    requirements = [
        ExtractedRequirement(
            category="PERF",
            description="投标人提供2023年信息化项目合同业绩，金额50万元",
            is_mandatory=True,
            keywords=["业绩", "合同", "投标人"],
        ),
        ExtractedRequirement(
            category="STAFF",
            description="项目经理在同项目中担任项目经理并提供任职证明",
            is_mandatory=True,
            keywords=["项目经理", "业绩", "任职证明"],
        ),
        build_requirement(3, "TECH"),
    ]
    issues = [{"type": "cross_category_duplicate", "affected_indices": [0, 1, 2]}]

    fake_llm = FakeLLMClient(
        [
            json.JSONDecodeError("bad json", "{}", 1),
            json.JSONDecodeError("still bad", "{}", 2),
        ]
    )
    extractor = TenderExtractor(db=None, llm_client=fake_llm)

    merged = await extractor._secondary_merge(requirements, issues)

    # 本地回退应执行语义保留，不再粗暴“只留第一条”
    assert len(merged) >= 2
    descriptions = {m.description for m in merged}
    assert requirements[0].description in descriptions
    assert requirements[1].description in descriptions or requirements[2].description in descriptions


@pytest.mark.asyncio
async def test_performance_alignment_marks_subject_scope_and_canonical():
    req_enterprise = ExtractedRequirement(
        category="PERF",
        description="投标人须提供2023-2024年信息化施工合同业绩证明，合同金额不少于50万元",
        is_mandatory=True,
        keywords=["业绩", "合同", "50万元", "投标人"],
    )
    req_personnel = ExtractedRequirement(
        category="STAFF",
        description="项目经理在上述信息化施工项目中担任项目经理职务，并提供任职证明",
        is_mandatory=True,
        keywords=["项目经理", "业绩", "任职证明", "合同"],
    )

    extractor = TenderExtractor(db=None, llm_client=FakeLLMClient([]))
    aligned = extractor._apply_performance_alignment([req_enterprise, req_personnel])

    assert len(aligned) == 2
    assert aligned[0].subject_scope in {"enterprise", "both"}
    assert aligned[1].subject_scope in {"personnel", "both"}
    assert aligned[0].canonical_key is not None
    assert aligned[1].canonical_key is not None


@pytest.mark.asyncio
async def test_build_secondary_batches_prioritizes_issue_indices(monkeypatch):
    monkeypatch.setattr(settings, "TENDER_SECONDARY_BATCH_SIZE", 3)

    requirements = [build_requirement(i, "QUAL") for i in range(8)]
    issues = [
        {"type": "keyword_excess", "affected_indices": [6, 1, 4]},
        {"type": "cross_category_duplicate", "affected_indices": [4, 2]},
    ]

    extractor = TenderExtractor(db=None, llm_client=FakeLLMClient([]))
    batches = extractor._build_secondary_batches(requirements, issues)

    assert len(batches) == 3
    first_batch_reqs, first_batch_issues = batches[0]
    first_batch_desc = [item.description for item in first_batch_reqs]

    # issue中的优先索引应优先进入首批
    assert requirements[6].description in first_batch_desc
    assert requirements[1].description in first_batch_desc
    assert requirements[4].description in first_batch_desc
    assert first_batch_issues


@pytest.mark.asyncio
async def test_secondary_merge_batched_runs_final_pass(monkeypatch):
    monkeypatch.setattr(settings, "TENDER_SECONDARY_BATCH_TRIGGER", 5)
    monkeypatch.setattr(settings, "TENDER_SECONDARY_BATCH_SIZE", 3)
    monkeypatch.setattr(settings, "TENDER_SECONDARY_JSON_RETRY", 0)
    monkeypatch.setattr(settings, "TENDER_SECONDARY_FINAL_PASS_MAX_TOKENS", 6000)

    requirements = [build_requirement(i, "QUAL") for i in range(6)]
    issues = [{"type": "keyword_excess", "affected_indices": [0, 1, 2, 3, 4, 5]}]

    # 2个批次 + 1个final-pass（通过mock _check_quality强制触发最终收敛轮）
    fake_llm = FakeLLMClient(
        [
            [
                {
                    "category": "QUAL",
                    "description": "ALPHA-RESULT-0001",
                    "is_mandatory": True,
                    "keywords": ["k1"],
                },
                {
                    "category": "QUAL",
                    "description": "BETA-RESULT-0002",
                    "is_mandatory": True,
                    "keywords": ["k2"],
                },
                {
                    "category": "QUAL",
                    "description": "GAMMA-RESULT-0003",
                    "is_mandatory": True,
                    "keywords": ["k3"],
                },
            ],
            [
                {
                    "category": "COMM",
                    "description": "DELTA-RESULT-0004",
                    "is_mandatory": False,
                    "keywords": ["k4"],
                },
                {
                    "category": "COMM",
                    "description": "EPSILON-RESULT-0005",
                    "is_mandatory": False,
                    "keywords": ["k5"],
                },
            ],
            [requirement_to_dict(build_requirement(103))],
        ]
    )
    extractor = TenderExtractor(db=None, llm_client=fake_llm)
    # 强制走固定分桶（绕过智能分桶把所有条目合入单桶的问题）
    monkeypatch.setattr(
        extractor,
        "_build_secondary_batches_by_budget",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("force fallback")),
    )
    _quality_call_count = 0

    def _mock_check_quality(_reqs):
        nonlocal _quality_call_count
        _quality_call_count += 1
        if _quality_call_count == 1:
            return [{"type": "keyword_excess", "affected_indices": [0, 1]}]
        return []

    monkeypatch.setattr(extractor, "_check_quality", _mock_check_quality)

    merged = await extractor._secondary_merge(requirements, issues)

    assert len(fake_llm.calls) == 3
    assert any(r.description == "测试要求描述-103" for r in merged)


@pytest.mark.asyncio
async def test_secondary_merge_final_pass_gate_to_batched(monkeypatch):
    monkeypatch.setattr(settings, "TENDER_SECONDARY_BATCH_TRIGGER", 4)
    monkeypatch.setattr(settings, "TENDER_SECONDARY_BATCH_SIZE", 2)
    monkeypatch.setattr(settings, "TENDER_FINAL_PASS_SINGLE_MAX_INPUT", 2)
    monkeypatch.setattr(settings, "TENDER_FINAL_PASS_BATCH_SIZE", 2)
    monkeypatch.setattr(settings, "TENDER_SECONDARY_JSON_RETRY", 0)

    requirements = [build_requirement(i, "QUAL") for i in range(8)]
    issues = [{"type": "keyword_excess", "affected_indices": list(range(8))}]

    # 初始二次合并4批（每批2进1出） + final-pass分批2批
    fake_llm = FakeLLMClient(
        [
            [requirement_to_dict(build_requirement(100))],
            [requirement_to_dict(build_requirement(101))],
            [requirement_to_dict(build_requirement(102))],
            [requirement_to_dict(build_requirement(103))],
            [requirement_to_dict(build_requirement(104))],
            [requirement_to_dict(build_requirement(105))],
        ]
    )
    extractor = TenderExtractor(db=None, llm_client=fake_llm)
    monkeypatch.setattr(
        extractor,
        "_build_secondary_batches_by_budget",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("force fallback")),
    )
    monkeypatch.setattr(
        extractor,
        "_check_quality",
        lambda _reqs: [{"type": "keyword_excess", "affected_indices": [0, 1, 2, 3]}],
    )

    merged = await extractor._secondary_merge(requirements, issues)

    assert len(fake_llm.calls) == 6
    assert len(merged) >= 1
