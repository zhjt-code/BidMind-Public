"""写作可操作性过滤器防御测试。

映射真实风险:
- 正常路径: 纯行为性废标(串通/多份投标/逾期/撤回)必须被剔除，否则会作为审标噪音
  贯穿去重/确认/匹配全链路(见 BUG 分析中 submission/行为性废标利用率<3% 的证据)。
- 防误杀: 内容性废标(超限价/★条款/未提供材料/技术负偏离)必须保留，漏掉一条即可能废标。
- 子类型隔离: 过滤器只作用于 disqualification，其余 sub_type 一律不动。
"""

from __future__ import annotations

import pytest

from app.core.writer.clause_writability_filter import split_writable_clauses
from app.schemas.writing import ExtractedClause

pytestmark = pytest.mark.scoring


def _clause(
    content: str,
    sub_type: str = "disqualification",
    raw_text: str | None = None,
) -> ExtractedClause:
    return ExtractedClause(sub_type=sub_type, content=content, raw_text=raw_text)


# ==================== 正常路径: 纯行为性废标应剔除 ====================

def test_drops_collusion_clause() -> None:
    kept, dropped = split_writable_clauses(
        [_clause("投标人相互串通投标的，其投标作无效投标处理。")]
    )
    assert kept == []
    assert len(dropped) == 1


def test_drops_multiple_bids_clause() -> None:
    """真实案例: 同一投标人提交2个或以上不同投标文件。"""
    kept, dropped = split_writable_clauses(
        [_clause("同一投标人不得提交2个或者2个以上不同的投标文件，否则视为无效投标。")]
    )
    assert kept == []
    assert len(dropped) == 1


def test_drops_late_submission_clause() -> None:
    kept, dropped = split_writable_clauses(
        [_clause("逾期送达的投标文件，招标人不予受理。")]
    )
    assert kept == []
    assert len(dropped) == 1


def test_drops_withdraw_clause() -> None:
    kept, dropped = split_writable_clauses(
        [_clause("投标截止时间后投标人撤回投标文件的，投标保证金不予退还。")]
    )
    assert kept == []
    assert len(dropped) == 1


# ==================== 防误杀: 内容性废标必须保留 ====================

def test_keeps_over_price_limit_clause() -> None:
    kept, dropped = split_writable_clauses(
        [_clause("投标报价超过最高投标限价461.09万元的，作无效投标处理。")]
    )
    assert len(kept) == 1
    assert dropped == []


def test_keeps_star_substantive_clause() -> None:
    kept, dropped = split_writable_clauses(
        [_clause("★投标人应具备有效的电力工程施工总承包资质，否则按无效投标处理。")]
    )
    assert len(kept) == 1
    assert dropped == []


def test_keeps_missing_material_clause() -> None:
    kept, dropped = split_writable_clauses(
        [_clause("未按要求提供近三年财务审计报告的，其投标无效。")]
    )
    assert len(kept) == 1
    assert dropped == []


def test_keeps_technical_negative_deviation_clause() -> None:
    kept, dropped = split_writable_clauses(
        [_clause("技术参数出现负偏离超过3项的，按无效投标处理。")]
    )
    assert len(kept) == 1
    assert dropped == []


def test_keeps_behavioral_word_when_writable_signal_present() -> None:
    """同时含行为性词与可写信号时，优先保留(防误杀双保险)。"""
    kept, dropped = split_writable_clauses(
        [_clause("★投标文件逾期送达或串通投标情形的响应说明须附相关证明材料。")]
    )
    assert len(kept) == 1
    assert dropped == []


# ==================== 子类型隔离: 非 disqualification 不处理 ====================

def test_ignores_non_disqualification_subtypes() -> None:
    """即使 material_required 内容含行为性词，也不属于过滤器职责范围。"""
    kept, dropped = split_writable_clauses(
        [_clause("投标人须提供近三年类似业绩合同（逾期送达的除外）。", sub_type="material_required")]
    )
    assert len(kept) == 1
    assert dropped == []


def test_ignores_scoring_criterion() -> None:
    kept, dropped = split_writable_clauses(
        [_clause("技术方案完整性最高得5分。", sub_type="scoring_criterion")]
    )
    assert len(kept) == 1
    assert dropped == []


# ==================== 边界与混合 ====================

def test_empty_list_returns_empty_pairs() -> None:
    assert split_writable_clauses([]) == ([], [])


def test_mixed_list_partitions_correctly() -> None:
    clauses = [
        _clause("投标人相互串通投标的，投标无效。"),               # drop
        _clause("投标报价超过最高投标限价的，作无效投标处理。"),     # keep
        _clause("逾期送达的投标文件不予受理。"),                    # drop
        _clause("★须提供有效安全生产许可证，否则废标。"),           # keep
        _clause("技术方案应包含施工进度计划。", sub_type="content_required"),  # keep
    ]
    kept, dropped = split_writable_clauses(clauses)
    assert len(kept) == 3
    assert len(dropped) == 2
    kept_contents = {c.content for c in kept}
    assert any("最高投标限价" in c for c in kept_contents)
    assert any("安全生产许可证" in c for c in kept_contents)
    assert any("施工进度计划" in c for c in kept_contents)
