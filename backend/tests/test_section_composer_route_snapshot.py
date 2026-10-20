"""Task 6 Phase 1 快照: 锁定 SectionComposer 拆分前后的对外契约。

设计目的:
- Task 6 将 section_composer.py (2712 行) 拆分为 thin orchestrator + composer/ 子包
  7 个纯函数模块。本快照充当"拆分零退化"的契约锚点。
- 聚焦三类不可变契约: (a) 模块级出口符号; (b) 三条路由 ComposePayload schema;
  (c) Gate flag / CoVe / 占位符净化的输出结构。
- 不依赖真实 DB / LLM, 通过 monkey patch composer 实例方法验证签名与输出形态。

如本文件失败, 说明拆分破坏了 SectionComposer 的对外可见契约, 必须立刻回滚。
"""

from __future__ import annotations
from tests.synthetic_data import synthetic_text

import asyncio
import json
import os
from dataclasses import dataclass, fields
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)


# ---------------------------------------------------------------------------
# 1. 模块级出口符号契约
# ---------------------------------------------------------------------------


class TestModuleExportContract:
    """section_composer 模块级符号必须可从原路径 import (拆分后保留 re-export)。"""

    def test_exports_section_composer_class(self) -> None:
        from app.core.writer.section_composer import SectionComposer
        assert isinstance(SectionComposer, type)

    def test_exports_compose_payload_dataclass(self) -> None:
        from app.core.writer.section_composer import ComposePayload
        # dataclass 三字段顺序锁定
        field_names = [f.name for f in fields(ComposePayload)]
        assert field_names == [
            "content",
            "source_material_ids",
            "source_clause_ids",
            "gate_flags",
            "bound_material_ids",
            "selected_material_ids",
            "consumption_trace",
            "section_plan_id",
            "planning_mode",
            "planning_failure_code",
        ]

    def test_exports_material_context_build_result_dataclass(self) -> None:
        from app.core.writer.section_composer import MaterialContextBuildResult
        field_names = [f.name for f in fields(MaterialContextBuildResult)]
        assert field_names == [
            "text",
            "used_material_ids",
            "bound_materials",
            "used_materials",
            "priority_materials_used",
            "priority_segments_used",
            "truncated_materials",
        ]

    def test_exports_sanitize_residual_placeholders(self) -> None:
        from app.core.writer.section_composer import sanitize_residual_placeholders
        assert callable(sanitize_residual_placeholders)

    def test_exports_get_section_composer_factory(self) -> None:
        from app.core.writer.section_composer import get_section_composer
        assert callable(get_section_composer)

    def test_exports_three_prompt_wrappers(self) -> None:
        from app.core.writer.section_composer import (
            get_data_fill_prompt,
            get_free_writing_prompt,
            get_summarize_prompt,
        )
        assert callable(get_data_fill_prompt)
        assert callable(get_free_writing_prompt)
        assert callable(get_summarize_prompt)

    def test_doc_renderer_can_import_sanitize(self) -> None:
        """doc_renderer.py:39 依赖此 import 路径 (出口闸门)"""
        from app.core.writer.doc_renderer import sanitize_residual_placeholders as a
        from app.core.writer.section_composer import sanitize_residual_placeholders as b
        assert a is b


# ---------------------------------------------------------------------------
# 2. ComposePayload 结构与三条路由返回 schema 契约
# ---------------------------------------------------------------------------


class TestComposePayloadSchema:

    def test_compose_payload_can_be_constructed_with_three_args(self) -> None:
        from app.core.writer.section_composer import ComposePayload
        payload = ComposePayload(
            content="<p>x</p>",
            source_material_ids=[1, 2],
            source_clause_ids=[10],
        )
        assert payload.content == "<p>x</p>"
        assert payload.source_material_ids == [1, 2]
        assert payload.source_clause_ids == [10]


# ---------------------------------------------------------------------------
# 3. sanitize_residual_placeholders 行为快照 (闸门)
# ---------------------------------------------------------------------------


class TestSanitizeBehaviorSnapshot:
    """与 test_writer_placeholder_leak.py 互补: 此处锁定四类典型样本的输出, 拆分后必须 byte-equal。"""

    def test_whitelist_replacement_byte_equal(self) -> None:
        from app.core.writer.section_composer import sanitize_residual_placeholders
        assert sanitize_residual_placeholders("{投标人}") == "[待补充:投标人名称]"
        assert sanitize_residual_placeholders("{项目名称}") == "[待补充:项目名称]"

    def test_fallback_chinese_byte_equal(self) -> None:
        from app.core.writer.section_composer import sanitize_residual_placeholders
        assert sanitize_residual_placeholders("{开户行}") == "[待补充:开户行]"

    def test_fallback_english_underscore_byte_equal(self) -> None:
        from app.core.writer.section_composer import sanitize_residual_placeholders
        assert sanitize_residual_placeholders("{node_title}") == "[待补充:node_title]"
        assert sanitize_residual_placeholders("{company_profile}") == "[待补充:company_profile]"

    def test_idempotent_byte_equal(self) -> None:
        from app.core.writer.section_composer import sanitize_residual_placeholders
        cleaned = "[待补充:投标人名称] 与 [待补充:开户行]"
        assert sanitize_residual_placeholders(cleaned) == cleaned

    def test_empty_braces_preserved_byte_equal(self) -> None:
        from app.core.writer.section_composer import sanitize_residual_placeholders
        text = "空花括号 {} 应保持原样"
        assert sanitize_residual_placeholders(text) == text

    def test_long_brace_content_preserved_byte_equal(self) -> None:
        """正则 _PLACEHOLDER_FALLBACK_RE 限制为 1-20 字符, 超出不应被替换。"""
        from app.core.writer.section_composer import sanitize_residual_placeholders
        # 21 个汉字, 超过 _PLACEHOLDER_FALLBACK_RE 的 {1,20} 上限
        text = "{这是超过二十一个字符的非常非常长内容不应被匹配}"
        assert sanitize_residual_placeholders(text) == text


# ---------------------------------------------------------------------------
# 4. _aggregate_gate_flags JSON 结构契约 (P2.1b draft.gate_flags 字段格式)
# ---------------------------------------------------------------------------


class TestGateFlagsAggregationSchema:

    def test_empty_returns_none(self) -> None:
        from app.core.writer.section_composer import SectionComposer
        assert SectionComposer._aggregate_gate_flags([]) is None
        assert SectionComposer._aggregate_gate_flags(None) is None  # type: ignore[arg-type]

    def test_pass_verdict_filtered_out(self) -> None:
        from app.core.writer.section_composer import SectionComposer
        verdicts = [
            SimpleNamespace(
                gate_name="GateA", verdict="pass",
                suggested_patch={}, reason="", severity="", trace={},
            )
        ]
        assert SectionComposer._aggregate_gate_flags(verdicts) is None

    def test_warn_verdict_keeps_required_keys(self) -> None:
        from app.core.writer.section_composer import SectionComposer
        verdicts = [
            SimpleNamespace(
                gate_name="GateA",
                verdict="warn",
                suggested_patch={"failure_state": "needs_review", "_internal": "skip"},
                reason="x" * 300,  # 长度截断到 200
                severity="warn",
                trace={
                    "fill_rate": 0.5,
                    "ai_phrase_count": 3,
                    "extra_should_be_dropped": "y",
                },
            )
        ]
        result = SectionComposer._aggregate_gate_flags(verdicts)
        assert result is not None
        entry = result["GateA"]
        # 顶层字段必须固定
        assert set(entry.keys()) == {"verdict", "severity", "reason", "patch", "trace"}
        assert entry["verdict"] == "warn"
        assert entry["severity"] == "warn"
        assert len(entry["reason"]) == 200  # 截断锁定
        # patch 过滤 _ 前缀字段
        assert entry["patch"] == {"failure_state": "needs_review"}
        # trace 仅保留白名单 key
        assert "fill_rate" in entry["trace"]
        assert "ai_phrase_count" in entry["trace"]
        assert "extra_should_be_dropped" not in entry["trace"]


# ---------------------------------------------------------------------------
# 5. _is_writer_critical_commitment / _merge_gate_flags 公共契约
# ---------------------------------------------------------------------------


class TestWriterCommitmentClassifier:

    def test_title_keyword_hits_critical(self) -> None:
        from app.core.writer.section_composer import (
            ComposePayload, SectionComposer,
        )
        node = SimpleNamespace(title="资质承诺", node_type=None)
        payload = ComposePayload("正文", [], [])
        is_critical, kw = SectionComposer._is_writer_critical_commitment(node, payload)
        assert is_critical is True
        assert kw in {"资质", "承诺"}

    def test_non_critical_returns_default_keyword(self) -> None:
        from app.core.writer.section_composer import (
            ComposePayload, SectionComposer,
        )
        node = SimpleNamespace(title="公司简介", node_type=None)
        payload = ComposePayload("我司成立于 2010 年。", [], [])
        is_critical, kw = SectionComposer._is_writer_critical_commitment(node, payload)
        assert is_critical is False
        assert kw == "关键承诺"

    def test_merge_gate_flags_keeps_both_keys(self) -> None:
        from app.core.writer.section_composer import SectionComposer
        base = {"GateA": {"verdict": "warn"}}
        extra = {"WriterCoVe": {"verdict": "disagree"}}
        merged = SectionComposer._merge_gate_flags(base, extra)
        assert merged is not None
        assert set(merged.keys()) == {"GateA", "WriterCoVe"}

    def test_merge_gate_flags_both_none_returns_none(self) -> None:
        from app.core.writer.section_composer import SectionComposer
        assert SectionComposer._merge_gate_flags(None, None) is None


# ---------------------------------------------------------------------------
# 6. 三条路由的 ComposePayload 返回语义 (mock _load_*, 验证 payload 结构)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fixed_template_route_returns_compose_payload_with_sanitized_content() -> None:
    """fixed_template 路由必须返回 ComposePayload 且 content 已通过出口闸门"""
    from app.core.writer.section_composer import ComposePayload, SectionComposer

    composer = SectionComposer(db=None, llm_client=SimpleNamespace())
    composer._find_bound_clause = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            id=42,
            raw_text="投标函: {投标人} {日期}",
            sub_type="fixed_template",
        )
    )
    composer._find_fallback_template_clause = AsyncMock(return_value=None)  # type: ignore[method-assign]
    composer._load_bound_materials = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_material_images = AsyncMock(return_value=[])  # type: ignore[method-assign]
    # fill_template 成功路径回填直接绑定约束(db=None, 需 mock)
    composer._load_bound_clauses = AsyncMock(return_value=[])  # type: ignore[method-assign]

    node = SimpleNamespace(
        id=1, title="第1章 投标函", project_id=10,
        node_type=None,
    )
    payload = await composer._fill_template(node)

    assert isinstance(payload, ComposePayload)
    assert "[待补充:投标人名称]" in payload.content  # 闸门生效
    assert "{投标人}" not in payload.content
    assert payload.source_material_ids == []
    assert 42 in payload.source_clause_ids


def test_append_figure_tags_skips_existing_image_id() -> None:
    """LLM 已按图片元信息插入 figure 时，后端兜底追加不能重复插入同一图片。"""
    from app.core.writer.section_composer import SectionComposer

    html = (
        '<p>合同扫描件如下：</p>'
        '<figure data-image-id="802"><figcaption>合同扫描件（第1页）</figcaption></figure>'
    )
    image = SimpleNamespace(
        id=802,
        image_index=0,
        caption="合同扫描件（第1页）",
    )

    result = SectionComposer._append_figure_tags(html, [image])

    assert result.count('data-image-id="802"') == 1


def test_append_figure_tags_reorders_identity_cards_to_layout_tail() -> None:
    """身份证图片应统一移到章节末尾，并按法人/代理、正/反面排序。"""
    from app.core.writer.section_composer import SectionComposer
    from app.models.writing_material_image import ImageType

    html = (
        "<p>正文。</p>"
        '<figure data-image-id="2"><figcaption>旧顺序：法人反面</figcaption></figure>'
        "<p>结尾。</p>"
    )
    images = [
        SimpleNamespace(
            id=4,
            image_index=3,
            material_id=1,
            caption="委托代理人身份证反面",
            vl_content="签发机关 XX公安局 有效期限 2020-2030",
            structured_fields="{}",
            image_type=ImageType.personnel_cert,
            business_subtype="id_card",
        ),
        SimpleNamespace(
            id=2,
            image_index=1,
            material_id=1,
            caption="法定代表人身份证反面",
            vl_content="签发机关 XX公安局 有效期限 2020-2030",
            structured_fields="{}",
            image_type=ImageType.personnel_cert,
            business_subtype="id_card",
        ),
        SimpleNamespace(
            id=3,
            image_index=2,
            material_id=1,
            caption="委托代理人身份证正面",
            vl_content="",
            structured_fields=synthetic_text('{"姓名": "<PERSON_B>", "证件号码": "', '110101', '199001', '011234', '"}'),
            image_type=ImageType.personnel_cert,
            business_subtype="id_card",
        ),
        SimpleNamespace(
            id=1,
            image_index=0,
            material_id=1,
            caption="法定代表人身份证正面",
            vl_content="",
            structured_fields=synthetic_text('{"姓名": "<PERSON_A>", "证件号码": "', '110101', '198001', '011234', '"}'),
            image_type=ImageType.personnel_cert,
            business_subtype="id_card",
        ),
    ]

    result = SectionComposer._append_figure_tags(html, images)

    assert result.count('data-image-id="2"') == 1
    assert result.rfind("<p>结尾。</p>") < result.index('data-image-id="1"')
    assert (
        result.index('data-image-id="1"')
        < result.index('data-image-id="2"')
        < result.index('data-image-id="3"')
        < result.index('data-image-id="4"')
    )


@pytest.mark.asyncio
async def test_data_fill_route_returns_compose_payload_calling_llm_once() -> None:
    """data_fill 路由必须调用 LLM 一次并返回 ComposePayload"""
    from app.core.writer.section_composer import ComposePayload, SectionComposer

    llm_client = SimpleNamespace(chat=AsyncMock(return_value="<p>章节填充结果</p>"))
    composer = SectionComposer(db=None, llm_client=llm_client)
    composer._load_bound_materials = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_bound_clauses = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_material_chunk_samples = AsyncMock(return_value={})  # type: ignore[method-assign]
    composer._load_material_images = AsyncMock(return_value=[])  # type: ignore[method-assign]

    node = SimpleNamespace(
        id=2, title="人员组成表", depth=1,
        target_word_count=500, chapter_profile=None,
    )
    payload = await composer._fill_data(
        node, industry_profile=None, number_map={2: "二、"},
    )

    assert isinstance(payload, ComposePayload)
    assert "章节填充结果" in payload.content
    llm_client.chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_free_writing_route_returns_compose_payload_calling_llm_once(
    monkeypatch,
) -> None:
    """free_writing 路由必须调用 LLM 一次并返回 ComposePayload。

    W1 引入"生成后字数闭环"会在字数偏差超出区间时追加一次 length adjust LLM
    调用；本快照测试关心的是路由 LLM 调用契约，因此 monkeypatch 关闭闭环
    （MIN_RATIO=0）以聚焦原契约。length 闭环的行为已在
    ``test_writer_length_control.py`` 中独立覆盖。
    """
    from app.core.config import settings as _settings
    from app.core.writer.section_composer import ComposePayload, SectionComposer

    monkeypatch.setattr(_settings, "WRITING_WORD_COUNT_MIN_RATIO", 0.0)
    monkeypatch.setattr(_settings, "WRITING_DE_AI_REWRITE_ENABLED", False)

    llm_client = SimpleNamespace(chat=AsyncMock(return_value="<p>自由论述输出</p>"))
    composer = SectionComposer(db=None, llm_client=llm_client)
    composer._load_bound_materials = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_bound_clauses = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_material_chunk_samples = AsyncMock(return_value={})  # type: ignore[method-assign]
    composer._load_material_images = AsyncMock(return_value=[])  # type: ignore[method-assign]

    node = SimpleNamespace(
        id=3, title="服务方案", depth=1,
        target_word_count=2000, chapter_profile=None,
    )
    payload = await composer._compose_free(
        node, industry_profile=None, previous_summaries=[], number_map={3: "三、"},
    )

    assert isinstance(payload, ComposePayload)
    assert "自由论述输出" in payload.content
    llm_client.chat.assert_awaited_once()


# ---------------------------------------------------------------------------
# 7. CoVe disagree -> WriterCoVe gate_flags 条目结构契约
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maybe_run_writer_cove_disagree_returns_writer_cove_entry() -> None:
    """关键承诺章节 CoVe disagree 时, 必须返回 WriterCoVe 条目, 含 needs_review patch"""
    from app.core.ai.critic.cove import CoveAnswer, CoveResult
    from app.core.writer.section_composer import (
        ComposePayload, SectionComposer,
    )

    composer = SectionComposer.__new__(SectionComposer)
    composer.db = None  # type: ignore[attr-defined]
    composer._material_pool_size_cache = {}  # type: ignore[attr-defined]
    composer._build_writer_judge_evidence = AsyncMock(  # type: ignore[attr-defined]
        return_value={"bound_materials": [], "hard_constraints": []},
    )

    node = SimpleNamespace(id=1, title="资质承诺", node_type=None)
    payload = ComposePayload(
        content="承诺工期 30 天, ¥100 万保证金。",
        source_material_ids=[],
        source_clause_ids=[],
    )

    async def fake_run_cove(**kwargs: Any) -> CoveResult:
        return CoveResult(
            trigger="writer_commitment",
            outcome="disagree",
            answers=[
                CoveAnswer(
                    question_id="q1", answer="no",
                    evidence_page=0, quote="x", valid=True,
                ),
            ],
            should_escalate=True,
            reason="",
        )

    with patch(
        "app.core.ai.critic.cove.should_use_cove", return_value=True,
    ), patch(
        "app.core.ai.ai_factory.get_judge_client", return_value=object(),
    ), patch(
        "app.core.ai.critic.cove.run_cove", side_effect=fake_run_cove,
    ):
        result = await composer._maybe_run_writer_cove(123, node, payload)

    assert result is not None
    assert "WriterCoVe" in result
    entry = result["WriterCoVe"]
    # 字段顺序与 _maybe_run_writer_cove 实现锁定 (severity / patch / trace)
    assert entry["verdict"] == "disagree"
    assert entry["severity"] == "advisory"
    assert entry["patch"] == {"failure_state": "needs_review"}
    assert entry["trace"]["trigger"] == "writer_commitment"


# ---------------------------------------------------------------------------
# 8. _save_draft 出口闸门契约 (: LLM {xxx} 落库前必须净化)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_draft_sanitizes_curly_placeholders_before_persistence() -> None:
    """_save_draft 是 LLM 路径的占位符出口闸门, 落库前必须替换 {xxx} 为 [待补充:xxx]"""
    from app.core.writer.section_composer import SectionComposer

    composer = SectionComposer(db=None, project_id=0)
    captured: dict[str, Any] = {}

    result_holder = MagicMock()
    result_holder.scalars.return_value.all.return_value = []

    async def _execute(*args: Any, **kwargs: Any) -> Any:
        return result_holder

    fake_db = SimpleNamespace(
        execute=_execute,
        add=lambda d: captured.setdefault("draft", d),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    composer.db = fake_db  # type: ignore[assignment]

    node = SimpleNamespace(id=99, title="测试章节")
    await composer._save_draft(
        node,
        "公司 {company_profile} 提供 {service_commitment}",
        source_material_ids=[1],
        source_clause_ids=[10],
    )

    draft = captured["draft"]
    assert "{company_profile}" not in draft.content
    assert "{service_commitment}" not in draft.content
    assert "[待补充:company_profile]" in draft.content
    assert "[待补充:service_commitment]" in draft.content


@pytest.mark.asyncio
async def test_save_draft_applies_gate_failure_state_patch() -> None:
    """Gate/CoVe 标记 needs_review 时, 草稿状态必须进入人工复核而非 completed。"""
    from app.core.writer.section_composer import SectionComposer

    composer = SectionComposer(db=None, project_id=0)
    captured: dict[str, Any] = {}

    result_holder = MagicMock()
    result_holder.scalars.return_value.all.return_value = []

    async def _execute(*args: Any, **kwargs: Any) -> Any:
        return result_holder

    fake_db = SimpleNamespace(
        execute=_execute,
        add=lambda d: captured.setdefault("draft", d),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    composer.db = fake_db  # type: ignore[assignment]

    node = SimpleNamespace(id=100, title="设备清单")
    await composer._save_draft(
        node,
        "<p>设备清单内容</p>",
        source_material_ids=[],
        source_clause_ids=[10],
        gate_flags={
            "TableFieldFillRateGate": {
                "verdict": "warn",
                "patch": {
                    "failure_state": "needs_review",
                    "table_fill_rate_violated": True,
                },
            },
        },
    )

    draft = captured["draft"]
    assert draft.failure_state == "needs_review"
    assert draft.review_required == 1


# ---------------------------------------------------------------------------
# 9. 关键内部常量稳定性 (拆分后必须保留同名同值, 子模块可 re-import)
# ---------------------------------------------------------------------------


class TestInternalConstantsStability:

    def test_long_text_specialization_rules_keys_preserved(self) -> None:
        from app.core.writer.section_composer import _LONG_TEXT_SPECIALIZATION_RULES
        # 当前业务依赖的四类高价值文本场景
        expected_keys = {
            "company_profile", "performance_summary", "service_commitment",
        }
        assert expected_keys.issubset(set(_LONG_TEXT_SPECIALIZATION_RULES.keys()))

    def test_placeholder_whitelist_map_size_lower_bound(self) -> None:
        from app.core.writer.section_composer import _PLACEHOLDER_WHITELIST_MAP
        # 至少 7 个白名单映射 (公司/投标人/项目/日期/法人/电话/地址)
        assert len(_PLACEHOLDER_WHITELIST_MAP) >= 7
        assert "{投标人}" in _PLACEHOLDER_WHITELIST_MAP
        assert "{项目名称}" in _PLACEHOLDER_WHITELIST_MAP

    def test_critical_keywords_remain_classifiable(self) -> None:
        from app.core.writer.section_composer import SectionComposer
        # _is_writer_critical_commitment 依赖的关键词集合契约
        title_keywords = SectionComposer._WRITER_CRITICAL_TITLE_KEYWORDS
        assert "资质" in title_keywords
        assert "承诺" in title_keywords


class TestComposePipelineSplitContract:
    """compose_all 必须薄委托到 ComposePipeline，阶段路由不得再堆在上帝类里。"""

    def test_pipeline_module_exports_required_methods(self) -> None:
        from app.core.writer.composer.compose_pipeline import (
            ComposePipeline,
            ComposeRunContext,
            ComposeSectionsRequest,
        )

        assert hasattr(ComposePipeline, "compose_sections")
        assert hasattr(ComposePipeline, "compose_fixed_sections")
        assert hasattr(ComposePipeline, "compose_data_sections")
        assert hasattr(ComposePipeline, "compose_free_sections")
        assert hasattr(ComposePipeline, "finalize_compose")
        assert ComposeRunContext is not None
        assert ComposeSectionsRequest is not None
        for banned in ("run", "execute", "process", "handle"):
            assert not hasattr(ComposePipeline, banned)

    def test_compose_all_is_thin_delegate(self) -> None:
        import inspect

        from app.core.writer.section_composer import SectionComposer

        source = inspect.getsource(SectionComposer.compose_all)
        assert "ComposePipeline" in source
        assert "compose_sections" in source
        assert "阶段1: fixed_template" not in source
        assert "_fill_template" not in source
        after_def = source.split("async def compose_all", 1)[1]
        body = after_def.split('"""', 2)[-1]
        body_lines = [line for line in body.splitlines() if line.strip()]
        assert len(body_lines) <= 20
