""": 分类合并进度文案必须用「组合并组数」，不得把任务数说成正式类别数。"""

import os
import sys
import types
import importlib.util
from pathlib import Path

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

sys.modules["app.core.ai"] = fake_ai_pkg
sys.modules["app.core.ai.prompts"] = fake_prompts_pkg
sys.modules["app.core.ai.prompts.tender_extract"] = fake_tender_prompts

from app.core.config import settings
from app.core.extractor.tender_extractor_parts.candidate_gate import (
    CandidateGateResult,
    CandidatePartition,
)
from app.schemas.requirement import ExtractedRequirement

_MODULE_PATH = Path(__file__).parent.parent / "app" / "core" / "extractor" / "tender_extractor.py"
_SPEC = importlib.util.spec_from_file_location(
    "tender_extractor_merge_progress_under_test", _MODULE_PATH
)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_MODULE)
TenderExtractor = _MODULE.TenderExtractor


def _req(index: int, category: str) -> ExtractedRequirement:
    return ExtractedRequirement(
        category=category,
        description=f"{category}独立要求条目-{index:02d}-用于进度文案口径测试",
        is_mandatory=False,
        keywords=[f"kw{index}"],
        source_pages=[1],
    )


def _pair_partition(requirements: list[ExtractedRequirement]) -> CandidatePartition:
    groups = [
        requirements[i : i + 2]
        for i in range(0, len(requirements) - 1, 2)
        if len(requirements[i : i + 2]) > 1
    ]
    leftover = requirements[len(requirements) // 2 * 2 :]
    components = [[i, i + 1] for i in range(0, len(requirements) - 1, 2)]
    singletons = list(range(len(requirements) // 2 * 2, len(requirements)))
    return CandidatePartition(
        candidate_groups=groups,
        bypass_requirements=leftover,
        gate_result=CandidateGateResult(components=components, singletons=singletons),
    )


@pytest.mark.asyncio
async def test_category_merge_progress_labels_groups_not_official_categories(
    monkeypatch,
) -> None:
    """2 个正式分类被切成 12 个合并组时，进度不得显示「12个类别」。"""
    monkeypatch.setattr(settings, "TENDER_CANDIDATE_GATE_ENABLED", True)
    monkeypatch.setattr(settings, "TENDER_NEAR_DUP_PREMERGE_ENABLED", False)

    messages: list[str] = []

    def _progress(stage, current, total, message, extra=None):
        messages.append(message)

    async def _fake_partition(requirements, **kwargs):
        return _pair_partition(requirements)

    async def _echo_merge(self, category, requirements, *, on_batch_progress=None):
        return list(requirements)

    async def _echo_cross(self, requirements):
        return list(requirements)

    monkeypatch.setattr(_MODULE, "partition_requirements_with_embeddings", _fake_partition)
    monkeypatch.setattr(TenderExtractor, "_merge_category", _echo_merge)
    monkeypatch.setattr(TenderExtractor, "_cross_category_dedup", _echo_cross)

    requirements = [_req(i, "QUAL") for i in range(12)] + [
        _req(i, "TECH") for i in range(12)
    ]
    extractor = TenderExtractor(db=None, llm_client=object(), progress_callback=_progress)
    await extractor._merge_requirements(requirements)

    assert "分组完成，2个类别" in messages
    assert "执行分类合并（12组）" in messages
    assert not any("12个类别" in msg for msg in messages)
    assert any(msg.startswith("分类合并 ") and msg.endswith("组") for msg in messages)


@pytest.mark.asyncio
async def test_category_merge_progress_includes_inner_batch_detail(monkeypatch) -> None:
    """: 组内分批完成时文案带「组内 a/b 批」，进度单调不回退。"""
    monkeypatch.setattr(settings, "TENDER_CANDIDATE_GATE_ENABLED", True)
    monkeypatch.setattr(settings, "TENDER_NEAR_DUP_PREMERGE_ENABLED", False)

    messages: list[str] = []
    currents: list[int] = []

    def _progress(stage, current, total, message, extra=None):
        messages.append(message)
        currents.append(current)

    async def _fake_partition(requirements, **kwargs):
        return _pair_partition(requirements)

    async def _merge_with_batches(self, category, requirements, *, on_batch_progress=None):
        if on_batch_progress is not None:
            on_batch_progress(1, 2)
            on_batch_progress(2, 2)
        return list(requirements)

    async def _echo_cross(self, requirements):
        return list(requirements)

    monkeypatch.setattr(_MODULE, "partition_requirements_with_embeddings", _fake_partition)
    monkeypatch.setattr(TenderExtractor, "_merge_category", _merge_with_batches)
    monkeypatch.setattr(TenderExtractor, "_cross_category_dedup", _echo_cross)

    requirements = [_req(i, "QUAL") for i in range(4)]
    extractor = TenderExtractor(db=None, llm_client=object(), progress_callback=_progress)
    await extractor._merge_requirements(requirements)

    assert any("组内 1/2 批" in msg for msg in messages)
    assert currents == sorted(currents)
