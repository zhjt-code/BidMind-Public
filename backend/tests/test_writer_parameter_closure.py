import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.writer.fixed_template_contract import FIXED_TEMPLATE_SNAPSHOT_KEY
from app.core.writer.industry_profiler import IndustryProfiler
from app.core.writer.prompts.clause_extract import HARD_CONSTRAINT_USER
from app.core.writer.prompts.section_compose import get_summarize_prompt
from app.core.writer.section_composer import SectionComposer
from app.models.writing_binding import BindingStatus
from app.models.writing_outline_node import OutlineNodeStatus, OutlineNodeType
from app.services.writing.material_service import MaterialService
from scripts.seed_llm_registry import _WRITING_NODES


def _binding(
    *,
    status: BindingStatus | str,
    clause_id: int | None = None,
    material_id: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        clause_id=clause_id,
        material_id=material_id,
    )


def test_writing_content_generate_seed_params_match_runtime_policy() -> None:
    nodes = {node.node_key: node for node in _WRITING_NODES}

    assert nodes["writing.data_fill"].default_temperature == 0.1
    assert nodes["writing.data_fill"].default_max_tokens == 2000
    assert nodes["writing.free_writing"].default_temperature == 0.2
    assert nodes["writing.free_writing"].default_max_tokens == 4000
    assert nodes["writing.cross_ref_check"].default_temperature == 0.1
    assert nodes["writing.cross_ref_check"].default_max_tokens == 2000


def test_summarize_prompt_preserves_key_facts_policy() -> None:
    system_msg, user_msg = get_summarize_prompt(
        "服务承诺",
        "本公司承诺服务期三年，响应时间不超过2小时，项目名称为智慧园区平台。",
    )

    assert "关键事实" in system_msg
    assert "不超过120字" in user_msg
    assert "关键承诺" in user_msg
    assert "服务期限" in user_msg


def test_collect_binding_targets_keeps_suggested_before_manual_review() -> None:
    direct_bindings = [
        _binding(status=BindingStatus.suggested, clause_id=101),
        _binding(status=BindingStatus.suggested, clause_id=102),
    ]

    result = SectionComposer._collect_binding_targets([direct_bindings], "clause_id")

    assert result == [101, 102]


def test_collect_binding_targets_only_keeps_confirmed_after_manual_review() -> None:
    direct_bindings = [
        _binding(status=BindingStatus.confirmed, clause_id=201),
        _binding(status=BindingStatus.suggested, clause_id=202),
        _binding(status=BindingStatus.rejected, clause_id=203),
    ]

    result = SectionComposer._collect_binding_targets([direct_bindings], "clause_id")

    assert result == [201]


def test_collect_binding_targets_merges_direct_and_inherited_groups_without_duplicates() -> None:
    direct_bindings = [
        _binding(status=BindingStatus.suggested, clause_id=301),
        _binding(status=BindingStatus.suggested, clause_id=302),
    ]
    inherited_root_bindings = [
        _binding(status=BindingStatus.confirmed, clause_id=401),
        _binding(status=BindingStatus.rejected, clause_id=402),
    ]
    inherited_volume_bindings = [
        _binding(status=BindingStatus.confirmed, clause_id=302),
        _binding(status=BindingStatus.confirmed, clause_id=403),
    ]

    result = SectionComposer._collect_binding_targets(
        [direct_bindings, inherited_root_bindings, inherited_volume_bindings],
        "clause_id",
    )

    assert result == [301, 302, 401, 403]


def test_build_material_context_entry_prefers_chunk_samples_and_metadata_tags() -> None:
    composer = SectionComposer(
        db=None,
        llm_client=SimpleNamespace(),
    )
    material = SimpleNamespace(
        id=1,
        title="核心资质证明材料",
        material_type=SimpleNamespace(value="certificate"),
        content="无关前缀内容",
        metadata_tags=json.dumps(
            {
                "domain": "企业资质",
                "applicable_sections": ["核心资质与专业认证"],
                "entities": ["营业执照", "资质证书"],
                "keywords": ["资质", "认证", "许可"],
            },
            ensure_ascii=False,
        ),
    )

    entry = composer._build_material_context_entry(
        material,
        excerpts=["本片段包含营业执照与资质证书编号。", "本片段包含许可有效期。"],
    )

    assert "标签:" in entry
    assert "领域=企业资质" in entry
    assert "适用章节=核心资质与专业认证" in entry
    assert "关键实体=营业执照、资质证书" in entry
    assert "相关片段1: 本片段包含营业执照与资质证书编号。" in entry
    assert "无关前缀内容" not in entry


def test_build_material_context_entry_exposes_structured_quality_signals() -> None:
    composer = SectionComposer(
        db=None,
        llm_client=SimpleNamespace(),
    )
    material = SimpleNamespace(
        id=2,
        title="营业执照扫描件",
        material_type=SimpleNamespace(value="image"),
        content="营业执照内容",
        source_page_start=3,
        source_page_end=4,
        source_page=None,
        resolved_label="营业执照",
        label_conflict="matched",
        metadata_tags=json.dumps(
            {
                "domain": "企业资质",
                "keywords": ["营业执照"],
                "_tag_status": "llm_success",
                "confidence": 0.92,
            },
            ensure_ascii=False,
        ),
    )

    entry = composer._build_material_context_entry(material, excerpts=["统一社会信用代码为91310000。"])

    assert "溯源:" in entry
    assert "业务标签=营业执照" in entry
    assert "标签一致性=matched" in entry
    assert "来源页=3-4" in entry
    assert "打标状态=llm_success" in entry
    assert "置信度=0.92" in entry


def test_format_image_metadata_includes_compact_structured_facts() -> None:
    text = SectionComposer._format_image_metadata([
        SimpleNamespace(
            id=11,
            image_index=0,
            image_type=SimpleNamespace(value="license"),
            business_subtype="business_license",
            caption="营业执照",
            structured_fields=json.dumps(
                {
                    "统一社会信用代码": "91310000MA1XXXXXX",
                    "企业名称": "示例公司",
                    "空字段": "",
                },
                ensure_ascii=False,
            ),
        )
    ])

    assert "业务子类=business_license" in text
    assert "结构化事实=统一社会信用代码=91310000MA1XXXXXX；企业名称=示例公司" in text


def test_material_quality_flags_expose_completed_but_not_ready_states() -> None:
    material = SimpleNamespace(
        metadata_tags=json.dumps({"_tag_status": "llm_failed"}, ensure_ascii=False),
        processing_status="completed",
        material_type=SimpleNamespace(value="text"),
        label_conflict="conflict",
    )

    flags, summary = MaterialService._build_material_quality_flags(
        material,
        chunk_count=0,
        review_image_count=1,
    )

    assert flags == ["llm_failed", "no_chunks", "label_conflict", "image_needs_review"]
    assert summary is not None
    assert "素材打标降级" in summary
    assert "缺少可检索分块" in summary


def test_is_material_consumable_filters_split_parent_and_ignored_child() -> None:
    split_parent = SimpleNamespace(material_role="split_parent", split_review_status="confirmed")
    ignored_child = SimpleNamespace(material_role="split_child", split_review_status="ignored")
    normal_child = SimpleNamespace(material_role="split_child", split_review_status="confirmed")

    assert SectionComposer._is_material_consumable(split_parent) is False
    assert SectionComposer._is_material_consumable(ignored_child) is False
    assert SectionComposer._is_material_consumable(normal_child) is True


def test_format_chapter_profile_text_outputs_semantic_role_and_boundaries() -> None:
    text = SectionComposer._format_chapter_profile_text(
        {
            "semantic_role": "说明企业资质、认证能力与合规性",
            "expects": ["企业资质等级", "许可证有效期"],
            "rejects": ["商务报价", "无关荣誉堆砌"],
        }
    )

    assert "章节职责: 说明企业资质、认证能力与合规性" in text
    assert "重点应覆盖: 企业资质等级；许可证有效期" in text
    assert "应避免混入: 商务报价；无关荣誉堆砌" in text


def test_parse_profile_accepts_valid_json() -> None:
    node = SimpleNamespace(
        chapter_profile=json.dumps(
            {
                "semantic_role": "说明企业资质、认证能力与合规性",
                "expects": ["企业资质等级"],
                "rejects": ["商务报价"],
            },
            ensure_ascii=False,
        )
    )

    profile = SectionComposer._parse_profile(node)

    assert profile is not None
    assert profile["semantic_role"] == "说明企业资质、认证能力与合规性"
    assert profile["expects"] == ["企业资质等级"]


def test_parse_profile_returns_none_for_invalid_json() -> None:
    node = SimpleNamespace(chapter_profile="{invalid-json")

    assert SectionComposer._parse_profile(node) is None


def test_sanitize_profile_text_removes_dialogue_preamble() -> None:
    raw = """好的，作为行业分析专家和资深投标顾问，我已根据您提供的招标约束信息，生成行业画像如下。

### 1. 行业识别与典型特征
所属行业：专业音视频系统集成行业。
"""

    cleaned = IndustryProfiler._sanitize_profile_text(raw)

    assert cleaned.startswith("### 1. 行业识别与典型特征")
    assert "好的，作为行业分析专家" not in cleaned


def test_sanitize_profile_text_keeps_existing_body_text() -> None:
    raw = """### 1. 行业识别与典型特征
所属行业：专业音视频系统集成行业。

### 2. 专业术语表（20-30个）
- 扩声系统
"""

    cleaned = IndustryProfiler._sanitize_profile_text(raw)

    assert cleaned == raw.strip()


@pytest.mark.asyncio
async def test_fill_data_reads_chapter_profile_without_attribute_error() -> None:
    llm_client = SimpleNamespace(chat=AsyncMock(return_value="<p>生成成功</p>"))
    composer = SectionComposer(db=None, llm_client=llm_client)
    composer._load_bound_materials = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_bound_clauses = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_material_chunk_samples = AsyncMock(return_value={})  # type: ignore[method-assign]
    composer._load_material_images = AsyncMock(return_value=[])  # type: ignore[method-assign]

    node = SimpleNamespace(
        id=1,
        title="核心资质与专业认证",
        depth=1,
        target_word_count=900,
        chapter_profile=json.dumps(
            {
                "semantic_role": "说明企业资质、认证能力与合规性",
                "expects": ["企业资质等级", "许可证有效期"],
                "rejects": ["商务报价"],
            },
            ensure_ascii=False,
        ),
    )

    payload = await composer._fill_data(node, industry_profile=None, number_map={1: "一、"})

    assert "生成成功" in payload.content
    assert payload.source_material_ids == []
    assert payload.source_clause_ids == []
    llm_client.chat.assert_awaited_once()
    assert "建议字数: 900 字" in llm_client.chat.await_args.kwargs["user_message"]


@pytest.mark.asyncio
async def test_fill_data_uses_registry_runtime_params(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.core.writer.composer.data_fill_route.resolve_runtime_params",
        lambda node_key, default_temperature, default_max_tokens: (0.0, 1234),
    )
    llm_client = SimpleNamespace(chat=AsyncMock(return_value="<p>生成成功</p>"))
    composer = SectionComposer(db=None, llm_client=llm_client)
    composer._load_bound_materials = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_bound_clauses = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_material_chunk_samples = AsyncMock(return_value={})  # type: ignore[method-assign]
    composer._load_material_images = AsyncMock(return_value=[])  # type: ignore[method-assign]

    node = SimpleNamespace(
        id=3,
        title="人员配置表",
        depth=1,
        target_word_count=500,
        chapter_profile=None,
    )

    await composer._fill_data(node, industry_profile=None, number_map={3: "三、"})

    assert llm_client.chat.await_args.kwargs["temperature"] == 0.0
    assert llm_client.chat.await_args.kwargs["max_tokens"] == 1234


@pytest.mark.asyncio
async def test_compose_free_tolerates_invalid_profile_json(monkeypatch) -> None:
    """W1 字数闭环可能追加 LLM 调用；本测试聚焦"非法 profile JSON 不崩溃"
    契约，monkeypatch 关闭闭环以保持原 1 次调用断言。
    """
    from app.core.config import settings as _settings
    monkeypatch.setattr(_settings, "WRITING_WORD_COUNT_MIN_RATIO", 0.0)
    monkeypatch.setattr(_settings, "WRITING_DE_AI_REWRITE_ENABLED", False)

    llm_client = SimpleNamespace(chat=AsyncMock(return_value="<p>自由论述成功</p>"))
    composer = SectionComposer(db=None, llm_client=llm_client)
    composer._load_bound_materials = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_bound_clauses = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_material_chunk_samples = AsyncMock(return_value={})  # type: ignore[method-assign]
    composer._load_material_images = AsyncMock(return_value=[])  # type: ignore[method-assign]

    node = SimpleNamespace(
        id=2,
        title="项目实施方案",
        depth=1,
        target_word_count=1200,
        chapter_profile="{invalid-json",
    )

    payload = await composer._compose_free(
        node,
        industry_profile=None,
        previous_summaries=[],
        number_map={2: "二、"},
    )

    assert "自由论述成功" in payload.content
    assert payload.source_material_ids == []
    assert payload.source_clause_ids == []
    llm_client.chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_compose_all_runs_data_fill_leaf_without_missing_profile_helper() -> None:
    llm_client = SimpleNamespace(chat=AsyncMock(return_value="<p>章节内容</p>"))
    composer = SectionComposer(db=SimpleNamespace(commit=AsyncMock()), llm_client=llm_client)
    composer._load_all_outline_nodes = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            SimpleNamespace(
                id=10,
                parent_id=None,
                depth=0,
                sort_order=0,
                title="核心资质与专业认证",
                node_type=OutlineNodeType.data_fill,
                status=OutlineNodeStatus.pending,
                chapter_profile=json.dumps(
                    {
                        "semantic_role": "说明企业资质、认证能力与合规性",
                        "expects": ["企业资质等级"],
                        "rejects": ["商务报价"],
                    },
                    ensure_ascii=False,
                ),
            )
        ]
    )
    composer._load_industry_profile = AsyncMock(return_value=None)  # type: ignore[method-assign]
    composer._load_bound_materials = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_bound_clauses = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_material_chunk_samples = AsyncMock(return_value={})  # type: ignore[method-assign]
    composer._load_material_images = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_current_draft = AsyncMock(return_value=None)  # type: ignore[method-assign]
    composer._save_draft = AsyncMock(return_value=SimpleNamespace(word_count=12))  # type: ignore[method-assign]
    composer._maybe_run_writer_judge = AsyncMock()  # type: ignore[method-assign]
    composer._is_writer_critical_commitment = lambda *_a, **_k: (False, "关键承诺")  # type: ignore[method-assign]
    snapshot_runner = AsyncMock()

    with patch(
        "app.core.writer.composer.gate_runner.run_writer_judge_from_snapshot",
        snapshot_runner,
    ):
        drafts = await composer.compose_all(project_id=553)

    assert len(drafts) == 1
    llm_client.chat.assert_awaited_once()
    composer._maybe_run_writer_judge.assert_not_awaited()
    snapshot_runner.assert_awaited_once()


def test_fixed_template_prompt_allows_long_raw_text_for_template() -> None:
    assert "fixed_template 类型不受500字限制" in HARD_CONSTRAINT_USER
    assert "普通约束不超过500字" in HARD_CONSTRAINT_USER


@pytest.mark.asyncio
async def test_fill_template_prefers_snapshot_source_and_flags_incomplete_snapshot() -> None:
    composer = SectionComposer(db=None, llm_client=SimpleNamespace())
    clause = SimpleNamespace(
        id=9,
        raw_text="旧的LLM摘录",
        metadata_json={
            FIXED_TEMPLATE_SNAPSHOT_KEY: {
                "title": "投标函",
                "source_page_start": 10,
                "source_page_end": 11,
                "raw_text_from_chunks": "投标函\n投标人：[待补充:投标人]\n法定代表人：[待补充:法定代表人]\n日期：[待补充:日期]\n盖章：",
                "extraction_confidence": 0.6,
                "is_complete_candidate": False,
            }
        },
    )
    composer._find_bound_clause = AsyncMock(return_value=clause)  # type: ignore[method-assign]
    composer._find_fallback_template_clause = AsyncMock(return_value=None)  # type: ignore[method-assign]
    composer._load_bound_materials = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_material_images = AsyncMock(return_value=[])  # type: ignore[method-assign]
    # fill_template 成功路径回填直接绑定约束(db=None, 需 mock)
    composer._load_bound_clauses = AsyncMock(return_value=[])  # type: ignore[method-assign]

    node = SimpleNamespace(id=4, title="投标函", project_id=1)

    payload = await composer._fill_template(node)

    assert "投标人" in payload.content
    assert "旧的LLM摘录" not in payload.content
    assert payload.source_clause_ids == [9]
    assert payload.gate_flags is not None
    assert "fixed_template_requires_review" in payload.gate_flags
