"""TenderExtractor 分类合并稳态测试"""

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://user:pass@localhost:5432/testdb",
)

# 注入最小 AI stub，避免加载完整运行时依赖
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
_SPEC = importlib.util.spec_from_file_location("tender_extractor_category_under_test", _MODULE_PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_MODULE)
TenderExtractor = _MODULE.TenderExtractor


class FakeLLMClient:
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


def build_requirement(index: int, category: str = "TECH") -> ExtractedRequirement:
    return ExtractedRequirement(
        category=category,
        description=f"分类合并测试要求-{index}",
        is_mandatory=bool(index % 2),
        keywords=[f"k{index}"],
    )


def req_to_dict(req: ExtractedRequirement) -> dict:
    return req.model_dump()


@pytest.mark.asyncio
async def test_category_merge_single_pass_retry_success(monkeypatch):
    monkeypatch.setattr(settings, "TENDER_CATEGORY_JSON_RETRY", 1)
    monkeypatch.setattr(settings, "TENDER_CATEGORY_BATCH_TRIGGER", 99)

    reqs = [build_requirement(1, "COMM"), build_requirement(2, "COMM")]
    fake_llm = FakeLLMClient(
        [
            json.JSONDecodeError("bad", "{}", 1),
            [req_to_dict(reqs[0])],
        ]
    )
    extractor = TenderExtractor(db=None, llm_client=fake_llm)

    merged = await extractor._merge_category("COMM", reqs)

    assert merged is not None
    assert len(merged) == 1
    assert len(fake_llm.calls) == 2
    assert fake_llm.calls[1]["temperature"] == 0.0


@pytest.mark.asyncio
async def test_category_merge_batched_for_large_tech(monkeypatch):
    monkeypatch.setattr(settings, "TENDER_CATEGORY_BATCH_TRIGGER", 100)
    monkeypatch.setattr(settings, "TENDER_CATEGORY_TECH_FORCE_BATCH_THRESHOLD", 5)
    monkeypatch.setattr(settings, "TENDER_CATEGORY_BATCH_SIZE", 2)
    monkeypatch.setattr(settings, "TENDER_CATEGORY_JSON_RETRY", 0)

    reqs = [build_requirement(i, "TECH") for i in range(9)]

    # 5个批次 + 1个类别内最终收敛轮
    fake_llm = FakeLLMClient(
        [
            [req_to_dict(build_requirement(100, "TECH"))],
            [req_to_dict(build_requirement(101, "TECH"))],
            [req_to_dict(build_requirement(102, "TECH"))],
            [req_to_dict(build_requirement(103, "TECH"))],
            [req_to_dict(build_requirement(104, "TECH"))],
            [req_to_dict(build_requirement(105, "TECH"))],
        ]
    )
    extractor = TenderExtractor(db=None, llm_client=fake_llm)

    merged = await extractor._merge_category("TECH", reqs)

    assert len(fake_llm.calls) == 6
    assert len(merged) == 1


@pytest.mark.asyncio
async def test_category_merge_fallback_to_simple_dedupe(monkeypatch):
    monkeypatch.setattr(settings, "TENDER_CATEGORY_JSON_RETRY", 1)
    monkeypatch.setattr(settings, "TENDER_CATEGORY_BATCH_TRIGGER", 99)

    reqs = [
        ExtractedRequirement(
            category="QUAL",
            description="投标人须提供营业执照副本并加盖公章",
            is_mandatory=True,
            keywords=["营业执照", "公章"],
        ),
        ExtractedRequirement(
            category="QUAL",
            description="投标人须提供营业执照复印件并盖章",
            is_mandatory=True,
            keywords=["营业执照", "盖章"],
        ),
        ExtractedRequirement(
            category="QUAL",
            description="投标人须提供资质证书复印件",
            is_mandatory=True,
            keywords=["资质证书"],
        ),
    ]

    fake_llm = FakeLLMClient(
        [
            json.JSONDecodeError("bad", "{}", 1),
            json.JSONDecodeError("still bad", "{}", 2),
        ]
    )
    extractor = TenderExtractor(db=None, llm_client=fake_llm)

    merged = await extractor._merge_category("QUAL", reqs)

    assert len(fake_llm.calls) == 2
    assert len(merged) <= len(reqs)


@pytest.mark.asyncio
async def test_category_merge_final_pass_gate_to_batched(monkeypatch):
    monkeypatch.setattr(settings, "TENDER_CATEGORY_BATCH_TRIGGER", 100)
    monkeypatch.setattr(settings, "TENDER_CATEGORY_TECH_FORCE_BATCH_THRESHOLD", 5)
    monkeypatch.setattr(settings, "TENDER_CATEGORY_BATCH_SIZE", 2)
    monkeypatch.setattr(settings, "TENDER_CATEGORY_FINAL_PASS_SINGLE_MAX_INPUT", 2)
    monkeypatch.setattr(settings, "TENDER_CATEGORY_FINAL_PASS_BATCH_SIZE", 2)
    monkeypatch.setattr(settings, "TENDER_CATEGORY_JSON_RETRY", 0)
    monkeypatch.setattr(settings, "TENDER_SECONDARY_JSON_RETRY", 0)

    reqs = [build_requirement(i, "TECH") for i in range(9)]
    # 初始分类合并5批 + 类别final-pass分批3批
    fake_llm = FakeLLMClient(
        [
            [req_to_dict(build_requirement(200, "TECH"))],
            [req_to_dict(build_requirement(201, "TECH"))],
            [req_to_dict(build_requirement(202, "TECH"))],
            [req_to_dict(build_requirement(203, "TECH"))],
            [req_to_dict(build_requirement(204, "TECH"))],
            [req_to_dict(build_requirement(205, "TECH"))],
            [req_to_dict(build_requirement(206, "TECH"))],
            [req_to_dict(build_requirement(207, "TECH"))],
        ]
    )
    extractor = TenderExtractor(db=None, llm_client=fake_llm)
    monkeypatch.setattr(extractor, "_check_quality", lambda _reqs: [])

    merged = await extractor._merge_category("TECH", reqs)

    assert len(fake_llm.calls) == 8
    assert len(merged) >= 1
