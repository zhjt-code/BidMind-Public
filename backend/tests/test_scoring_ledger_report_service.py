"""确认页账本报告服务的条目视图防御测试（Task6 扩展）。

职责: 锁定 _ledger_entry_views 如实从 scoring_metadata 带出 score_rules 与
 deduction_rules, 使确认页口径与提取侧观测结论一致, 不产生误拒收。
 Task6: 新增序列化测试——_serialize_report 必须输出
 scope_reports 与 global_comparison_allowed 字段。
不做: 不测 DB 查询与 API 编排、不测捕获/对账数值逻辑。
"""
from __future__ import annotations

import pytest

from app.core.extractor.scoring.ledger.contracts import (
    BasisLevel,
    LedgerReport,
    LedgerStatus,
)
from app.core.extractor.scoring.ledger.extractor_bridge import (
    observe_scoring_ledger_shadow,
)
from app.core.extractor.scoring.ledger.scoped_contracts import (
    ScopedReport,
)
from app.core.extractor.scoring.scoring_green_verdict import resolve_scoring_verdict
from app.services.scoring_ledger_report_service import (
    _ledger_entry_views,
    _rule_list,
    _serialize_report,
    build_scoring_ledger_report,
)

pytestmark = pytest.mark.scoring


class _FakeChunk:
    """scoring_table chunk 最小形态（无 grid 载荷, 不影响充分性断言）。"""

    def __init__(self, page_num: int, content: str) -> None:
        self.page_num = page_num
        self.content = content


class _FakeRequirement:
    """评分全集行最小形态（确认页侧视图构造入参）。"""

    def __init__(self, description: str, weight: float | None, meta: dict) -> None:
        self.requirement_kind = "scoring_item"
        self.is_key_review = 1
        self.score_weight = weight
        self.scoring_metadata = meta
        self.description = description


def test_views_carry_both_rule_kinds_from_metadata():
    """ 根因③: 视图曾把 score_rules 硬编码为空列表, 使确认页口径下任何
 条目都"无规则文本", 与提取侧观测结论不一致。"""
    requirements = [
        _FakeRequirement(
            "扣分项",
            None,
            {
                "score_item_name": "扣分项",
                "volume_label": "价格评审",
                "score_rules": [],
                "deduction_rules": ["未按要求响应的每项扣1分"],
            },
        ),
        _FakeRequirement(
            "企业业绩",
            2.0,
            {
                "score_item_name": "企业业绩",
                "volume_label": "商务技术评审",
                "score_rules": ["每个业绩得1分"],
            },
        ),
    ]

    views = _ledger_entry_views(requirements)

    assert len(views) == 2
    assert views[0].score_item_name == "扣分项"
    assert views[0].volume_label == "价格评审"
    assert views[0].score_rules == []
    assert views[0].deduction_rules == ["未按要求响应的每项扣1分"]
    assert views[1].score_rules == ["每个业绩得1分"]
    assert views[1].deduction_rules == []


def test_views_feed_sufficiency_without_false_reject():
    """确认页口径端到端: 单字符项名+仅扣分规则经视图喂入后不产生拒收
    (视图漏带规则时本用例即复现项目2449 的琥珀横幅)。"""
    requirements = [
        _FakeRequirement(
            "优",
            5.0,
            {
                "score_item_name": "优",
                "volume_label": "商务技术评审",
                "deduction_rules": ["每项扣1分"],
            },
        )
    ]

    views = _ledger_entry_views(requirements)
    report = observe_scoring_ledger_shadow(
        [_FakeChunk(page_num=1, content="第五章 评标办法")], views, document_id=1
    )

    assert report is not None
    assert report.insufficiency_flags == ()


def test_views_skip_non_scoring_rows():
    """回归: 非评分项且非"重点评审带分值"的普通要求行不得进入账本条目。"""

    class _NormalReq:
        requirement_kind = "normal"
        is_key_review = 0
        score_weight = None
        scoring_metadata = None
        description = "投标人须具备一级资质"

    assert _ledger_entry_views([_NormalReq()]) == []


def test_rule_list_accepts_bare_string_and_rejects_blank():
    """边界: 落库形态不统一——单字符串按单条规则计, 空白/None 按无规则计。"""
    assert _rule_list({"score_rules": "全部满足得5分"}, "score_rules") == ["全部满足得5分"]
    assert _rule_list({"score_rules": "   "}, "score_rules") == []
    assert _rule_list({"score_rules": None}, "score_rules") == []
    assert _rule_list({}, "score_rules") == []


class _FakeDbResult:
    """build_scoring_ledger_report 查询结果最小替身。"""

    def __init__(self, *, rows=None, scalar=None) -> None:
        self._rows = list(rows or [])
        self._scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._scalar


class _SequentialFakeDb:
    """依次返回 chunks 与 scoring_signals 查询结果。"""

    def __init__(self, *results: _FakeDbResult) -> None:
        self._results = list(results)
        self.calls = 0

    async def execute(self, _query):
        self.calls += 1
        return self._results.pop(0)


@pytest.mark.anyio
async def test_empty_scoring_chunks_still_build_fail_visible_ledger() -> None:
    """: 零 scoring_table 不能丢账本后仅按100分合计重算green。"""
    requirements = [
        _FakeRequirement(
            "价格与增值服务",
            35.0,
            {
                "score_item_name": "价格与增值服务",
                "volume_label": None,
                "score_rules": ["按原文计分"],
            },
        ),
        _FakeRequirement(
            "其余评分项目",
            65.0,
            {
                "score_item_name": "其余评分项目",
                "volume_label": "已识别评分组",
                "score_rules": ["按原文计分"],
            },
        ),
    ]
    db = _SequentialFakeDb(
        _FakeDbResult(rows=[]),
        _FakeDbResult(scalar=None),
    )

    payload, ledger = await build_scoring_ledger_report(db, 2511, requirements)

    assert db.calls == 2
    assert payload is not None
    assert ledger is not None
    assert ledger.status is LedgerStatus.REVIEW_REQUIRED
    assert ledger.unlabeled_sum == pytest.approx(35.0)
    assert resolve_scoring_verdict({"needs_review": False}, ledger) == "amber"


# ---------------------------------------------------------------------------
# Task6: _serialize_report 加法字段序列化测试
# ---------------------------------------------------------------------------


def _make_report_single() -> LedgerReport:
    """单 scope 兼容报告（scope_reports=(), global_comparison_allowed=True）。"""
    return LedgerReport(
        status=LedgerStatus.REVIEW_REQUIRED,
        basis_level=BasisLevel.L4_NONE,
        basis_low_confidence=True,
        expected_total=None,
        extracted_total=100.0,
        total_gap=None,
    )


def test_serialize_single_scope_includes_scope_reports_empty():
    """ Task6: 单 scope 序列化结果含 scope_reports=[] 字段。"""
    d = _serialize_report(_make_report_single())
    assert "scope_reports" in d, "序列化结果缺少 scope_reports 字段"
    assert d["scope_reports"] == []


def test_serialize_single_scope_includes_global_comparison_true():
    """ Task6: 单 scope 序列化结果含 global_comparison_allowed=True。"""
    d = _serialize_report(_make_report_single())
    assert "global_comparison_allowed" in d, "序列化结果缺少 global_comparison_allowed 字段"
    assert d["global_comparison_allowed"] is True


def test_serialize_multi_scope_global_comparison_false():
    """ Task6: 多 scope 报告序列化时 global_comparison_allowed=False。"""
    report = LedgerReport(
        status=LedgerStatus.REVIEW_REQUIRED,
        basis_level=BasisLevel.L4_NONE,
        basis_low_confidence=True,
        expected_total=None,
        extracted_total=200.0,
        total_gap=None,
        global_comparison_allowed=False,
    )
    d = _serialize_report(report)
    assert d["global_comparison_allowed"] is False


def test_serialize_multi_scope_reports_each_has_required_fields():
    """ Task6: scope_reports 每个成员含 scope_id/status/expected_total/gap。"""
    sr = ScopedReport(
        scope=("scheme_1", "采购包1"),  # I1: plain tuple，不用 NamedTuple 构造器
        status=LedgerStatus.RECONCILED,
        basis=BasisLevel.L3_DOC_TOTAL,
        expected_total=100.0,
        extracted_total=100.0,
        total_gap=0.0,
    )
    report = LedgerReport(
        status=LedgerStatus.REVIEW_REQUIRED,
        basis_level=BasisLevel.L4_NONE,
        basis_low_confidence=True,
        expected_total=None,
        extracted_total=100.0,
        total_gap=None,
        scope_reports=(sr,),
        global_comparison_allowed=False,
    )
    d = _serialize_report(report)
    assert len(d["scope_reports"]) == 1
    item = d["scope_reports"][0]
    assert item["scope_id"] == "采购包1"
    assert item["status"] == "reconciled"
    assert item["basis"] == "l3_doc_total"
    assert item["expected_total"] == pytest.approx(100.0)
    assert item["extracted_total"] == pytest.approx(100.0)
    assert item["total_gap"] == pytest.approx(0.0)
    assert item["applicability"] == "applicable"


# ---------------------------------------------------------------------------
# Step 2: leaf_integrity 序列化集成红测
# ---------------------------------------------------------------------------


def test_serialize_report_leaf_integrity_nested_default():
    """ Step2: _serialize_report 输出 leaf_integrity 嵌套对象（旧默认报告安全）。"""
    d = _serialize_report(_make_report_single())
    assert "leaf_integrity" in d, "序列化结果缺少 leaf_integrity 字段"
    li = d["leaf_integrity"]
    assert li["verified"] is False
    assert li["basis"] == ""
    assert li["inventory_count"] == 0
    assert li["matched_count"] == 0
    assert li["total"] == pytest.approx(0.0)


def test_serialize_scoped_report_leaf_integrity_nested_default():
    """ Step2: _serialize_scoped_report 输出 leaf_integrity 嵌套对象（旧默认安全）。"""
    from app.services.scoring_ledger_report_service import _serialize_scoped_report

    sr = ScopedReport(
        scope=("scheme_1", "采购包1"),
        status=LedgerStatus.RECONCILED,
        basis=BasisLevel.L3_DOC_TOTAL,
        expected_total=100.0,
        extracted_total=100.0,
        total_gap=0.0,
    )
    d = _serialize_scoped_report(sr)
    assert "leaf_integrity" in d, "scope 序列化结果缺少 leaf_integrity 字段"
    li = d["leaf_integrity"]
    assert li["verified"] is False
    assert "basis" in li
    assert "inventory_count" in li
    assert "matched_count" in li
    assert "total" in li
