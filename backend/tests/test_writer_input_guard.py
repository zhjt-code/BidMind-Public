"""标书编写链路 input-too-long 400 兜底测试。"""

import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.writer.clause_extractor import ClauseExtractor
from app.core.writer.industry_profiler import IndustryProfiler
from app.core.writer.outline_generator import OutlineGenerator
from app.core.writer.section_composer import SectionComposer


class _FakeJsonClient:
    def __init__(self, scripted_results):
        self.scripted_results = list(scripted_results)
        self.calls = []

    async def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        current = self.scripted_results.pop(0)
        if isinstance(current, Exception):
            raise current
        return current


class _FakeChatClient:
    def __init__(self, scripted_results):
        self.scripted_results = list(scripted_results)
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        current = self.scripted_results.pop(0)
        if isinstance(current, Exception):
            raise current
        return current


class _FakeDb:
    def add(self, _obj):
        return None

    async def commit(self):
        return None

    async def refresh(self, _obj):
        return None


def _make_api_status_error(message: str, status_code: int = 400) -> APIStatusError:
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    response = httpx.Response(status_code=status_code, request=request)
    return APIStatusError(message, response=response, body={"error": {"message": message}})


@pytest.mark.asyncio
async def test_outline_generator_unified_retries_after_input_too_long():
    fake_client = _FakeJsonClient([
        _make_api_status_error("Range of input length should be [1, 30720]"),
        [],
    ])
    generator = object.__new__(OutlineGenerator)
    generator.llm_client = fake_client

    async def _build_context(_project_id: int):
        return {
            "directory_rules": "\n".join(f"- 目录规则{i}" for i in range(20)),
            "content_requirements": "\n".join(f"- 内容要求{i}" for i in range(30)),
            "material_requirements": "\n".join(f"- 材料要求{i}" for i in range(30)),
            "scoring_criteria": "\n".join(f"- 评分标准{i}" for i in range(20)),
            "industry_profile": "\n".join(f"- 行业画像{i}" for i in range(15)),
        }

    generator._build_context = _build_context
    generator._parse_nodes = lambda raw_list: []
    generator._assign_word_counts_tree = lambda nodes, budget: None
    generator._override_node_type = lambda node: None

    async def _save_tree(_project_id: int, nodes):
        return nodes

    generator._save_tree = _save_tree

    result = await generator._generate_unified(project_id=1, total_word_budget=1000)

    assert result == []
    assert len(fake_client.calls) == 2
    assert len(fake_client.calls[1]["user_message"]) < len(fake_client.calls[0]["user_message"])


@pytest.mark.asyncio
async def test_writer_industry_profiler_retries_after_input_too_long():
    fake_client = _FakeChatClient([
        _make_api_status_error("Range of input length should be [1, 30720]"),
        (
            "### 1. 行业识别与典型特征\n这是生成后的行业画像，特征清楚。\n\n"
            "### 2. 专业术语表（20-30个）\n- 缓蚀剂\n\n"
            "### 3. 技术方案典型结构\n按工艺与验收组织。\n\n"
            "### 4. 评标专家核心关注点（5-8个）\n- 指标可核验\n\n"
            "### 5. 写作风格指导\n用数据和措施说话。\n\n"
            "### 6. 差异化竞争建议\n可突出既有业绩，不得写成招标已要求。"
        ),
    ])
    profiler = object.__new__(IndustryProfiler)
    profiler.db = _FakeDb()
    profiler.llm_client = fake_client

    clauses = [
        SimpleNamespace(sub_type="project_context", content="项目背景A", score_weight=None),
        SimpleNamespace(sub_type="project_context", content="项目背景B", score_weight=None),
        SimpleNamespace(sub_type="scoring_criterion", content="评分标准A", score_weight=5),
        SimpleNamespace(sub_type="scoring_criterion", content="评分标准B", score_weight=6),
        SimpleNamespace(sub_type="content_required", content="内容要求A", score_weight=None),
        SimpleNamespace(sub_type="content_required", content="内容要求B", score_weight=None),
        SimpleNamespace(sub_type="material_required", content="材料要求A", score_weight=None),
        SimpleNamespace(sub_type="material_required", content="材料要求B", score_weight=None),
    ]

    clause = await profiler.generate(
        project_id=1,
        document_id=2,
        clauses=clauses,
    )

    assert "行业画像" in clause.content
    assert len(fake_client.calls) == 2
    assert len(fake_client.calls[1]["user_message"]) < len(fake_client.calls[0]["user_message"])


@pytest.mark.asyncio
async def test_writer_industry_profiler_skips_llm_without_evidence():
    fake_client = _FakeChatClient(["不应调用"])
    profiler = object.__new__(IndustryProfiler)
    profiler.db = _FakeDb()
    profiler.llm_client = fake_client

    clause = await profiler.generate(
        project_id=1,
        document_id=2,
        clauses=[],
    )

    assert fake_client.calls == []
    assert "参考信息不足" in clause.content
    assert clause.metadata_json["source"] == "evidence_gate"


@pytest.mark.asyncio
async def test_section_composer_free_writing_retries_after_input_too_long(monkeypatch):
    """W1 字数闭环可能在生成成功后追加一次 length adjust LLM 调用；本测试
    聚焦"输入过长重试"契约，monkeypatch 关闭字数闭环以保持原 2 次调用断言。
    """
    from app.core.config import settings as _settings
    monkeypatch.setattr(_settings, "WRITING_WORD_COUNT_MIN_RATIO", 0.0)
    monkeypatch.setattr(_settings, "WRITING_DE_AI_REWRITE_ENABLED", False)

    fake_client = _FakeChatClient([
        _make_api_status_error("Range of input length should be [1, 30720]"),
        "<p>生成成功</p>",
    ])
    composer = object.__new__(SectionComposer)
    composer.llm_client = fake_client
    composer._db_lock = None
    composer._parse_profile = lambda _node: None
    composer._build_material_usage_rules = lambda _materials: ""
    composer._dedupe_ids = lambda ids: ids

    async def _load_bound_materials(*args, **kwargs):
        return []

    async def _load_bound_clauses(*args, **kwargs):
        return [SimpleNamespace(id=1, content="章节要求" * 40)]

    async def _load_material_chunk_samples(*args, **kwargs):
        return {}

    async def _load_material_images(*args, **kwargs):
        return []

    composer._load_bound_materials = _load_bound_materials
    composer._load_bound_clauses = _load_bound_clauses
    composer._load_material_chunk_samples = _load_material_chunk_samples
    composer._load_material_images = _load_material_images
    composer._build_context_within_budget = lambda **kwargs: {
        "profile_excerpt": "行业画像\n" + "\n".join(f"- 画像{i}" for i in range(20)),
        "chapter_profile_text": "章节画像\n" + "\n".join(f"- 章节{i}" for i in range(12)),
        "clauses_text": "\n".join(f"- 条款{i}" for i in range(20)),
        "materials_text": "\n".join(f"- 素材{i}" for i in range(30)),
        "selected_material_ids": [],
        "bound_material_count": 0,
        "priority_materials_used": 0,
        "priority_segments_used": 0,
        "truncated_materials": 0,
    }

    node = SimpleNamespace(id=1, title="技术方案", target_word_count=1200, depth=1)
    previous_summaries = [{"title": f"章节{i}", "summary": "摘要内容" * 20} for i in range(8)]

    result = await composer._compose_free(
        node=node,
        industry_profile="行业画像" * 200,
        previous_summaries=previous_summaries,
        number_map={1: "1.1"},
    )

    assert "生成成功" in result.content
    assert len(fake_client.calls) == 2
    assert len(fake_client.calls[1]["user_message"]) < len(fake_client.calls[0]["user_message"])


@pytest.mark.asyncio
async def test_clause_extractor_bid_structure_retries_after_input_too_long():
    fake_client = _FakeJsonClient([
        _make_api_status_error("Range of input length should be [1, 30720]"),
        {
            "structure_mode": "tech_comm",
            "has_technical": True,
            "has_commercial": True,
            "reasoning": "成功",
        },
    ])
    extractor = object.__new__(ClauseExtractor)
    extractor.llm_client = fake_client
    extractor.db = _FakeDb()

    clauses = [
        SimpleNamespace(sub_type="directory_rule", content="目录规则A", score_weight=None),
        SimpleNamespace(sub_type="directory_rule", content="目录规则B", score_weight=None),
        SimpleNamespace(sub_type="scoring_criterion", content="评分标准A", score_weight=10),
        SimpleNamespace(sub_type="scoring_criterion", content="评分标准B", score_weight=8),
        SimpleNamespace(sub_type="content_required", content="内容要求A", score_weight=None),
        SimpleNamespace(sub_type="content_required", content="内容要求B", score_weight=None),
        SimpleNamespace(sub_type="material_required", content="材料要求A", score_weight=None),
        SimpleNamespace(sub_type="material_required", content="材料要求B", score_weight=None),
        SimpleNamespace(sub_type="industry_profile", content="行业画像A\n行业画像B", score_weight=None),
    ]

    result = await extractor.analyze_bid_structure(
        project_id=1,
        document_id=2,
        clauses=clauses,
    )

    assert "tech_comm" in result.content
    assert len(fake_client.calls) == 2
    assert len(fake_client.calls[1]["user_message"]) < len(fake_client.calls[0]["user_message"])
