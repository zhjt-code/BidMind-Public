""" 识别链防御测试。

覆盖:
- R1: 有快照时泛化检查不再被 LLM 概括 content 中的"全部格式"误杀
- R2: catalog->约束合并卷感知(页锚定认领, 未认领卷条目照常补建)
- R5: 碎片标题不再反向命中复合别名; match_template_title 子串覆盖率约束
- T4: custom key 全链(titles/绑定例外/目录升级)
- T8: 同卷模板唯一归属仲裁
"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.writer.clause_extractor import _match_catalog_entry
from app.core.writer.composer.binding_loader import _is_acceptable_template_clause
from app.core.writer.fixed_template.models import FixedTemplateEntry
from app.core.writer.fixed_template.ownership import (
    select_owner_node,
    template_affinity_score,
)
from app.core.writer.fixed_template.titles import (
    custom_template_key,
    infer_fixed_template_key,
    is_custom_template_key,
    match_template_title,
)
from app.schemas.writing import ExtractedClause

# ---------------------------------------------------------------------------
# R1: 泛化检查不再拼接 content
# ---------------------------------------------------------------------------

_BID_LETTER_TEXT = (
    "致：招标人\n1）我方将按招标文件的规定履行义务；\n"
    "投标方名称： （盖章）\n"
    "投标方法定代表人或授权委托人：（签字） 职务：\n日期： 年 月 日"
)


def _clause_with_snapshot(content: str, **snapshot_overrides) -> SimpleNamespace:
    snapshot = {
        "title": "投标函",
        "template_key": "bid_letter",
        "source": "llm_only",
        "is_complete_candidate": True,
        "volume_type": "technical",
        "raw_text_from_chunks": _BID_LETTER_TEXT,
        "raw_text_verbatim": _BID_LETTER_TEXT,
    }
    snapshot.update(snapshot_overrides)
    return SimpleNamespace(
        content=content,
        raw_text=_BID_LETTER_TEXT,
        metadata_json={
            "template_key": snapshot["template_key"],
            "fixed_template_snapshot": snapshot,
        },
    )


def test_generic_wording_in_content_does_not_kill_concrete_template() -> None:
    """2086 实证: LLM 概括 content 含"全部格式"时, 携带具体快照的约束不得被拒。"""
    clause = _clause_with_snapshot(
        "投标文件必须使用招标文件提供的全部格式模板，包括投标意向确认书、投标函、开标一览表等"
    )
    assert _is_acceptable_template_clause("投标函", clause, node_volume_type="technical")


def test_generic_source_text_still_rejected() -> None:
    """模板源文本本身是泛化格式要求时仍拒绝(检查对象修正不放松实质校验)。"""
    generic = "投标文件必须毫无例外地使用招标文件所提供的投标文件全部格式，表格可以按同样格式扩展。"
    clause = _clause_with_snapshot(
        "投标函固定模板",
        raw_text_from_chunks=generic,
        raw_text_verbatim=generic,
    )
    assert not _is_acceptable_template_clause("投标函", clause, node_volume_type="technical")


# ---------------------------------------------------------------------------
# R2: 卷感知合并
# ---------------------------------------------------------------------------


def _entry(key: str, title: str, volume: str, page: int) -> FixedTemplateEntry:
    return FixedTemplateEntry(
        template_key=key,
        title=title,
        aliases=(title,),
        source_page_start=page,
        source_page_end=page,
        raw_text=f"{title}模板正文, 投标人: (盖章), 日期: 年 月 日, 长度补齐补齐补齐补齐",
        confidence=0.95,
        source="llm_only",
        volume_type=volume,
    )


def test_match_catalog_entry_prefers_page_anchor() -> None:
    """同 key 双卷条目: 按 LLM 约束的页锚点认领对应卷条目。"""
    catalog = [
        _entry("bid_letter", "投标函", "technical", 41),
        _entry("bid_letter", "投标函", "commercial", 52),
    ]
    ext_technical = ExtractedClause(
        sub_type="fixed_template", content="投标函模板", raw_text="致:招标人",
        source_page=41, metadata={"target_section_hint": "投标函"},
    )
    ext_commercial = ExtractedClause(
        sub_type="fixed_template", content="投标函模板", raw_text="致:招标人",
        source_page=52, metadata={"target_section_hint": "投标函"},
    )
    assert _match_catalog_entry(ext_technical, catalog).volume_type == "technical"
    assert _match_catalog_entry(ext_commercial, catalog).volume_type == "commercial"


def test_unclaimed_volume_entry_survives_merge_semantics() -> None:
    """2086 实证语义锁: 一条 technical 页锚约束只认领 technical 条目,
    commercial 条目未被认领(合并循环会将其补为独立约束)。"""
    catalog = [
        _entry("bid_letter", "投标函", "technical", 41),
        _entry("bid_letter", "投标函", "commercial", 52),
    ]
    ext = ExtractedClause(
        sub_type="fixed_template", content="投标函模板", raw_text="致:招标人",
        source_page=41, metadata={"target_section_hint": "投标函"},
    )
    claimed = {id(_match_catalog_entry(ext, catalog))}
    unclaimed = [entry for entry in catalog if id(entry) not in claimed]
    assert len(unclaimed) == 1
    assert unclaimed[0].volume_type == "commercial"


# ---------------------------------------------------------------------------
# R5: 碎片推断收紧
# ---------------------------------------------------------------------------


def test_fragment_title_no_longer_hits_composite_alias() -> None:
    """2086 实证: "公司简介"/"人员配置表"不得再命中 bidder_basic_info。"""
    assert infer_fixed_template_key("公司简介") is None
    assert infer_fixed_template_key("人员配置表") is None


def test_high_coverage_fragment_still_matches() -> None:
    assert infer_fixed_template_key("基本情况表") == "bidder_basic_info"
    assert infer_fixed_template_key("投标人基本情况表") == "bidder_basic_info"
    assert infer_fixed_template_key("投标人基本情况及人员配置表及公司简介") == "bidder_basic_info"
    assert infer_fixed_template_key("投标函") == "bid_letter"
    assert infer_fixed_template_key("投标保证金缴纳证明复印件(盖单位章)") == "bid_security_certificate"


def test_match_template_title_rejects_fragment_substring() -> None:
    result = match_template_title("公司简介", "投标人基本情况及人员配置表及公司简介", None)
    assert not result.matched


def test_fallback_no_longer_matches_fragment_to_basic_info_clause() -> None:
    """绑定守门: "公司简介"节点不得接受基本情况表模板约束。"""
    clause = _clause_with_snapshot(
        "投标人基本情况及人员配置表及公司简介固定模板",
        title="投标人基本情况表",
        template_key="bidder_basic_info",
        aliases=["投标人基本情况表", "投标人基本情况及人员配置表及公司简介"],
    )
    assert not _is_acceptable_template_clause("公司简介", clause, node_volume_type="technical")
    assert not _is_acceptable_template_clause("人员配置表", clause, node_volume_type="technical")
    assert _is_acceptable_template_clause("投标人基本情况表", clause, node_volume_type="technical")


# ---------------------------------------------------------------------------
# T4: custom key 全链
# ---------------------------------------------------------------------------


def test_custom_template_key_normalization() -> None:
    key = custom_template_key("（一）拟担任本项目总负责人简历表")
    assert key == "custom:拟担任本项目总负责人简历表"
    assert is_custom_template_key(key)
    assert custom_template_key("表1") is None


def test_custom_key_snapshot_accepted_with_review_channel() -> None:
    """custom key 快照 is_complete=False 时走例外通道放行(配合人工 gate)。"""
    clause = _clause_with_snapshot(
        "拟担任本项目总负责人简历表固定模板",
        title="拟担任本项目总负责人简历表",
        template_key="custom:拟担任本项目总负责人简历表",
        is_complete_candidate=False,
        raw_text_from_chunks="项目：\n姓名 性别 年龄\n投标人名称：\n投标人公章：\n日期： 年 月 日",
        raw_text_verbatim="项目：\n姓名 性别 年龄\n投标人名称：\n投标人公章：\n日期： 年 月 日",
    )
    assert _is_acceptable_template_clause(
        "拟担任本项目总负责人简历表", clause, node_volume_type="technical",
    )


def test_incomplete_dictionary_key_still_rejected() -> None:
    """非 custom / 非 rule_only 的 incomplete 快照维持拒绝(防幻觉)。"""
    clause = _clause_with_snapshot("投标函固定模板", is_complete_candidate=False)
    assert not _is_acceptable_template_clause("投标函", clause, node_volume_type="technical")


# ---------------------------------------------------------------------------
# T8: 唯一归属仲裁
# ---------------------------------------------------------------------------


def _node(node_id: int, title: str, depth: int, sort_order: int = 0) -> SimpleNamespace:
    return SimpleNamespace(id=node_id, title=title, depth=depth, sort_order=sort_order)


_BASIC_INFO_SNAPSHOT = {
    "title": "投标人基本情况表",
    "template_key": "bidder_basic_info",
    "aliases": ["投标人基本情况表", "投标人基本情况及人员配置表及公司简介", "投标人基本情况"],
}


def test_owner_prefers_exact_snapshot_title_over_alias() -> None:
    """2086 实证: 子节点"投标人基本情况表"(快照标题精确相等)胜过父节点(别名相等)。"""
    parent = _node(6953, "投标人基本情况及人员配置表及公司简介", depth=1)
    child = _node(6954, "投标人基本情况表", depth=2)
    owner = select_owner_node([parent, child], _BASIC_INFO_SNAPSHOT)
    assert owner is child


def test_owner_single_candidate_keeps_itself() -> None:
    only = _node(1, "投标函", depth=1)
    snapshot = {"title": "投标函", "template_key": "bid_letter", "aliases": ["投标函"]}
    assert select_owner_node([only], snapshot) is only


def test_fragment_titles_score_zero_after_tightening() -> None:
    assert template_affinity_score("公司简介", _BASIC_INFO_SNAPSHOT) == 0
    assert template_affinity_score("人员配置表", _BASIC_INFO_SNAPSHOT) == 0


def test_owner_tie_breaks_by_depth_then_order() -> None:
    """同分(都与快照标题精确相等)时, 更深节点(叶子)优先。"""
    shallow = _node(10, "投标函", depth=1, sort_order=0)
    deep = _node(11, "投标函", depth=2, sort_order=5)
    snapshot = {"title": "投标函", "template_key": "bid_letter", "aliases": ["投标函"]}
    assert select_owner_node([shallow, deep], snapshot) is deep
