""" 修复后行为锁（纯前端方向 A）。

职责: 机械锁定「多方案无 lot 文档」后端仍保持 applicability=unresolved，
 同时守护各 scope 仍携带真实 expected_total/total_gap（前端暴露缺口的数据前提）。
不做: 不改生产行为、不断言前端展示态。

背景（docs/bug_tracking.md）:
 项目 2758 的 legacy 已报出方案级缺口（方案4=118/方案6=82/方案14=85/方案10=10），
 确认页曾显示蓝条且无「补充评分项」入口。

机理:
 ledger/scoped_reconciliation.py `_entry_scope` 把无 lot 条目归一为 `__single__`；
 多方案无 lot 文档进入多 scope 路径后，`coordinate_scoped_ledger` 对 `is_no_lot`
 的 scope 一律强制 `applicability="unresolved"`（适用关系待确认，设计语义保留）。
 但各 scope 的 expected_total/total_gap 仍由 `_to_scoped` 如实填入。
 前端方向 A（isMisclassifiedApplicable）据此暴露方案级缺口并给出补录入口。

本文件断言意图（修复后）:
 1. 无 lot 多方案 scope 仍全部 unresolved（适用关系语义不变）；
 2. 这些 unresolved scope 仍携带 expected_total 与负 total_gap，
 且非低置信——守护前端方向 A 的数据前提，防止后端未来把 gap 一并抹掉。
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-key-not-for-prod")
os.environ.setdefault("ADMIN_PASSWORD", "<TEST_PASSWORD>")

from app.core.extractor.scoring.ledger.extractor_bridge import (  # noqa: E402
    observe_scoring_ledger_shadow,
)
from app.core.extractor.scoring.scoring_facts import ScoringScopeKey  # noqa: E402

import pytest

pytestmark = pytest.mark.scoring


class _FakeChunk:
    """scoring_table chunk 最小形态（对齐 extractor_bridge 读取协议）。"""

    def __init__(
        self,
        page_num: int,
        content: str,
        grid: list[list[str]],
        lot: str | None = None,
        scheme: str | None = None,
    ) -> None:
        self.page_num = page_num
        self.content = content
        self.chunk_metadata = {
            "scoring_payload": {"lot": lot, "scheme": scheme, "grid": grid}
        }


class _FakeItem:
    """提取侧条目最小形态（对齐 extractor_bridge._ledger_entry 读取协议）。"""

    def __init__(
        self,
        name: str,
        weight: float,
        volume_label: str = "",
        scheme: str | None = None,
        lot: str | None = None,
    ) -> None:
        self.score_item_name = name
        self.score_weight = weight
        self.volume_label = volume_label
        self.scheme = scheme
        self.lot = lot
        self.score_rules: list[str] = []
        self.deduction_rules: list[str] = []


def _multi_scheme_no_lot_with_real_gap() -> tuple[list[_FakeChunk], list[_FakeItem]]:
    """构造 形态：两套具名方案、全文无 lot，且两套方案均有真实负缺口。

 方案A 声明满分 100 但仅提取 60（缺 40）；方案B 声明满分 100 但仅提取 80（缺 20）。
 """
    chunks = [
        _FakeChunk(
            page_num=1,
            content="第一包评分办法（总分100分）：技术部分100分。",
            grid=[["评分因素", "分值"], ["技术方案", "100"]],
            lot=None,
            scheme="scheme_A",
        ),
        _FakeChunk(
            page_num=2,
            content="第二包评分办法（总分100分）：技术部分100分。",
            grid=[["评分因素", "分值"], ["技术方案", "100"]],
            lot=None,
            scheme="scheme_B",
        ),
    ]
    items = [
        _FakeItem("方案A技术项", 60.0, "技术方案", scheme="scheme_A", lot=None),
        _FakeItem("方案B技术项", 80.0, "技术方案", scheme="scheme_B", lot=None),
    ]
    return chunks, items


class TestBug803NoLotMultiSchemeGapDataPreserved:
    """ 修复后：后端保留 unresolved + 真实 gap 数据前提。"""

    def test_no_lot_multi_scheme_scopes_are_all_unresolved(self):
        """设计行为：无 lot 多方案 scope 保持 unresolved（适用关系待确认）。

        前端负责暴露 intrinsic gap；后端不因有缺口而把 applicability 改成 applicable。
        """
        chunks, items = _multi_scheme_no_lot_with_real_gap()
        report = observe_scoring_ledger_shadow(chunks, items, document_id=803001)

        assert report is not None
        scope_reports = list(getattr(report, "scope_reports", ()))
        assert len(scope_reports) >= 2, (
            f"应进入多 scope 路径（无 lot 多方案），实际 scope 数={len(scope_reports)}"
        )

        no_lot_scopes = [
            s for s in scope_reports if s.scope[1] == ScoringScopeKey.SINGLE_LOT
        ]
        assert len(no_lot_scopes) == len(scope_reports), (
            "构造场景应全部为无 lot scope"
        )
        for scope_report in no_lot_scopes:
            assert scope_report.applicability == "unresolved", (
                " 设计行为漂移：无 lot scope 不再是 unresolved。"
                f"实际={scope_report.applicability}。"
                "若有意改适用关系语义，须同步更新前端 isMisclassifiedApplicable 与本文档。"
            )

    def test_unresolved_scopes_preserve_real_gap_for_frontend(self):
        """守护前端方向 A 数据前提：unresolved scope 仍携带真实负 gap。

        对齐 frontend isMisclassifiedApplicable：
        expected_total 非空 + total_gap < 0 + 非低置信 → 前端可升 amber 并显示补录。
        """
        chunks, items = _multi_scheme_no_lot_with_real_gap()
        report = observe_scoring_ledger_shadow(chunks, items, document_id=803002)

        assert report is not None
        assert getattr(report, "global_comparison_allowed", True) is False, (
            "多 scope 报告应禁止全局比较（前端据此进入多包模式判据）"
        )

        scope_reports = list(getattr(report, "scope_reports", ()))
        assert len(scope_reports) >= 2

        gap_bearing = [
            s
            for s in scope_reports
            if s.applicability == "unresolved"
            and s.expected_total is not None
            and s.total_gap is not None
            and s.total_gap < 0
            and not s.basis_low_confidence
        ]
        assert len(gap_bearing) >= 1, (
            " 数据前提被破坏：无 lot unresolved scope 未保留真实负 gap。"
            "前端 isMisclassifiedApplicable 将无法暴露方案级缺口。"
            f"scope 摘要={[ (s.applicability, s.expected_total, s.total_gap, s.basis_low_confidence) for s in scope_reports ]}"
        )
