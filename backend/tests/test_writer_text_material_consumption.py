import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import numpy as np
import pytest

from app.core.exceptions import ServiceError
from app.core.tasks.writing_match_task import run_writing_match_task
from app.core.writer.filename_classifier import classify_by_filename
from app.core.writer.label_resolver import resolve_material_label
from app.core.writer.material_tagger import MaterialTagger
from app.core.writer.prompts.section_compose import (
    get_data_fill_prompt,
    get_free_writing_prompt,
)
from app.core.writer.section_composer import SectionComposer
from app.services.writing_service import WritingService


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self):
        self.commit = AsyncMock()
        self.refresh = AsyncMock()
        self.added = None

    async def execute(self, *_args, **_kwargs):
        return _ScalarRows([])

    def add(self, obj):
        self.added = obj


def _binding(
    *,
    status: str,
    material_id: int | None = None,
    outline_node_id: int | None = None,
):
    return SimpleNamespace(
        status=status,
        material_id=material_id,
        outline_node_id=outline_node_id,
    )


class _AsyncSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_refresh_fixed_template_drafts_uses_payload_content_and_sources() -> None:
    node = SimpleNamespace(id=9, project_id=100, title="投标函", node_type=None)
    fake_result = _ScalarRows([node])
    db = SimpleNamespace(execute=AsyncMock(return_value=fake_result))
    service = WritingService(db=db)
    composer = SimpleNamespace(
        _fill_template=AsyncMock(
            return_value=SimpleNamespace(
                content="<p>fixed-template</p>",
                source_material_ids=[31],
                source_clause_ids=[41],
            )
        ),
        _load_current_draft=AsyncMock(return_value=None),
        _save_draft=AsyncMock(),
    )

    await service._refresh_fixed_template_drafts(project_id=100, composer=composer)

    composer._save_draft.assert_awaited_once()
    args, kwargs = composer._save_draft.await_args
    from app.core.writer.composer.draft_persistence import DraftWriteRequest

    assert args[0] is node
    request = args[1]
    assert isinstance(request, DraftWriteRequest)
    assert request.content == "<p>fixed-template</p>"
    assert request.source_material_ids == [31]
    assert request.source_clause_ids == [41]
    assert kwargs == {}


@pytest.mark.asyncio
async def test_fixed_template_company_profile_consumes_text_material_before_source_trace() -> None:
    """fixed_template 只有把素材事实写入正文后，才允许记录 source_material_ids。"""
    material = SimpleNamespace(
        id=726,
        title="公司简介.docx",
        material_type=SimpleNamespace(value="text"),
        resolved_label="公司简介",
        file_name_label="公司简介",
        content=(
            "公司简介\n\n"
            "我公司主要从事油气田勘探开发技术研发、工程设计与现场技术服务。"
            "在<ORG_UNIVERSITY_1>石油工程学院设有研发中心，通过了QHSE体系认证。"
            "公司人员由高校教师为核心的研发团队和来自油田生产一线的技术专家组成，"
            "硕士学历以上占比72%，工程师以上职称占比91%，研究领域覆盖油气田勘探开发各个方向，"
            "现场人员长期从事钻井、压裂、三采等项目技术服务，专业水平高，经验丰富。"
            "依托湖盆致密油气教育部重点实验室、中石油HSE重点实验室和页岩油气富集机理重点实验室，"
            "团队在科研服务、现场试验、数据分析和成果转化方面积累了系统经验。"
            "团队发表论文15篇，发明专利17项。"
            "企业文化为卓越、导向、务实。"
        ),
        metadata_tags=json.dumps({"domain": "企业概况"}, ensure_ascii=False),
    )
    clause = SimpleNamespace(
        id=41,
        raw_text=(
            "投标人：[待补充:投标人名称]\n"
            "地址：[待补充:地址]\n"
            "电话：[待补充:电话]\n"
            "法定代表人：[待补充:法定代表人]\n"
            "日期：[待补充:日期]\n"
            "注:公司简介格式自定。"
        ),
        content="公司简介格式自定。",
        metadata_json={},
    )
    composer = SectionComposer(db=None, llm_client=SimpleNamespace())
    composer._find_bound_clause = AsyncMock(return_value=clause)  # type: ignore[method-assign]
    composer._find_fallback_template_clause = AsyncMock(return_value=None)  # type: ignore[method-assign]
    composer._load_bound_materials = AsyncMock(return_value=[material])  # type: ignore[method-assign]
    composer._load_material_images = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_bound_clauses = AsyncMock(return_value=[])  # type: ignore[method-assign]
    node = SimpleNamespace(
        id=6772,
        project_id=1568,
        title="公司简介",
        volume_type=None,
    )

    payload = await composer._fill_template(node)

    assert "油气田勘探开发技术研发" in payload.content
    assert "QHSE体系认证" in payload.content
    assert "发明专利17项" in payload.content
    assert "企业文化为卓越、导向、务实" in payload.content
    assert "公司简介.docx" not in payload.content
    assert "公司简介 我公司" not in payload.content
    assert payload.source_material_ids == [726]


@pytest.mark.asyncio
async def test_load_bound_materials_inherits_parent_company_profile_for_leaf() -> None:
    inherited_material = SimpleNamespace(
        id=108,
        title="公司简介.docx",
        material_type=SimpleNamespace(value="text"),
        resolved_label="公司简介",
        file_name_label="公司简介",
        content="公司成立于2012年，主营业务覆盖系统设计与运维服务。",
        metadata_tags=json.dumps({"domain": "企业概况"}, ensure_ascii=False),
    )
    fake_db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarRows([]),
                _ScalarRows([_binding(status="confirmed", material_id=108, outline_node_id=200)]),
                _ScalarRows([inherited_material]),
            ]
        )
    )
    composer = SectionComposer(db=fake_db, llm_client=SimpleNamespace())
    composer._load_ancestor_node_ids = AsyncMock(return_value=[200])  # type: ignore[method-assign]

    materials = await composer._load_bound_materials(
        300,
        node_title="公司基本情况简介",
        chapter_profile={"semantic_role": "介绍企业概况", "expects": ["主营业务"]},
    )

    assert [material.id for material in materials] == [108]


@pytest.mark.asyncio
async def test_load_bound_materials_filters_irrelevant_parent_materials() -> None:
    inherited_material = SimpleNamespace(
        id=208,
        title="设备清单.docx",
        material_type=SimpleNamespace(value="text"),
        resolved_label="设备资料",
        file_name_label="设备资料",
        content="设备型号、仪器清单及配置参数。",
        metadata_tags=json.dumps(
            {"domain": "设备清单", "keywords": ["设备", "仪器"]},
            ensure_ascii=False,
        ),
    )
    fake_db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarRows([]),
                _ScalarRows([_binding(status="confirmed", material_id=208, outline_node_id=200)]),
                _ScalarRows([inherited_material]),
            ]
        )
    )
    composer = SectionComposer(db=fake_db, llm_client=SimpleNamespace())
    composer._load_ancestor_node_ids = AsyncMock(return_value=[200])  # type: ignore[method-assign]

    materials = await composer._load_bound_materials(
        300,
        node_title="公司基本情况简介",
        chapter_profile={"semantic_role": "介绍企业概况", "expects": ["主营业务"]},
    )

    assert materials == []


def test_company_profile_material_gets_priority_segments_and_longer_fallback() -> None:
    composer = SectionComposer(db=None, llm_client=SimpleNamespace())
    material = SimpleNamespace(
        id=1,
        title="公司简介",
        material_type=SimpleNamespace(value="text"),
        resolved_label="公司简介",
        file_name_label="公司简介",
        content=(
            "公司成立于2012年，专注智慧园区与音视频系统集成。\\n\\n"
            "主营业务覆盖系统设计、设备集成、运维服务与平台开发。\\n\\n"
            "近三年实施多个大型项目，具备跨区域交付与驻场保障能力。"
        ),
        metadata_tags=json.dumps(
            {
                "domain": "企业概况",
                "applicable_sections": ["投标人基本情况"],
                "keywords": ["公司简介", "智慧园区", "项目经验"],
            },
            ensure_ascii=False,
        ),
    )

    entry = composer._build_material_context_entry(
        material,
        excerpts=None,
        node_title="投标人基本情况",
        chapter_profile={"semantic_role": "介绍企业基础情况", "expects": ["主营业务", "代表项目"]},
    )

    assert "重点基础信息1:" in entry
    assert "公司成立于2012年" in entry
    assert "主营业务覆盖系统设计" in entry
    assert "近三年实施多个大型项目" in entry
    assert "内容摘要1:" in entry


def test_long_text_specializations_are_treated_as_high_value() -> None:
    composer = SectionComposer(db=None, llm_client=SimpleNamespace())
    materials = [
        SimpleNamespace(
            id=11,
            title="项目业绩综述",
            material_type=SimpleNamespace(value="text"),
            resolved_label="项目业绩综述",
            file_name_label="项目业绩综述",
            content="代表项目与履约成果说明",
            metadata_tags=json.dumps({"domain": "项目业绩"}, ensure_ascii=False),
        ),
        SimpleNamespace(
            id=12,
            title="服务承诺",
            material_type=SimpleNamespace(value="text"),
            resolved_label="服务承诺",
            file_name_label="服务承诺",
            content="响应时效与售后机制说明",
            metadata_tags=json.dumps({"domain": "服务承诺"}, ensure_ascii=False),
        ),
        SimpleNamespace(
            id=13,
            title="管理制度",
            material_type=SimpleNamespace(value="text"),
            resolved_label="管理制度",
            file_name_label="管理制度",
            content="制度名称、流程机制与责任分工",
            metadata_tags=json.dumps({"domain": "管理制度"}, ensure_ascii=False),
        ),
    ]

    assert all(composer._is_high_value_text_material(material) for material in materials)


def test_consumption_evidence_matches_html_escaped_content() -> None:
    from app.core.writer.material_consumption_trace import (
        build_consumption_trace,
        has_consumption_evidence,
    )

    trace = build_consumption_trace(
        bound_material_ids=[91],
        selected_material_ids=[91],
        consumed_material_ids=[91],
        evidence={91: "QHSE & ISO 质量认证"},
    )

    assert has_consumption_evidence(
        91,
        "<p>QHSE &amp; ISO 质量认证</p>",
        trace,
    )


@pytest.mark.asyncio
async def test_save_draft_persists_source_ids() -> None:
    db = _FakeSession()
    composer = SectionComposer(db=db, llm_client=SimpleNamespace())
    node = SimpleNamespace(id=7)

    draft = await composer._save_draft(
        node,
        "<p>正文内容</p>",
        source_material_ids=[11, 12, 11],
        source_clause_ids=[21, 22, 21],
    )

    assert draft.source_material_ids == json.dumps([11, 12], ensure_ascii=False)
    assert draft.source_clause_ids == json.dumps([21, 22], ensure_ascii=False)
    assert db.added is draft
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(draft)


@pytest.mark.asyncio
async def test_company_profile_binds_variant_section_title_via_semantic_pipeline() -> None:
    """公司简介素材可绑定变体标题章节"投标人基本情况"(迁移自旧 resolved_label 模式表)。

 旧机制: _RESOLVED_LABEL_SECTION_PATTERNS 维护"公司简介->投标人基本情况"等变体词表。
 新机制: 变体归属由完整命题语义召回(safe_encode) + LLM 句对蕴含裁决承担，
 不再维护词表。此处注入语义等价 encoder 与 supports 裁决, 断言管线产出该绑定。
 """
    import re as _re

    from app.core.writer.material_matching.contracts import MaterialMatchingRequest
    from app.core.writer.material_matching.pipeline import match_materials_to_chapters

    material = SimpleNamespace(
        id=3,
        title="公司简介",
        display_title=None,
        material_type="text",
        resolved_label="公司简介",
        label_conflict="matched",
        metadata_tags=json.dumps(
            {"domain": "企业概况", "bid_purpose": "企业综合实力介绍"},
            ensure_ascii=False,
        ),
    )
    nodes = [
        SimpleNamespace(
            id=31, title="投标人基本情况", parent_id=None,
            node_type=None, volume_type=None,
            chapter_profile=json.dumps({"semantic_role": "介绍投标人基本情况", "expects": ["公司简介"]}),
        ),
        SimpleNamespace(
            id=32, title="技术方案", parent_id=None,
            node_type=None, volume_type=None,
            chapter_profile=json.dumps({"semantic_role": "说明技术方案", "expects": ["实施方法"]}),
        ),
    ]

    async def _variant_encoder(texts: list[str], **_kw) -> np.ndarray:
        # 公司简介命题与"投标人基本情况"需求语义等价(同向量), 与"技术方案"正交
        vectors = []
        for text in texts:
            if "公司简介" in text or "投标人基本情况" in text:
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.0, 1.0])
        return np.array(vectors, dtype=float)

    class _ScriptedLLM:
        async def chat_with_validation(self, *, user_message, response_model, **kwargs):
            pair_ids = _re.findall(r'"pair_id":\s*"(m\d+_n\d+)"', user_message)
            decisions = [
                {
                    "pair_id": pid,
                    "relation": "supports" if pid == "m3_n31" else "unrelated",
                    "confidence": 0.9 if pid == "m3_n31" else 0.2,
                }
                for pid in pair_ids
            ]
            return response_model(decisions=decisions)

    request = MaterialMatchingRequest(project_id=567, materials=[material], nodes=nodes)
    proposals = await match_materials_to_chapters(
        request, llm_client=_ScriptedLLM(), encoder=_variant_encoder
    )

    pairs = {(p.material_id, p.outline_node_id) for p in proposals}
    assert (3, 31) in pairs
    assert (3, 32) not in pairs


def test_prompt_includes_material_usage_rules() -> None:
    _, data_fill_user = get_data_fill_prompt(
        node_title="投标人基本情况",
        target_word_count=900,
        clauses_text="（无关联约束）",
        materials_text="### 公司简介 (text)",
        chapter_profile_text="（无章节画像）",
        chapter_number="一、",
        sub_numbering_hint="子标题使用 1、2、3、... 编号",
        material_usage_rules="- 必须使用公司简介中的事实点",
    )
    _, free_writing_user = get_free_writing_prompt(
        node_title="投标人基本情况",
        target_word_count=1200,
        clauses_text="（无关联约束）",
        materials_text="### 公司简介 (text)",
        previous_summaries_text="（首个章节，无前文）",
        industry_profile_excerpt="",
        chapter_profile_text="（无章节画像）",
        chapter_number="一、",
        sub_numbering_hint="子标题使用 1、2、3、... 编号",
        material_usage_rules="- 必须使用公司简介中的事实点",
    )

    assert "## 素材使用要求" in data_fill_user
    assert "必须使用公司简介中的事实点" in data_fill_user
    assert "建议字数: 900 字" in data_fill_user
    assert "参考篇幅为 900 字左右" in data_fill_user
    assert "## 素材使用要求" in free_writing_user
    assert "必须使用公司简介中的事实点" in free_writing_user
    assert "建议字数: 1200 字" in free_writing_user


def test_material_usage_rules_include_type_specific_constraints() -> None:
    composer = SectionComposer(db=None, llm_client=SimpleNamespace())
    materials = [
        SimpleNamespace(
            id=21,
            title="服务承诺",
            material_type=SimpleNamespace(value="text"),
            resolved_label="服务承诺",
            file_name_label="服务承诺",
            content="2小时响应、24小时到场、终身技术支持。",
            metadata_tags=json.dumps({"domain": "服务承诺"}, ensure_ascii=False),
        ),
        SimpleNamespace(
            id=22,
            title="管理制度",
            material_type=SimpleNamespace(value="text"),
            resolved_label="管理制度",
            file_name_label="管理制度",
            content="制度名称、审批流程、监督机制。",
            metadata_tags=json.dumps({"domain": "管理制度"}, ensure_ascii=False),
        ),
    ]

    rules = composer._build_material_usage_rules(materials)

    assert "响应时效、服务机制、保障措施和违约责任" in rules
    assert "制度名称、关键流程、责任分工和执行监督机制" in rules


def test_build_outline_embedding_text_contains_path_and_profile() -> None:
    root = SimpleNamespace(id=1, title="商务部分", parent_id=None, chapter_profile=None)
    node = SimpleNamespace(
        id=2,
        title="投标人基本情况",
        parent_id=1,
        chapter_profile=json.dumps(
            {
                "semantic_role": "介绍企业概况与综合实力",
                "expects": ["主营业务", "代表项目"],
                "rejects": ["商务报价"],
            },
            ensure_ascii=False,
        ),
    )

    text = WritingService._build_outline_embedding_text(node, {1: root, 2: node})

    assert "标题: 投标人基本情况" in text
    assert "路径: 商务部分 > 投标人基本情况" in text
    assert "章节职责: 介绍企业概况与综合实力" in text
    assert "重点覆盖: 主营业务；代表项目" in text


def test_context_budget_skips_oversized_first_material_without_starving_following_materials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.core.writer.section_composer.settings.WRITING_CONTEXT_BUDGET_TOKENS", 1000)
    composer = SectionComposer(db=None, llm_client=SimpleNamespace())
    first = SimpleNamespace(
        id=1,
        title="公司简介",
        material_type=SimpleNamespace(value="text"),
        resolved_label="公司简介",
        file_name_label="公司简介",
        content=("公司简介" * 800),
        metadata_tags=json.dumps({"domain": "企业概况"}, ensure_ascii=False),
    )
    second = SimpleNamespace(
        id=2,
        title="技术服务要点",
        material_type=SimpleNamespace(value="text"),
        resolved_label=None,
        file_name_label=None,
        content="本章最相关的技术服务内容",
        metadata_tags=json.dumps({"domain": "技术方案", "keywords": ["技术服务"]}, ensure_ascii=False),
    )

    context = composer._build_context_within_budget(
        industry_profile=None,
        clauses=[],
        materials=[first, second],
        previous_summaries=[],
        images=None,
        chunk_samples={
            1: ["公司简介核心段落" * 120],
            2: ["技术服务关键措施与交付安排"],
        },
        chapter_profile={"semantic_role": "说明技术服务能力", "expects": ["技术服务"]},
        node_title="技术服务方案",
    )

    assert context["materials_text"]
    assert 2 in context["selected_material_ids"]


def test_filename_classifier_recognizes_new_long_text_types() -> None:
    _, service_label = classify_by_filename("服务承诺汇编.pdf")
    _, management_label = classify_by_filename("质量管理制度.docx")
    _, performance_label = classify_by_filename("项目业绩综述.pdf")

    assert service_label == "服务承诺"
    assert management_label == "质量管理制度"
    assert performance_label == "项目业绩综述"


def test_label_resolver_detects_long_text_from_non_head_window() -> None:
    content = ("前言说明" * 300) + " 本节详细说明服务承诺、响应时效和售后服务机制。"
    resolution = resolve_material_label(None, content, [])

    assert resolution.resolved_label == "服务承诺"
    assert resolution.conflict_status == "matched"


def test_label_resolver_accepts_structure_hint_label() -> None:
    resolution = resolve_material_label(
        file_name_label=None,
        material_content="企业基本情况与主营业务说明。" * 10,
        images=[],
        structure_hint_label="公司简介",
    )

    assert resolution.conflict_status == "matched"
    assert resolution.resolved_label == "公司简介"
    assert resolution.source == "structure"


def test_label_resolver_prefers_structure_hint_when_text_keyword_is_noisy() -> None:
    resolution = resolve_material_label(
        file_name_label=None,
        material_content=(
            "公司及团队简介 本公司主要从事油气田勘探开发技术研发、工程设计与现场技术服务。"
            "公司建立了现代企业管理制度，通过了QHSE体系认证。"
        ),
        images=[],
        structure_hint_label="公司简介",
    )

    assert resolution.conflict_status == "matched"
    assert resolution.resolved_label == "公司简介"
    assert resolution.source == "structure"


@pytest.mark.asyncio
async def test_list_materials_attaches_image_summary_fields() -> None:
    materials = [
        SimpleNamespace(id=1, project_id=100, title="其它.pdf", display_title="公司简介", metadata_tags=None, processing_status="saved", material_type="pdf", label_conflict=None),
        SimpleNamespace(id=2, project_id=100, title="法人身份证.pdf", display_title=None, metadata_tags=None, processing_status="saved", material_type="pdf", label_conflict=None),
    ]
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarRows(materials),
                _ScalarRows([
                    (1, 3, 1, 2),
                ]),
                _ScalarRows([]),
                _ScalarRows([
                    SimpleNamespace(
                        id=11,
                        material_id=1,
                        image_index=0,
                        caption="营业执照扫描件",
                        image_type=SimpleNamespace(value="license"),
                        business_subtype="business_license",
                    ),
                    SimpleNamespace(
                        id=12,
                        material_id=1,
                        image_index=1,
                        caption="待复核图片",
                        image_type=None,
                        business_subtype="needs_review",
                    ),
                ]),
            ]
        )
    )
    service = WritingService(db=db)
    service._get_project_or_raise = AsyncMock(return_value=SimpleNamespace(id=100))  # type: ignore[method-assign]

    result = await service.list_materials(100, SimpleNamespace())

    assert result[0].image_count == 3
    assert result[0].validated_image_count == 1
    assert result[0].review_image_count == 2
    assert result[0].effective_title == "公司简介"
    assert result[0].image_assets[0]["image_type"] == "license"
    assert result[0].image_assets[1]["review_status"] == "needs_review"
    assert result[1].image_count == 0
    assert result[1].effective_title == "法人身份证.pdf"


@pytest.mark.asyncio
async def test_update_material_title_sets_display_title_and_effective_title() -> None:
    material = SimpleNamespace(
        id=41,
        project_id=100,
        title="其它.pdf",
        display_title=None,
        processing_status="saved",
        structure_hint_label=None,
        content=None,
    )
    db = SimpleNamespace(
        get=AsyncMock(return_value=material),
        execute=AsyncMock(return_value=_ScalarRows([])),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    service = WritingService(db=db)
    service._get_project_or_raise = AsyncMock(return_value=SimpleNamespace(id=100))  # type: ignore[method-assign]

    result = await service.update_material_title(100, 41, "公司简介", SimpleNamespace())

    assert material.display_title == "公司简介"
    assert material.file_name_label == "公司简介"
    assert result.effective_title == "公司简介"
    assert result.image_assets == []
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(material)


@pytest.mark.asyncio
async def test_batch_update_material_titles_updates_once_and_requests_rematch() -> None:
    first = SimpleNamespace(
        id=41,
        project_id=100,
        title="其它.pdf",
        display_title=None,
        effective_title="其它.pdf",
    )
    second = SimpleNamespace(
        id=42,
        project_id=100,
        title="附件材料.pdf",
        display_title=None,
        effective_title="附件材料.pdf",
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarRows([first, second])),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    service = WritingService(db=db)
    service._get_project_or_raise = AsyncMock(return_value=SimpleNamespace(id=100))  # type: ignore[method-assign]
    service._refresh_material_title_derivatives = AsyncMock()  # type: ignore[method-assign]

    result = await service.batch_update_material_titles(
        100,
        [
            {"material_id": 41, "display_title": "公司简介"},
            {"material_id": 42, "display_title": "服务承诺"},
        ],
        SimpleNamespace(),
    )

    assert first.display_title == "公司简介"
    assert second.display_title == "服务承诺"
    assert result["updated_count"] == 2
    assert result["updated_material_ids"] == [41, 42]
    assert result["requires_rematch"] is True
    service._refresh_material_title_derivatives.assert_has_awaits([call(first), call(second)])
    db.commit.assert_awaited_once()
    assert db.refresh.await_count == 2


@pytest.mark.asyncio
async def test_refine_sub_material_titles_uses_single_strong_caption_for_poor_title() -> None:
    material = SimpleNamespace(
        id=168,
        title="195",
        display_title=None,
        structure_hint_label=None,
        material_role="split_child",
        content="",
        file_name_label=None,
        content_label=None,
        resolved_label=None,
        label_conflict=None,
        label_source=None,
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarRows([
            SimpleNamespace(
                material_id=168,
                caption="图1：劳动合同扫描件，签约地郑州，有效期长期。",
                image_index=0,
                business_subtype="needs_review",
                image_type=None,
            ),
        ])),
        commit=AsyncMock(),
    )
    service = WritingService(db=db)

    refined = await service._refine_sub_material_titles([material])  # type: ignore[arg-type]

    assert refined == 1
    assert material.display_title == "劳动合同扫描件"
    assert material.file_name_label == "项目合同"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_refine_sub_material_titles_does_not_override_existing_display_title() -> None:
    material = SimpleNamespace(
        id=169,
        title="196",
        display_title="用户已修正标题",
        structure_hint_label="职称证书",
        material_role="split_child",
        content="",
        file_name_label=None,
        content_label=None,
        resolved_label=None,
        label_conflict=None,
        label_source=None,
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarRows([
            SimpleNamespace(
                material_id=169,
                caption="图1：合同扫描件",
                image_index=0,
                business_subtype="other",
                image_type=None,
            ),
        ])),
        commit=AsyncMock(),
    )
    service = WritingService(db=db)

    refined = await service._refine_sub_material_titles([material])  # type: ignore[arg-type]

    assert refined == 0
    assert material.display_title == "用户已修正标题"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_persist_and_classify_images_expires_only_image_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """图片分类跨 session 写回后，只刷新图片记录，不能全局过期素材对象。"""

    async def fake_enqueue_image_llm_job(**_kwargs):
        return True, "queued"

    async def fake_wait_for_image_llm_result(_batch_id, **_kwargs):
        return {
            "stats": {
                "classified": 1,
                "validated": 0,
                "needs_review": 0,
                "unknown": 0,
                "invalid_subtype": 0,
            }
        }

    monkeypatch.setattr(
        "app.core.tasks.writing_image_llm_task.enqueue_image_llm_job",
        fake_enqueue_image_llm_job,
    )
    monkeypatch.setattr(
        "app.core.tasks.writing_image_llm_task.wait_for_image_llm_result",
        fake_wait_for_image_llm_result,
    )

    class FakeDb:
        def __init__(self):
            self.added = []
            self.flush = AsyncMock(side_effect=self._flush)
            self.commit = AsyncMock()
            self.expire = Mock()
            self.expire_all = Mock()

        def add(self, obj):
            self.added.append(obj)

        async def _flush(self):
            for idx, obj in enumerate(self.added, start=1):
                obj.id = 700 + idx

    db = FakeDb()
    service = WritingService(db=db)  # type: ignore[arg-type]
    material = SimpleNamespace(
        id=41,
        processing_status="processing",
        title="法人身份证.jpg",
        display_title=None,
    )
    image = SimpleNamespace(
        file_path="storage/uploads/41/page-1.png",
        visual_content="这是一份居民身份证扫描件",
        width=800,
        height=600,
        _file_hash="hash-1",
    )

    await service._persist_and_classify_images(
        100,
        [material],  # type: ignore[list-item]
        {41: SimpleNamespace(images=[image])},
        {},
    )

    db.expire_all.assert_not_called()
    db.expire.assert_called_once()
    expired_record, expired_attrs = db.expire.call_args.args
    assert expired_record.id == 701
    assert expired_attrs == [
        "image_type",
        "caption",
        "business_subtype",
        "structured_fields",
    ]


@pytest.mark.asyncio
async def test_save_material_attaches_effective_title_for_upload_response(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "WRITING_SUPPORTED_MATERIAL_TYPES", ".pdf,.docx")
    monkeypatch.setattr(settings, "WRITING_MAX_MATERIAL_SIZE_MB", 5)
    db = _FakeSession()
    service = WritingService(db=db)
    service._get_project_or_raise = AsyncMock(return_value=SimpleNamespace(id=100))  # type: ignore[method-assign]

    import fitz

    with fitz.open() as pdf:
        pdf.new_page()
        pdf_bytes = pdf.tobytes()
    result = await service.save_material(100, SimpleNamespace(), "其它.pdf", pdf_bytes)

    assert result.effective_title == "其它.pdf"
    assert result.image_assets == []
    assert db.added.title == "其它.pdf"
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_get_material_preview_path_rejects_non_pdf(tmp_path: pytest.TempPathFactory) -> None:
    doc_path = tmp_path / "说明材料.docx"
    doc_path.write_text("placeholder", encoding="utf-8")
    material = SimpleNamespace(
        id=51,
        project_id=100,
        file_path=str(doc_path),
    )
    db = SimpleNamespace(get=AsyncMock(return_value=material))
    service = WritingService(db=db)
    service._get_project_or_raise = AsyncMock(return_value=SimpleNamespace(id=100))  # type: ignore[method-assign]

    with pytest.raises(ServiceError, match="仅支持PDF预览"):
        await service.get_material_preview_path(100, 51, SimpleNamespace())


def test_material_tagger_fallback_prefers_effective_title() -> None:
    tagger = MaterialTagger(db=SimpleNamespace(), llm_client=SimpleNamespace())
    material = SimpleNamespace(
        title="其它.pdf",
        display_title="服务承诺",
    )

    result = tagger._fallback_tags(material)

    assert result["domain"] == "服务承诺"
    assert "服务承诺" in result["keywords"]


@pytest.mark.asyncio
async def test_fill_data_records_inherited_material_in_source_ids() -> None:
    llm_client = SimpleNamespace(chat=AsyncMock(return_value="<p>公司简介已消费</p>"))
    composer = SectionComposer(db=None, llm_client=llm_client)
    inherited_material = SimpleNamespace(
        id=308,
        title="公司简介.docx",
        material_type=SimpleNamespace(value="text"),
        resolved_label="公司简介",
        file_name_label="公司简介",
        content="公司成立于2012年，主营业务覆盖系统设计与运维服务。",
        metadata_tags=json.dumps({"domain": "企业概况"}, ensure_ascii=False),
    )
    composer._load_bound_materials = AsyncMock(return_value=[inherited_material])  # type: ignore[method-assign]
    composer._load_bound_clauses = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_material_chunk_samples = AsyncMock(return_value={308: ["公司成立于2012年，主营业务覆盖系统设计与运维服务。"]})  # type: ignore[method-assign]
    composer._load_material_images = AsyncMock(return_value=[])  # type: ignore[method-assign]

    node = SimpleNamespace(
        id=300,
        title="公司基本情况简介",
        depth=1,
        chapter_profile=json.dumps(
            {"semantic_role": "介绍企业概况", "expects": ["主营业务"]},
            ensure_ascii=False,
        ),
    )

    payload = await composer._fill_data(node, industry_profile=None, number_map={300: "一、"})

    assert "公司简介已消费" in payload.content
    assert payload.source_material_ids == [308]


@pytest.mark.asyncio
async def test_trigger_match_returns_without_waiting_for_outline_vectorize(monkeypatch: pytest.MonkeyPatch) -> None:
    service = WritingService(SimpleNamespace())
    service._get_project_or_raise = AsyncMock(return_value=SimpleNamespace(id=567, owner_id=1))  # type: ignore[method-assign]
    service._vectorize_outline_nodes = AsyncMock(return_value=33)  # type: ignore[method-assign]

    dispatch = AsyncMock(return_value=("asyncio", None))
    monkeypatch.setattr("app.core.tasks.is_task_active", lambda _name: False)
    monkeypatch.setattr(
        "app.services.writing.match_service.WritingClauseReadinessService.ensure_visible_clauses_confirmed",
        AsyncMock(),
    )
    monkeypatch.setattr("app.services.writing.material_billing_gate.ensure_writing_billing_hold", AsyncMock())
    monkeypatch.setattr("app.services.retry_service.RetryService.begin_attempt", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr("app.services.retry_service.RetryService.attach_dispatch_result", AsyncMock())
    monkeypatch.setattr("app.core.tasks.dispatch_background_job", dispatch)

    result = await service.trigger_match(567, SimpleNamespace())

    assert result["task_name"] == "writing_match_567"
    service._vectorize_outline_nodes.assert_not_awaited()
    dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_material_bulk_deletes_children_images_and_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    material = SimpleNamespace(id=130, project_id=599)
    execute_mock = AsyncMock(side_effect=[
        _ScalarRows([(130,), (131,), (132,)]),
        SimpleNamespace(rowcount=11),
        SimpleNamespace(rowcount=5),
        SimpleNamespace(rowcount=3),
    ])
    db = SimpleNamespace(
        get=AsyncMock(return_value=material),
        execute=execute_mock,
        commit=AsyncMock(),
        delete=AsyncMock(),
    )
    service = WritingService(db=db)
    service._get_project_or_raise = AsyncMock(return_value=SimpleNamespace(id=599))  # type: ignore[method-assign]

    deleted_ids: list[int] = []

    class _FakeIndexer:
        def __init__(self, _db):
            self.db = _db

        async def delete_material_chunks(self, material_id: int) -> int:
            deleted_ids.append(material_id)
            return 1

    monkeypatch.setattr(
        "app.core.writer.material_indexer.MaterialIndexer",
        _FakeIndexer,
    )

    await service.delete_material(599, 130, SimpleNamespace())

    assert deleted_ids == [130, 131, 132]
    db.delete.assert_not_called()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_match_task_marks_zero_bindings_as_matched_with_empty_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = SimpleNamespace(
        id=567,
        project_type="bid_writing",
        status="outlined",
        error_message=None,
    )
    fake_db = SimpleNamespace(
        get=AsyncMock(return_value=project),
        commit=AsyncMock(),
    )
    heartbeat_events: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "app.core.tasks.writing_match_task.async_session_factory",
        lambda: _AsyncSessionContext(fake_db),
    )
    monkeypatch.setattr(
        "app.core.tasks.update_heartbeat",
        lambda _task_name, stage="", progress="", **_kw: heartbeat_events.append((stage, progress)),
    )
    monkeypatch.setattr(
        "app.services.writing_service.WritingService._vectorize_outline_nodes",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        "app.core.writer.material_matcher.get_material_matcher",
        lambda **_kw: SimpleNamespace(match=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(
        "app.core.ai.release_project_binding",
        AsyncMock(),
    )
    monkeypatch.setattr("app.services.retry_service.RetryService.mark_task_running", AsyncMock())
    monkeypatch.setattr("app.services.retry_service.RetryService.mark_task_completed", AsyncMock())
    monkeypatch.setattr("app.core.tasks.writing_match_task.prepare_binding_rematch", AsyncMock(return_value=0))
    monkeypatch.setattr("app.services.express_flow.dispatch.queue_express_advance_if_needed", AsyncMock())

    await run_writing_match_task(567)

    assert project.status == "matched" or getattr(project.status, "value", None) == "matched"
    assert any("当前无可用绑定" in progress for _stage, progress in heartbeat_events)


@pytest.mark.asyncio
async def test_vectorize_outline_nodes_emits_progress_during_long_running_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node1 = SimpleNamespace(
        id=11,
        title="项目概述",
        parent_id=None,
        chapter_profile=None,
        embedding=None,
    )
    node2 = SimpleNamespace(
        id=12,
        title="服务方案",
        parent_id=11,
        chapter_profile=None,
        embedding=None,
    )
    fake_db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarRows([node1, node2]),
                _ScalarRows([node1, node2]),
            ]
        ),
        commit=AsyncMock(),
    )
    service = WritingService(fake_db)
    progress_events: list[tuple[int, int, str]] = []

    async def fake_encode(texts, initial_batch_size, progress_callback):
        assert len(texts) == 2
        assert initial_batch_size == 2
        progress_callback(1, 2)
        progress_callback(2, 2)
        return np.array([[0.1, 0.2], [0.3, 0.4]])

    monkeypatch.setattr(
        "app.core.vectorizer.encode_gate.safe_encode",
        fake_encode,
    )

    count = await service._vectorize_outline_nodes(
        567,
        progress_callback=lambda current, total, message: progress_events.append(
            (current, total, message)
        ),
    )

    assert count == 2
    fake_db.commit.assert_awaited_once()
    assert node1.embedding == [0.1, 0.2]
    assert node2.embedding == [0.3, 0.4]
    assert any("已加载待向量目录节点" in message for _, _, message in progress_events)
    assert any("已构造目录向量文本" in message for _, _, message in progress_events)
    assert any("目录向量化 1/2" in message for _, _, message in progress_events)
    assert any("目录向量回写完成，准备提交" in message for _, _, message in progress_events)
    assert any("目录向量准备完成 2 个节点" in message for _, _, message in progress_events)


def test_company_profile_resolved_label_bias_overrides_wrong_domain() -> None:
    tagger = MaterialTagger(db=SimpleNamespace(commit=AsyncMock()), llm_client=SimpleNamespace())

    normalized = tagger._apply_resolved_label_bias(
        {
            "domain": "企业资质",
            "bid_purpose": "",
            "exclusive_section": "投标人基本情况",
            "applicable_sections": ["投标人基本情况"],
            "inapplicable_sections": ["报价"],
            "entities": [],
            "keywords": [],
        },
        resolved_label="公司简介",
        material_type="text",
    )

    assert normalized["domain"] == "企业概况"
    assert normalized["exclusive_section"] is None
    assert normalized["bid_purpose"] == "企业基本情况与综合实力介绍材料"
    assert normalized["keywords"][0] == "公司简介"


def test_long_text_bias_overrides_domain_for_service_and_management() -> None:
    tagger = MaterialTagger(db=SimpleNamespace(commit=AsyncMock()), llm_client=SimpleNamespace())

    service = tagger._apply_resolved_label_bias(
        {
            "domain": "其他",
            "bid_purpose": "",
            "exclusive_section": "服务承诺",
            "applicable_sections": ["服务承诺"],
            "inapplicable_sections": [],
            "entities": [],
            "keywords": [],
        },
        resolved_label="服务承诺",
        material_type="text",
    )
    management = tagger._apply_resolved_label_bias(
        {
            "domain": "其他",
            "bid_purpose": "",
            "exclusive_section": "管理制度",
            "applicable_sections": ["管理制度"],
            "inapplicable_sections": [],
            "entities": [],
            "keywords": [],
        },
        resolved_label="管理制度",
        material_type="text",
    )

    assert service["domain"] == "服务承诺"
    assert service["exclusive_section"] is None
    assert "响应机制" in service["bid_purpose"]
    assert management["domain"] == "管理制度"
    assert management["exclusive_section"] is None
    assert "执行机制" in management["bid_purpose"]


def test_material_db_content_no_longer_truncates_at_50000() -> None:
    full_text = "A" * 60050

    stored = WritingService._build_material_db_content(full_text)

    assert stored is not None
    assert len(stored) == 60050
