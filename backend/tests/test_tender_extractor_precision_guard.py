"""TenderExtractor 精度闸门与反思双通道测试"""

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://user:pass@localhost:5432/testdb",
)

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
_SPEC = importlib.util.spec_from_file_location("tender_extractor_precision_under_test", _MODULE_PATH)
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


def build_requirement(index: int, category: str, description: str, mandatory: bool = False) -> ExtractedRequirement:
    return ExtractedRequirement(
        category=category,
        description=description,
        is_mandatory=mandatory,
        keywords=[f"k{index}", "保证金" if category == "COMM" else "资质"],
    )


@pytest.mark.asyncio
async def test_cross_category_dedup_llm_failure_still_uses_secondary_merge(monkeypatch):
    monkeypatch.setattr(settings, "TENDER_REFLECTION_JSON_RETRY", 0)
    reqs = [
        build_requirement(1, "COMM", "投标保证金应按要求提交并从基本账户转出", True),
        build_requirement(2, "QUAL", "投标保证金应按要求提交并从基本账户转出", True),
    ]
    fake_llm = FakeLLMClient([json.JSONDecodeError("bad", "{}", 1)])
    extractor = TenderExtractor(db=None, llm_client=fake_llm)

    captured = {"called": False, "issue_count": 0}

    async def fake_secondary_merge(requirements, issues):
        captured["called"] = True
        captured["issue_count"] = len(issues)
        return requirements[:1]

    monkeypatch.setattr(extractor, "_secondary_merge", fake_secondary_merge)

    async def fake_prefetch(_texts):
        return None

    monkeypatch.setattr(
        "app.core.extractor.tender_extractor_parts.dedup_stage.prefetch_candidate_embeddings",
        fake_prefetch,
    )

    result = await extractor._cross_category_dedup(reqs)

    assert captured["called"] is True
    assert captured["issue_count"] >= 1
    assert len(result) == 1


def test_precision_meta_contains_required_guard_fields():
    extractor = TenderExtractor(db=None, llm_client=FakeLLMClient([]))
    extractor._fallback_events = [{"stage": "secondary_merge", "reason": "secondary_batch_failed"}]
    extractor._stage_failures = [{"stage": "reflection", "reason": "llm_reflection_failed"}]
    reqs = [
        build_requirement(10, "COMM", "报价应不高于最高限价", True),
        build_requirement(11, "TECH", "技术方案需包含测试验收流程", False),
    ]
    issues = [{"type": "keyword_excess", "affected_indices": [0, 1]}]

    meta = extractor._build_precision_meta(reqs, duplicate_issues=issues)

    assert "mandatory_ratio" in meta
    assert "category_distribution" in meta
    assert "duplicate_signals" in meta
    assert "fallback_path" in meta
    assert "stage_failures" in meta
    assert meta["duplicate_signals"]["keyword_excess"] == 1
