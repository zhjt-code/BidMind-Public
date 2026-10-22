import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.writer.composer.binding_loader import (
    _is_acceptable_template_clause,
    extract_fixed_template_keywords,
    find_fallback_template_clause,
    normalize_fixed_template_lookup,
)
from app.core.writer.composer.template_route import _is_template_source_usable
from app.core.writer.fixed_template.models import FixedTemplateEntry
from app.core.writer.outline_generator import OutlineGenerator


def _clause(
    title: str,
    template_key: str,
    raw_text: str,
    volume_type: str | None = None,
) -> SimpleNamespace:
    entry = FixedTemplateEntry(
        template_key=template_key,
        title=title,
        aliases=(title,),
        source_page_start=1,
        source_page_end=1,
        raw_text=raw_text,
        field_slots=("投标人", "日期"),
        signature_slots=("盖章", "日期"),
        confidence=0.95,
        source="llm_only",
        volume_type=volume_type,
    )
    return SimpleNamespace(
        content=f"{title}固定模板",
        raw_text=raw_text,
        metadata_json={
            "template_key": template_key,
            "target_section_hint": title,
            "fixed_template_snapshot": entry.snapshot_dict(),
        },
    )


def test_outline_infers_legal_representative_certificate_as_fixed_template() -> None:
    assert OutlineGenerator._infer_node_type("法定代表人身份证明") == "fixed_template"


def test_binding_rejects_mismatched_concrete_template() -> None:
    clause = _clause(
        "投标保证金缴纳证明",
        "bid_security_certificate",
        "三、投标保证金缴纳证明\n投 标 人: (盖单位章)\n日 期: 年 月 日",
    )

    assert not _is_acceptable_template_clause("法定代表人授权委托书", clause)


def test_compose_guard_rejects_generic_template_requirement() -> None:
    template = "投标文件必须毫无例外地使用招标文件所提供的投标文件全部格式，表格可以按同样格式扩展。"

    assert not _is_template_source_usable("投标函", template, None)


def test_compose_guard_accepts_matching_concrete_template() -> None:
    template = (
        "五、法定代表人授权委托书\n"
        "本人系企业法定代表人，特授权代表我单位办理投标。\n"
        "被授权人签名: 职务:\n"
        "法定代表人签名: 职务:\n"
        "投标人(公章)\n"
        "日 期: 年 月 日"
    )
    snapshot = {
        "title": "法定代表人授权委托书",
        "template_key": "legal_representative_authorization",
    }

    assert _is_template_source_usable("法定代表人授权委托书", template, snapshot)


def test_binding_rejects_volume_type_mismatch() -> None:
    """节点 volume=commercial 时不应选中 volume=technical 的同 key 模板。"""
    technical_clause = _clause(
        "投标函",
        "bid_letter",
        "一、投标函\n投 标 人: (盖单位章)\n日 期: 年 月 日",
        volume_type="technical",
    )
    assert not _is_acceptable_template_clause(
        "投标函",
        technical_clause,
        node_volume_type="commercial",
    )


def test_binding_accepts_volume_type_match() -> None:
    technical_clause = _clause(
        "投标函",
        "bid_letter",
        "一、投标函\n投 标 人: (盖单位章)\n日 期: 年 月 日",
        volume_type="technical",
    )
    assert _is_acceptable_template_clause(
        "投标函",
        technical_clause,
        node_volume_type="technical",
    )


def _stub_composer(clauses: list) -> MagicMock:
    """构造 fallback 测试用最小可用 composer mock。"""
    composer = MagicMock()
    composer._db_lock = asyncio.Lock()

    scalars_obj = MagicMock()
    scalars_obj.all.return_value = clauses
    result_obj = MagicMock()
    result_obj.scalars.return_value = scalars_obj
    composer.db = MagicMock()
    composer.db.execute = AsyncMock(return_value=result_obj)

    composer._normalize_fixed_template_lookup = staticmethod(normalize_fixed_template_lookup)
    composer._extract_fixed_template_keywords = staticmethod(extract_fixed_template_keywords)
    return composer


@pytest.mark.asyncio
async def test_fallback_hits_by_template_key_when_raw_text_has_no_title() -> None:
    """raw_text 已被清洗为不含节点标题字符串时, 新主路径仍能按 template_key 命中。"""
    clause = _clause(
        "投标函",
        "bid_letter",
        "致:招标人\n投 标 人: (盖单位章)\n日 期: 年 月 日",
        volume_type="technical",
    )
    clause.id = 999
    composer = _stub_composer([clause])

    found = await find_fallback_template_clause(
        composer,
        project_id=1,
        node_title="投标函",
        node_volume_type="technical",
    )

    assert found is clause


@pytest.mark.asyncio
async def test_fallback_volume_mismatch_still_blocks_template_key_match() -> None:
    """新主路径必须仍然受 volume_type 守门保护, 不能跨 volume 命中。"""
    clause = _clause(
        "投标函",
        "bid_letter",
        "致:招标人\n投 标 人: (盖单位章)\n日 期: 年 月 日",
        volume_type="technical",
    )
    clause.id = 1000
    composer = _stub_composer([clause])

    found = await find_fallback_template_clause(
        composer,
        project_id=1,
        node_title="投标函",
        node_volume_type="commercial",
    )

    assert found is None


@pytest.mark.asyncio
async def test_fallback_legacy_substring_path_still_works_for_unknown_key() -> None:
    """节点标题不能被 infer_fixed_template_key 推断时, 旧子串路径仍兜底。"""
    clause = _clause(
        "其他特殊承诺函",
        "bid_letter",
        "其他特殊承诺函\n致:招标人\n投 标 人: (盖单位章)\n日 期: 年 月 日",
        volume_type=None,
    )
    clause.id = 1001
    composer = _stub_composer([clause])

    found = await find_fallback_template_clause(
        composer,
        project_id=1,
        node_title="其他特殊承诺函",
    )

    assert found is clause


def test_binding_volume_guard_skips_when_either_side_missing() -> None:
    """snapshot 或 node 任一 volume_type 缺失时维持向后兼容, 不强制匹配。"""
    untyped_clause = _clause(
        "投标函",
        "bid_letter",
        "一、投标函\n投 标 人: (盖单位章)\n日 期: 年 月 日",
        volume_type=None,
    )
    assert _is_acceptable_template_clause(
        "投标函",
        untyped_clause,
        node_volume_type="commercial",
    )

    typed_clause = _clause(
        "投标函",
        "bid_letter",
        "一、投标函\n投 标 人: (盖单位章)\n日 期: 年 月 日",
        volume_type="technical",
    )
    assert _is_acceptable_template_clause(
        "投标函",
        typed_clause,
        node_volume_type=None,
    )


def _rule_only_clause(
    title: str,
    template_key: str,
    raw_text: str,
    volume_type: str | None = None,
) -> SimpleNamespace:
    """构造 source=rule_only / is_complete_candidate=False 的兜底 clause。"""
    entry = FixedTemplateEntry(
        template_key=template_key,
        title=title,
        aliases=(title,),
        source_page_start=1,
        source_page_end=1,
        raw_text=raw_text,
        field_slots=("投标人",),
        signature_slots=("盖章",),
        confidence=0.55,
        source="rule_only",
        volume_type=volume_type,
    )
    snapshot = entry.snapshot_dict()
    assert snapshot["is_complete_candidate"] is False
    return SimpleNamespace(
        content=f"{title}固定模板",
        raw_text=raw_text,
        metadata_json={
            "template_key": template_key,
            "target_section_hint": title,
            "fixed_template_snapshot": snapshot,
        },
    )


def test_binding_accepts_rule_only_incomplete_candidate() -> None:
    """: source=rule_only 的兜底 entry 即使 is_complete_candidate=False 也应被放行,
 由 template_route 自动打 needs_review gate flag。"""
    clause = _rule_only_clause(
        "投标函",
        "bid_letter",
        "一、投标函\n致:招标人\n投 标 人: (盖单位章)\n日 期: 年 月 日",
        volume_type="technical",
    )
    assert _is_acceptable_template_clause(
        "投标函",
        clause,
        node_volume_type="technical",
    )


def test_binding_still_rejects_non_rule_only_incomplete_candidate() -> None:
    """非 rule_only 的 incomplete entry 仍应被拒绝, 避免幻觉模板透传。"""
    entry = FixedTemplateEntry(
        template_key="bid_letter",
        title="投标函",
        aliases=("投标函",),
        source_page_start=1,
        source_page_end=1,
        raw_text="一、投标函\n致:招标人\n投 标 人: (盖单位章)\n日 期: 年 月 日",
        field_slots=("投标人",),
        signature_slots=("盖章",),
        confidence=0.40,  # 低于 is_complete_candidate 阈值
        source="llm_only",
        volume_type="technical",
    )
    snapshot = entry.snapshot_dict()
    assert snapshot["is_complete_candidate"] is False
    clause = SimpleNamespace(
        content="投标函固定模板",
        raw_text=entry.raw_text,
        metadata_json={
            "template_key": "bid_letter",
            "target_section_hint": "投标函",
            "fixed_template_snapshot": snapshot,
        },
    )
    assert not _is_acceptable_template_clause(
        "投标函",
        clause,
        node_volume_type="technical",
    )


def test_binding_rejects_rule_only_when_volume_mismatch() -> None:
    """source=rule_only 的放行不应突破 volume_type 守门保护。"""
    technical_clause = _rule_only_clause(
        "投标函",
        "bid_letter",
        "一、投标函\n致:招标人\n投 标 人: (盖单位章)\n日 期: 年 月 日",
        volume_type="technical",
    )
    assert not _is_acceptable_template_clause(
        "投标函",
        technical_clause,
        node_volume_type="commercial",
    )
