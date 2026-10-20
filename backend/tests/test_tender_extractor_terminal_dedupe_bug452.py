""" 防御测试: 入库前确定性终检去重。

映射真实回归:
- happy-path（同类别 <=3 条直接保留 / _check_quality 无问题提前返回）会绕过
 batched 路径里的去重，使同类别"逐字相同"条目漏入数据库，结果页出现明显重复。

护栏关注:
1. _check_quality 确实检测不到"同类别逐字重复"（证明终检收口的必要性）。
2. terminal_exact_collapse 能确定性合并逐字重复并融合 provenance。
3. terminal_exact_collapse 不跨 subject_scope/sub_requirements 合并，
 避免回收 _split_compound_requirements 的复合拆分（防 回归）。
4. simple_dedupe 合并时不把否决项降级为非否决项。
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import types
import importlib.util


os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://user:pass@localhost:5432/testdb",
)

sys.path.insert(0, str(Path(__file__).parent.parent))

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

from app.schemas.requirement import ExtractedRequirement  # noqa: E402

_MODULE_PATH = (
    Path(__file__).parent.parent / "app" / "core" / "extractor" / "tender_extractor.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "tender_extractor_terminal_dedupe_under_test", _MODULE_PATH
)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_MODULE)
TenderExtractor = _MODULE.TenderExtractor
dedup_stage = _MODULE._dedup_stage


def _make_extractor() -> TenderExtractor:
    return TenderExtractor(db=None, llm_client=None)


_SCREENSHOT_DESC = (
    "人员安全培训与资质要求：乙方应对作业人员进行安全生产教育培训，"
    "使其具备相应的安全意识和技能；特种作业人员应具备相应的资格证书。"
)


def _staff_dup(source_page: int) -> ExtractedRequirement:
    return ExtractedRequirement(
        category="STAFF",
        description=_SCREENSHOT_DESC,
        is_mandatory=True,
        keywords=["安全培训", "特种作业"],
        source_pages=[source_page],
        merged_count=2,
    )


def test_check_quality_misses_same_category_exact_duplicates() -> None:
    """根因护栏: 同类别逐字重复不会被 _check_quality 标记, 故必须有终检收口。"""
    extractor = _make_extractor()
    requirements = [_staff_dup(35), _staff_dup(35), _staff_dup(35)]

    issues = extractor._check_quality(requirements)

    # 既无跨类别重复, 关键词也未超阈值 -> 现有质检对该类重复无感
    assert all(issue.get("type") != "cross_category_duplicate" for issue in issues)
    assert issues == []


def test_terminal_collapse_removes_verbatim_duplicates() -> None:
    """3 条逐字相同的人员要求应被终检合并为 1 条, 并融合来源页码。"""
    extractor = _make_extractor()
    requirements = [_staff_dup(35), _staff_dup(40), _staff_dup(35)]

    collapsed = dedup_stage.terminal_exact_collapse(extractor, requirements)

    assert len(collapsed) == 1
    survivor = collapsed[0]
    assert survivor.description == _SCREENSHOT_DESC
    assert survivor.source_pages == [35, 40]
    assert survivor.is_mandatory is True
    # merged_count 累加, 反映被合并来源数
    assert survivor.merged_count == 6


def test_terminal_collapse_preserves_compound_facets() -> None:
    """复合拆分产生的同描述子面(不同 subject_scope/sub_requirements)不得被回收。"""
    extractor = _make_extractor()
    base = (
        "项目负责人须具备建造师证书并提供社保缴费证明及类似项目业绩"
    )
    facet_cert = ExtractedRequirement(
        category="STAFF",
        description=base,
        is_mandatory=True,
        keywords=["建造师"],
        source_pages=[12],
        subject_scope="personnel",
        sub_requirements=["personnel_cert"],
    )
    facet_perf = ExtractedRequirement(
        category="STAFF",
        description=base,
        is_mandatory=True,
        keywords=["业绩"],
        source_pages=[12],
        subject_scope="personnel",
        sub_requirements=["personnel_perf"],
    )

    collapsed = dedup_stage.terminal_exact_collapse(
        extractor, [facet_cert, facet_perf]
    )

    # 子要求不同 -> 视为不同子面, 保留两条, 不回收复合拆分
    assert len(collapsed) == 2
    assert {tuple(item.sub_requirements) for item in collapsed} == {
        ("personnel_cert",),
        ("personnel_perf",),
    }


def test_terminal_collapse_does_not_demote_mandatory() -> None:
    """逐字相同但一条否决一条非否决时, 合并结果必须保持否决属性。"""
    extractor = _make_extractor()
    mandatory = ExtractedRequirement(
        category="QUAL",
        description="投标人须具备安全生产许可证",
        is_mandatory=True,
        keywords=["安全生产许可证"],
        source_pages=[3],
    )
    non_mandatory = ExtractedRequirement(
        category="QUAL",
        description="投标人须具备安全生产许可证",
        is_mandatory=False,
        keywords=["安全生产许可证"],
        source_pages=[9],
    )

    collapsed = dedup_stage.terminal_exact_collapse(
        extractor, [mandatory, non_mandatory]
    )

    assert len(collapsed) == 1
    assert collapsed[0].is_mandatory is True
    assert collapsed[0].source_pages == [3, 9]
