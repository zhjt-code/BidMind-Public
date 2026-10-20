"""约束到写作指令转化层防御测试。

映射风险: 第一期提取的结构化 metadata(score_rules/deduction_rules/risk_consequence
/material_kind 等)必须真正进入章节生成指令, 而非被扁平化丢弃(证据 J/K/L)。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.writer.composer.clause_directive import build_clause_directives

import pytest

pytestmark = pytest.mark.scoring


def _clause(sub_type, content, *, metadata=None, raw_text=None):
    return SimpleNamespace(
        sub_type=sub_type,
        content=content,
        raw_text=raw_text,
        metadata_json=metadata,
    )


# ==================== 评分点转化 ====================

def test_scoring_directive_includes_max_score_rules_and_deductions() -> None:
    c = _clause("scoring_criterion", "技术方案完整性", metadata={
        "score_item_name": "技术方案完整性",
        "max_score": 5,
        "score_rules": ["覆盖全部技术指标得满分"],
        "deduction_rules": ["缺项每项扣1分"],
    })
    out = build_clause_directives([c], max_chars=2000)
    assert "评分响应点" in out
    assert "满分5分" in out
    assert "覆盖全部技术指标得满分" in out
    assert "避免扣分：缺项每项扣1分" in out


def test_scoring_degrades_to_content_when_metadata_missing() -> None:
    c = _clause("scoring_criterion", "仅内容的评分项", metadata=None)
    out = build_clause_directives([c], max_chars=2000)
    assert "仅内容的评分项" in out


def test_scoring_directive_falls_back_to_content_when_rules_missing() -> None:
    c = _clause("scoring_criterion", "方案覆盖全部技术指标得10分，缺项扣2分", metadata={
        "score_item_name": "技术方案完整性",
        "max_score": 10,
        "score_rules": [],
        "deduction_rules": [],
    })
    out = build_clause_directives([c], max_chars=2000)
    assert "技术方案完整性（满分10分）" in out
    assert "评分标准：方案覆盖全部技术指标得10分，缺项扣2分" in out


def test_scoring_directive_does_not_duplicate_content_when_rules_present() -> None:
    c = _clause("scoring_criterion", "方案覆盖全部技术指标得10分", metadata={
        "score_item_name": "技术方案完整性",
        "max_score": 10,
        "score_rules": ["覆盖全部技术指标得10分"],
    })
    out = build_clause_directives([c], max_chars=2000)
    assert "得分要点：覆盖全部技术指标得10分" in out
    assert "评分标准：方案覆盖全部技术指标得10分" not in out


# ==================== 红线转化 ====================

def test_redline_includes_risk_and_substantive_first() -> None:
    normal = _clause("disqualification", "未按要求提供审计报告", metadata={"risk_consequence": "废标"})
    star = _clause(
        "disqualification", "投标报价超最高限价",
        raw_text="★投标报价超最高限价", metadata={"risk_consequence": "无效投标"},
    )
    out = build_clause_directives([normal, star], max_chars=2000)
    assert "内容红线" in out
    assert "（无效投标）" in out
    assert "（废标）" in out
    # ★实质性条款在组内置顶
    assert out.index("★") < out.index("未按要求提供审计报告")


# ==================== 材料转化 ====================

def test_material_directive_includes_kind_form_evidence() -> None:
    c = _clause("material_required", "提供近三年类似业绩", metadata={
        "material_kind": "业绩",
        "required_form": "扫描件",
        "evidence_hint": "合同金额与工期",
    })
    out = build_clause_directives([c], max_chars=2000)
    assert "需在正文体现的材料/证据" in out
    assert "业绩/扫描件" in out
    assert "需体现：合同金额与工期" in out


# ==================== 分组与顺序 ====================

def test_group_priority_order_redline_scoring_context() -> None:
    clauses = [
        _clause("project_context", "项目背景说明"),
        _clause("scoring_criterion", "评分项", metadata={"max_score": 3}),
        _clause("disqualification", "红线条款"),
    ]
    out = build_clause_directives(clauses, max_chars=2000)
    assert out.index("内容红线") < out.index("评分响应点") < out.index("项目背景")


def test_empty_groups_not_rendered() -> None:
    out = build_clause_directives([_clause("content_required", "必写要素A")], max_chars=2000)
    assert "必写内容要素" in out
    assert "内容红线" not in out
    assert "评分响应点" not in out


# ==================== 兜底 ====================

def test_unknown_subtype_kept_in_other_group() -> None:
    out = build_clause_directives([_clause("future_type", "未来约束内容")], max_chars=2000)
    assert "其他要求" in out
    assert "未来约束内容" in out


def test_empty_list_returns_empty_string() -> None:
    assert build_clause_directives([], max_chars=2000) == ""


def test_non_dict_metadata_does_not_crash() -> None:
    c = _clause("material_required", "材料条款", metadata="不是字典")
    out = build_clause_directives([c], max_chars=2000)
    assert "材料条款" in out


# ==================== 预算截断 ====================

def test_budget_truncation_keeps_high_priority_group() -> None:
    redline = _clause("disqualification", "关键红线条款内容")
    context = _clause("project_context", "低优先级背景内容" * 50)
    out = build_clause_directives([context, redline], max_chars=40)
    assert "内容红线" in out
    assert "关键红线条款内容" in out
    # 低优先级背景组因预算耗尽被丢弃
    assert "项目背景" not in out
