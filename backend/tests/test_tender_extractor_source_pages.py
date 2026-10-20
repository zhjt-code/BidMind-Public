"""TenderExtractor 招标要求来源页码护栏测试

覆盖两个新增护栏，确保确认页“可点击页码定位”能拿到完整且不幻觉的来源页：

1. ``_align_chunk_source_pages``: 单分块抽取后，把每条要求的 source_pages
   折回 chunk.page_num，避免 LLM 偶发的页码幻觉/占位符（如返回页码 0
   或脱离当前 chunk 的页码）。
2. ``_merge_provenance``/本地去重: 当 ``_local_pre_dedupe`` /
   ``_simple_dedupe`` 决定丢弃同义条目时，应当把被丢弃条目的
   source_pages、keywords、sub_requirements 与 merged_count 并入保留条目，
   防止多分块来源信息被吞掉。
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import types
import importlib.util


# 测试环境最小化配置，避免导入 Settings 时报缺失
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://user:pass@localhost:5432/testdb",
)

# TenderExtractor 仅需少量 AI 接口，测试时注入最小桩模块避免加载完整 AI 栈
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
    "tender_extractor_source_pages_under_test", _MODULE_PATH
)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_MODULE)
TenderExtractor = _MODULE.TenderExtractor


class _FakeChunk:
    """轻量 Chunk 桩，仅暴露 page_num/id，避免拉起 SQLAlchemy 模型。"""

    def __init__(self, *, id: int, page_num: int | None) -> None:
        self.id = id
        self.page_num = page_num


def _make_extractor() -> TenderExtractor:
    return TenderExtractor(db=None, llm_client=None)


def test_align_chunk_source_pages_fills_missing_page() -> None:
    extractor = _make_extractor()
    item = ExtractedRequirement(
        category="QUAL",
        description="投标人需提供营业执照复印件并加盖公章",
        is_mandatory=True,
        keywords=["营业执照"],
        source_pages=[],
    )
    chunk = _FakeChunk(id=11, page_num=7)

    aligned = extractor._align_chunk_source_pages([item], chunk)

    assert len(aligned) == 1
    assert aligned[0].source_pages == [7]


def test_parse_items_with_salvage_returns_user_facing_failure_details() -> None:
    extractor = _make_extractor()

    valid, failed_items = extractor._parse_items_with_salvage(
        [
            {
                "category": "QUAL",
                "description": "投标人须提供营业执照复印件并加盖公章",
                "is_mandatory": True,
                "keywords": ["营业执照"],
                "source_pages": [3],
            },
            {
                "category": "UNKNOWN",
                "description": "投标人须具备相关服务能力",
                "is_mandatory": True,
                "keywords": ["服务能力"],
                "source_pages": [3],
            },
        ],
        chunk_id=11,
    )

    assert len(valid) == 1
    assert failed_items == [
        {
            "item_idx": 1,
            "summary": "投标人须具备相关服务能力",
            "reason": "要求分类无法识别",
        }
    ]


def test_align_chunk_source_pages_corrects_hallucinated_pages() -> None:
    """LLM 偶发返回不属于当前 chunk 的页码（含 0 / 99）时应折回真实 page_num。"""
    extractor = _make_extractor()
    item = ExtractedRequirement(
        category="TECH",
        description="技术方案需包含质量控制措施",
        is_mandatory=False,
        keywords=["质量控制"],
        source_pages=[0, 99],
    )
    chunk = _FakeChunk(id=12, page_num=5)

    aligned = extractor._align_chunk_source_pages([item], chunk)

    assert aligned[0].source_pages == [5]


def test_align_chunk_source_pages_skips_when_chunk_page_unknown() -> None:
    """chunk.page_num 为空时不应越权改写 LLM 原始来源。"""
    extractor = _make_extractor()
    item = ExtractedRequirement(
        category="COMM",
        description="投标保证金 5 万元，递交方式为银行转账",
        is_mandatory=True,
        keywords=["保证金"],
        source_pages=[3, 4],
    )
    chunk = _FakeChunk(id=13, page_num=None)

    aligned = extractor._align_chunk_source_pages([item], chunk)

    assert aligned[0].source_pages == [3, 4]


def test_merge_provenance_unions_source_pages_and_keywords() -> None:
    extractor = _make_extractor()
    keep = ExtractedRequirement(
        category="QUAL",
        description="投标人须提供营业执照及资质证书",
        is_mandatory=False,
        keywords=["营业执照"],
        sub_requirements=["营业执照"],
        source_pages=[3],
        merged_count=1,
    )
    extra = ExtractedRequirement(
        category="QUAL",
        description="投标人须提供营业执照",
        is_mandatory=True,
        keywords=["营业执照", "资质证书"],
        sub_requirements=["资质证书"],
        source_pages=[7, 3],
        merged_count=2,
    )

    merged = extractor._merge_provenance(keep, extra)

    assert merged.source_pages == [3, 7]
    assert merged.keywords == ["营业执照", "资质证书"]
    assert merged.sub_requirements == ["营业执照", "资质证书"]
    assert merged.merged_count == 3
    # 非 provenance 字段保持 keep 一侧
    assert merged.description == keep.description
    assert merged.is_mandatory is False


def test_simple_dedupe_preserves_dropped_source_pages() -> None:
    extractor = _make_extractor()
    requirements = [
        ExtractedRequirement(
            category="QUAL",
            description="投标人须提供营业执照复印件并加盖公章",
            is_mandatory=False,
            keywords=["营业执照", "公章"],
            source_pages=[3],
            merged_count=1,
        ),
        ExtractedRequirement(
            category="QUAL",
            description="投标人须提供营业执照复印件并加盖公章",
            is_mandatory=True,
            keywords=["营业执照"],
            source_pages=[7, 11],
            merged_count=1,
        ),
    ]

    deduped = extractor._simple_dedupe(requirements)

    assert len(deduped) == 1
    survivor = deduped[0]
    # 命中重复后应将被丢弃条目的来源页码并入保留条目
    assert survivor.source_pages == [3, 7, 11]
    assert "营业执照" in survivor.keywords
    assert "公章" in survivor.keywords
    # 被丢弃条目为否决项时，应把否决属性带回保留条目
    assert survivor.is_mandatory is True
    # merged_count 累加，反映来源条目数量
    assert survivor.merged_count == 2


def test_local_pre_dedupe_protects_mandatory_and_unions_low_risk_pages() -> None:
    extractor = _make_extractor()
    # 低风险重复项可以合并来源；否决项即使同文也必须保留，避免吞掉高风险判定。
    base_desc = "投标保证金应按招标文件要求提交"
    requirements = [
        ExtractedRequirement(
            category="COMM",
            description=base_desc,
            is_mandatory=False,
            keywords=["保证金", "金额"],
            source_pages=[3],
            merged_count=1,
        ),
        ExtractedRequirement(
            category="COMM",
            description=base_desc,
            is_mandatory=True,
            keywords=["保证金", "金额"],
            source_pages=[5],
            merged_count=1,
        ),
        ExtractedRequirement(
            category="COMM",
            description=base_desc,
            is_mandatory=False,
            keywords=["保证金", "金额"],
            source_pages=[8],
            merged_count=1,
        ),
        ExtractedRequirement(
            category="QUAL",
            description="投标人需提供营业执照复印件",
            is_mandatory=True,
            keywords=["营业执照"],
            source_pages=[2],
            merged_count=1,
        ),
    ]

    deduped = extractor._local_pre_dedupe(requirements)

    comm_items = [item for item in deduped if item.category == "COMM"]
    assert len(comm_items) == 2

    low_risk = [item for item in comm_items if item.is_mandatory is False]
    assert len(low_risk) == 1
    assert low_risk[0].source_pages == [3, 8]
    assert low_risk[0].merged_count == 2

    mandatory = [item for item in comm_items if item.is_mandatory is True]
    assert len(mandatory) == 1
    assert mandatory[0].source_pages == [5]

    qual_items = [item for item in deduped if item.category == "QUAL"]
    assert len(qual_items) == 1
    assert qual_items[0].source_pages == [2]
