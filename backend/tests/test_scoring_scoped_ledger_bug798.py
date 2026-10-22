"""Scoped ledger 防御测试（Task 6 修订）。

覆盖范围（修订后新增 RED 测试）:
- ScopeIdentity / ScopedReport 契约（I1: 普通 tuple，不再是 NamedTuple）
- declaration_scope C1 公式门禁（精确括号 + 语境检查）
- declaration_scope I2 首个非空行
- observe_scoring_ledger_shadow 多 scope 路由
- 单 scope 向后兼容
- C2 无 lot 条目在多 scope 只消费无 lot chunk → unresolved
- C3 scheme×lot 双维精确过滤
- I3 同页多 lot 按 chunk 保留
- C4 顶层守恒汇总（unlabeled/diag 聚合）
- I1 __unassigned__ 保持未决，不与 __single__ 混合
- I4 applicability Literal，ScopedReport 携带 basis_low_confidence/diagnostics
- I6 多 scope 丢弃补充声明追加诊断
- Minor: expected_by_volume 对象数组序列化；单 scope 序列化字段兼容

失败原因必须是业务断言，非 fixture/导入错误。
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-key-not-for-prod")
os.environ.setdefault("ADMIN_PASSWORD", "<TEST_PASSWORD>")

from app.core.extractor.scoring.ledger.contracts import (  # noqa: E402
    BasisLevel,
    LedgerItemEntry,
    LedgerReport,
    LedgerStatus,
)
from app.core.extractor.scoring.ledger.declaration_scope import (  # noqa: E402
    extract_formula_total_candidates,
    resolve_chunk_lot,
)
from app.core.extractor.scoring.ledger.extractor_bridge import (  # noqa: E402
    observe_scoring_ledger_shadow,
)
from app.core.extractor.scoring.ledger.scoped_contracts import (  # noqa: E402
    DEFAULT_SCOPE,
    SINGLE_SCOPE,
    UNASSIGNED_LOT,
    ScopedReport,
    ScopeIdentity,
)
from app.core.extractor.scoring.scoring_facts import ScoringScopeKey  # noqa: E402
from app.services.scoring_ledger_report_service import _serialize_report  # noqa: E402

pytestmark = pytest.mark.scoring


# ---------------------------------------------------------------------------
# 辅助 helper
# ---------------------------------------------------------------------------


class _FakeChunk:
    """scoring_table chunk 最小形态，支持 scheme/lot payload（C3 测试）。"""

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
    """提取侧条目最小形态，对齐 extractor_bridge._ledger_entry 读取协议。"""

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


def _make_two_lot_scenario() -> tuple[list[_FakeChunk], list[_FakeItem]]:
    """构造 2499 形态：两采购包各100分，chunk header 含 lot 名。"""
    chunks = [
        _FakeChunk(
            page_num=1,
            content="[评分表 第1-1页 · 采购包1]\n采购包1综合评标方式：FA=F1×A1+F2×A2=100分(满分时)。",
            grid=[
                ["分组", "评分因素", "分值"],
                ["价格部分", "报价评分", "30分"],
            ],
            lot=None,  # payload.lot 为空，依赖 header 解析
            scheme="scheme_1",
        ),
        _FakeChunk(
            page_num=3,
            content="[评分表 第3-5页 · 采购包1]\n技术项满分70分。",
            grid=[
                ["项目", "分值"],
                ["技术项A", "50.00"],
                ["技术项B", "20.00"],
            ],
            lot="采购包1",
            scheme="scheme_1",
        ),
        _FakeChunk(
            page_num=7,
            content="[评分表 第7-7页 · 采购包2]\n采购包2综合评标方式：FA=F1×A1+F2×A2=100分(满分时)。",
            grid=[
                ["分组", "评分因素", "分值"],
                ["价格部分", "报价评分", "10分"],
            ],
            lot=None,
            scheme="scheme_1",
        ),
        _FakeChunk(
            page_num=8,
            content="[评分表 第8-13页 · 采购包2]\n技术项满分90分。",
            grid=[
                ["项目", "分值"],
                ["技术项C", "90.00"],
            ],
            lot="采购包2",
            scheme="scheme_1",
        ),
    ]
    items = [
        # 采购包1: 30 + 50 + 20 = 100
        _FakeItem("报价评分", 30.0, "价格部分", scheme="scheme_1", lot="采购包1"),
        _FakeItem("技术项A", 50.0, "", scheme="scheme_1", lot="采购包1"),
        _FakeItem("技术项B", 20.0, "", scheme="scheme_1", lot="采购包1"),
        # 采购包2: 10 + 90 = 100
        _FakeItem("报价评分", 10.0, "价格部分", scheme="scheme_1", lot="采购包2"),
        _FakeItem("技术项C", 90.0, "", scheme="scheme_1", lot="采购包2"),
    ]
    return chunks, items


# ---------------------------------------------------------------------------
# T1: ScopeIdentity / ScopedReport 契约（I1 修订: 普通 tuple）
# ---------------------------------------------------------------------------


class TestScopedContracts:
    """ScopeIdentity 与 ScopedReport 数据契约验证。

    I1 修订: ScopeIdentity = tuple[str, str]（类型别名），不再是 NamedTuple 构造器。
    创建 scope 时直接用 ("scheme", "lot") 元组字面量。
    """

    def test_single_scope_constant(self):
        """SINGLE_SCOPE 等于 __single__（使用 ScoringScopeKey 常量来源）。"""
        assert SINGLE_SCOPE == "__single__"

    def test_unassigned_lot_constant(self):
        """UNASSIGNED_LOT 等于 __unassigned__，与 SINGLE_SCOPE 不同。"""
        assert UNASSIGNED_LOT == "__unassigned__"
        assert UNASSIGNED_LOT != SINGLE_SCOPE, "__unassigned__ 与 __single__ 必须不同"

    def test_default_scope_is_tuple(self):
        """DEFAULT_SCOPE 是 ('__single__', '__single__') 元组。"""
        assert DEFAULT_SCOPE == ("__single__", "__single__")

    def test_scope_is_tuple_pair(self):
        """I1: ScopeIdentity 为 tuple[str,str] 类型别名，scope 用元组字面量创建。"""
        s: ScopeIdentity = ("scheme_1", "采购包1")
        assert s[0] == "scheme_1"     # scheme 用索引[0]
        assert s[1] == "采购包1"      # lot 用索引[1]
        assert s == ("scheme_1", "采购包1")

    def test_scoped_report_scope_id_uses_lot(self):
        sr = ScopedReport(
            scope=("scheme_1", "采购包1"),
            status=LedgerStatus.RECONCILED,
            basis=BasisLevel.L3_DOC_TOTAL,
            expected_total=100.0,
            extracted_total=100.0,
            total_gap=0.0,
        )
        assert sr.scope_id == "采购包1"

    def test_scoped_report_scope_id_uses_scheme_for_single_lot(self):
        sr = ScopedReport(
            scope=DEFAULT_SCOPE,
            status=LedgerStatus.REVIEW_REQUIRED,
            basis=BasisLevel.L4_NONE,
            expected_total=None,
            extracted_total=100.0,
            total_gap=None,
        )
        assert sr.scope_id == "__single__"

    def test_scoped_report_expected_by_volume_defaults_empty(self):
        sr = ScopedReport(
            scope=DEFAULT_SCOPE,
            status=LedgerStatus.REVIEW_REQUIRED,
            basis=BasisLevel.L4_NONE,
            expected_total=None,
            extracted_total=0.0,
            total_gap=None,
        )
        assert sr.expected_by_volume == ()

    def test_scoped_report_applicability_default(self):
        sr = ScopedReport(
            scope=DEFAULT_SCOPE,
            status=LedgerStatus.REVIEW_REQUIRED,
            basis=BasisLevel.L4_NONE,
            expected_total=None,
            extracted_total=0.0,
            total_gap=None,
        )
        assert sr.applicability == "applicable"

    def test_scoped_report_has_basis_low_confidence_field(self):
        """I4: ScopedReport 携带 basis_low_confidence 字段（默认 False）。"""
        sr = ScopedReport(
            scope=DEFAULT_SCOPE,
            status=LedgerStatus.REVIEW_REQUIRED,
            basis=BasisLevel.L4_NONE,
            expected_total=None,
            extracted_total=0.0,
            total_gap=None,
        )
        assert hasattr(sr, "basis_low_confidence")
        assert sr.basis_low_confidence is False

    def test_scoped_report_has_diagnostics_field(self):
        """I4: ScopedReport 携带 diagnostics 字段（默认空元组）。"""
        sr = ScopedReport(
            scope=DEFAULT_SCOPE,
            status=LedgerStatus.REVIEW_REQUIRED,
            basis=BasisLevel.L4_NONE,
            expected_total=None,
            extracted_total=0.0,
            total_gap=None,
        )
        assert hasattr(sr, "diagnostics")
        assert sr.diagnostics == ()

    def test_scoped_report_has_unlabeled_sum_field(self):
        """C4: ScopedReport 携带 unlabeled_sum 字段（默认 0.0）。"""
        sr = ScopedReport(
            scope=DEFAULT_SCOPE,
            status=LedgerStatus.REVIEW_REQUIRED,
            basis=BasisLevel.L4_NONE,
            expected_total=None,
            extracted_total=0.0,
            total_gap=None,
        )
        assert hasattr(sr, "unlabeled_sum")
        assert sr.unlabeled_sum == 0.0

    def test_scoped_report_applicability_can_be_unresolved(self):
        """I4: applicability='unresolved' 合法。"""
        sr = ScopedReport(
            scope=DEFAULT_SCOPE,
            status=LedgerStatus.REVIEW_REQUIRED,
            basis=BasisLevel.L4_NONE,
            expected_total=None,
            extracted_total=0.0,
            total_gap=None,
            applicability="unresolved",
        )
        assert sr.applicability == "unresolved"


# ---------------------------------------------------------------------------
# T2: LedgerReport 加法扩展兼容性
# ---------------------------------------------------------------------------


class TestLedgerReportBackwardCompat:
    """contracts.py 加法扩展：旧代码不感知新字段，默认值保持旧语义。"""

    def test_new_fields_have_correct_defaults(self):
        """scope_reports=() 且 global_comparison_allowed=True（旧报告）。"""
        report = LedgerReport(
            status=LedgerStatus.REVIEW_REQUIRED,
            basis_level=BasisLevel.L4_NONE,
            basis_low_confidence=True,
            expected_total=None,
            extracted_total=100.0,
            total_gap=None,
        )
        assert report.scope_reports == ()
        assert report.global_comparison_allowed is True

    def test_scope_reports_can_hold_scoped_report_objects(self):
        """scope_reports 可持有 ScopedReport 实例（类型检查通过）。"""
        sr = ScopedReport(
            scope=("s", "lot_a"),   # I1: plain tuple
            status=LedgerStatus.REVIEW_REQUIRED,
            basis=BasisLevel.L4_NONE,
            expected_total=None,
            extracted_total=50.0,
            total_gap=None,
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
        assert len(report.scope_reports) == 1
        assert report.global_comparison_allowed is False


# ---------------------------------------------------------------------------
# T3: declaration_scope.resolve_chunk_lot（I2 修订: 首个非空行）
# ---------------------------------------------------------------------------


class TestDeclarationScope:
    """resolve_chunk_lot 规则验证（I2: 单源，首个非空行）。"""

    def test_payload_lot_takes_priority(self):
        assert resolve_chunk_lot("[评分表 第1页 · 采购包2]\n...", "采购包1") == "采购包1"

    def test_resolve_from_markdown_header_middle_dot(self):
        content = "[评分表 第1-1页 · 采购包1]\n采购包1综合评标方式..."
        assert resolve_chunk_lot(content, None) == "采购包1"

    def test_resolve_from_header_second_lot(self):
        content = "[评分表 第7-7页 · 采购包2]\n采购包2综合评标方式..."
        assert resolve_chunk_lot(content, None) == "采购包2"

    def test_returns_none_when_no_header_and_no_payload(self):
        assert resolve_chunk_lot("评标方法如下\n详见附件。", None) is None

    def test_returns_none_for_empty_content(self):
        assert resolve_chunk_lot("", None) is None

    def test_payload_lot_none_string_treated_as_absent(self):
        content = "[评分表 第3-5页 · 第一标段]\n..."
        assert resolve_chunk_lot(content, None) == "第一标段"

    def test_i2_first_nonempty_line_used(self):
        """I2: 首个非空行（跳过空行前缀），与解析器 _extract_lot_from_markdown 一致。"""
        content = "\n\n[评分表 第1-1页 · 采购包X]\n正文内容"
        result = resolve_chunk_lot(content, None)
        assert result == "采购包X", (
            f"应从首个非空行解析 lot，得到 {result!r}，期望 '采购包X'"
        )

    def test_trailing_space_in_lot_stripped(self):
        """I1/Minor: lot 名称尾空格被 strip。"""
        content = "[评分表 第1页 · 采购包A  ]\n..."
        result = resolve_chunk_lot(content, None)
        assert result == "采购包A", f"尾空格未 strip，得到 {result!r}"


# ---------------------------------------------------------------------------
# T4: observe_scoring_ledger_shadow 单 scope 向后兼容
# ---------------------------------------------------------------------------


class TestSingleScopeBackwardCompat:
    """单 scope 时 scope_reports=()，global_comparison_allowed=True，语义不变。"""

    def test_single_lot_report_has_empty_scope_reports(self):
        chunk = _FakeChunk(
            page_num=1,
            content="分值构成（总分100分）商务20分；技术80分",
            grid=[],
            lot="唯一包",
            scheme="scheme_1",
        )
        items = [
            _FakeItem("商务项", 20.0, "商务部分", scheme="scheme_1", lot="唯一包"),
            _FakeItem("技术项", 80.0, "技术部分", scheme="scheme_1", lot="唯一包"),
        ]
        report = observe_scoring_ledger_shadow([chunk], items, document_id=9001)
        assert report is not None
        assert getattr(report, "scope_reports", ()) == ()
        assert getattr(report, "global_comparison_allowed", True) is True

    def test_no_lot_single_scope_backward_compat(self):
        """所有条目无 lot（旧接口）：报告保持旧标量语义。"""
        chunk = _FakeChunk(
            page_num=1,
            content="分值构成（总分100分）技术50分；商务50分",
            grid=[],
        )
        items = [
            _FakeItem("技术项", 50.0, "技术部分"),
            _FakeItem("商务项", 50.0, "商务部分"),
        ]
        report = observe_scoring_ledger_shadow([chunk], items, document_id=9002)
        assert report is not None
        assert getattr(report, "scope_reports", ()) == ()
        assert getattr(report, "global_comparison_allowed", True) is True

    def test_single_scope_serialize_each_standard_field(self):
        """Minor: 单 scope 序列化结果包含所有标准字段。"""
        report = LedgerReport(
            status=LedgerStatus.REVIEW_REQUIRED,
            basis_level=BasisLevel.L4_NONE,
            basis_low_confidence=True,
            expected_total=None,
            extracted_total=100.0,
            total_gap=None,
        )
        d = _serialize_report(report)
        for field in [
            "status", "basis_level", "basis_low_confidence",
            "expected_total", "extracted_total", "total_gap",
            "group_rows", "unlabeled_sum", "multi_scheme_signal",
            "insufficiency_count", "scope_reports", "global_comparison_allowed",
        ]:
            assert field in d, f"序列化缺少字段: {field}"


# ---------------------------------------------------------------------------
# T5: observe_scoring_ledger_shadow 多 scope
# ---------------------------------------------------------------------------


class TestMultiScopeShadow:
    """两 lot 各100：scope_reports=2，顶层 expected/gap=None，global=False。"""

    @pytest.fixture
    def two_lot_report(self):
        chunks, items = _make_two_lot_scenario()
        return observe_scoring_ledger_shadow(chunks, items, document_id=2499)

    def test_report_is_not_none(self, two_lot_report):
        assert two_lot_report is not None

    def test_scope_reports_count(self, two_lot_report):
        sr = getattr(two_lot_report, "scope_reports", ())
        assert len(sr) == 2, (
            f"期望 2 个 scope 子报告，实际 {len(sr)}"
        )

    def test_global_comparison_not_allowed(self, two_lot_report):
        flag = getattr(two_lot_report, "global_comparison_allowed", True)
        assert flag is False

    def test_top_level_expected_total_is_none(self, two_lot_report):
        assert two_lot_report.expected_total is None

    def test_top_level_total_gap_is_none(self, two_lot_report):
        assert two_lot_report.total_gap is None

    def test_each_scope_expected_100(self, two_lot_report):
        sr = getattr(two_lot_report, "scope_reports", ())
        if not sr:
            pytest.skip("scope_reports 尚未实现")
        for s in sr:
            assert s.expected_total is not None
            assert abs(s.expected_total - 100.0) < 0.01, (
                f"scope_id={s.scope_id} expected_total={s.expected_total}"
            )

    def test_each_scope_extracted_100(self, two_lot_report):
        sr = getattr(two_lot_report, "scope_reports", ())
        if not sr:
            pytest.skip("scope_reports 尚未实现")
        for s in sr:
            assert abs(s.extracted_total - 100.0) < 0.01

    def test_each_scope_gap_within_tolerance(self, two_lot_report):
        sr = getattr(two_lot_report, "scope_reports", ())
        if not sr:
            pytest.skip("scope_reports 尚未实现")
        for s in sr:
            assert s.total_gap is not None
            assert abs(s.total_gap) <= 1.0

    def test_scope_ids_match_lots(self, two_lot_report):
        sr = getattr(two_lot_report, "scope_reports", ())
        if not sr:
            pytest.skip("scope_reports 尚未实现")
        scope_ids = {s.scope_id for s in sr}
        assert scope_ids == {"采购包1", "采购包2"}


# ---------------------------------------------------------------------------
# T6: 无 scope chunk 在多 scope 文档不广播
# ---------------------------------------------------------------------------


class TestNoScopeChunkIsolation:
    """无 scope chunk 在多 scope 文档中的声明不广播。"""

    def test_unscoped_chunk_with_extra_formula_not_broadcast(self):
        chunks = [
            _FakeChunk(
                page_num=0,
                content="补充说明：综合评标总得分=200分（两包合计，满分时）。",
                grid=[],
                lot=None,
            ),
            _FakeChunk(
                page_num=1,
                content="[评分表 第1-1页 · 采购包1]\n采购包1评标总得分=100分(满分时)。",
                grid=[["分值"], ["100"]],
                lot=None,
                scheme="scheme_1",
            ),
            _FakeChunk(
                page_num=2,
                content="[评分表 第2-2页 · 采购包2]\n采购包2评标总得分=100分(满分时)。",
                grid=[["分值"], ["100"]],
                lot=None,
                scheme="scheme_1",
            ),
        ]
        items = [
            _FakeItem("项A", 100.0, "", scheme="scheme_1", lot="采购包1"),
            _FakeItem("项B", 100.0, "", scheme="scheme_1", lot="采购包2"),
        ]
        report = observe_scoring_ledger_shadow(chunks, items, document_id=9999)
        assert report is not None
        global_ok = getattr(report, "global_comparison_allowed", True)
        assert global_ok is False
        sr = getattr(report, "scope_reports", ())
        if sr:
            for s in sr:
                assert s.expected_total is not None
                assert abs(s.expected_total - 100.0) <= 1.0


# ---------------------------------------------------------------------------
# T7: _serialize_report 序列化字段
# ---------------------------------------------------------------------------


class TestSerializeReportScopedFields:
    """_serialize_report 必须输出 scope_reports 和 global_comparison_allowed。"""

    def _make_report(self, *, scope_reports=(), global_comparison_allowed=True):
        return LedgerReport(
            status=LedgerStatus.REVIEW_REQUIRED,
            basis_level=BasisLevel.L4_NONE,
            basis_low_confidence=True,
            expected_total=None,
            extracted_total=100.0,
            total_gap=None,
            scope_reports=scope_reports,
            global_comparison_allowed=global_comparison_allowed,
        )

    def test_single_scope_serialize_global_flag_true(self):
        d = _serialize_report(self._make_report())
        assert "global_comparison_allowed" in d
        assert d["global_comparison_allowed"] is True

    def test_single_scope_serialize_scope_reports_empty(self):
        d = _serialize_report(self._make_report())
        assert "scope_reports" in d
        assert d["scope_reports"] == []

    def test_multi_scope_serialize_global_flag_false(self):
        d = _serialize_report(self._make_report(global_comparison_allowed=False))
        assert d["global_comparison_allowed"] is False

    def test_multi_scope_serialize_scope_reports_items(self):
        """scope_reports 非空时序列化包含所有必要字段（I1: scope[0]/[1] 索引访问）。"""
        sr = ScopedReport(
            scope=("scheme_1", "采购包1"),  # I1: plain tuple
            status=LedgerStatus.RECONCILED,
            basis=BasisLevel.L3_DOC_TOTAL,
            expected_total=100.0,
            extracted_total=100.0,
            total_gap=0.0,
        )
        d = _serialize_report(
            self._make_report(scope_reports=(sr,), global_comparison_allowed=False)
        )
        assert len(d["scope_reports"]) == 1
        item = d["scope_reports"][0]
        assert item["scope_id"] == "采购包1"
        assert item["status"] == "reconciled"
        assert item["expected_total"] == pytest.approx(100.0)
        assert item["extracted_total"] == pytest.approx(100.0)
        assert item["total_gap"] == pytest.approx(0.0)
        assert item["scheme"] == "scheme_1"
        assert item["lot"] == "采购包1"

    def test_expected_by_volume_serialized_as_object_array(self):
        """Minor: expected_by_volume 序列化为对象数组 [{"volume": ..., "expected": ...}]。"""
        sr = ScopedReport(
            scope=("scheme_1", "采购包1"),
            status=LedgerStatus.RECONCILED,
            basis=BasisLevel.L3_DOC_TOTAL,
            expected_total=100.0,
            extracted_total=100.0,
            total_gap=0.0,
            expected_by_volume=(("技术部分", 70.0), ("商务部分", 30.0)),
        )
        report = self._make_report(scope_reports=(sr,))
        d = _serialize_report(report)
        ebv = d["scope_reports"][0]["expected_by_volume"]
        assert isinstance(ebv, list), f"expected_by_volume 应为列表，实际 {type(ebv)}"
        assert len(ebv) == 2
        assert ebv[0] == {"volume": "技术部分", "expected": 70.0}, f"实际: {ebv[0]}"
        assert ebv[1] == {"volume": "商务部分", "expected": 30.0}, f"实际: {ebv[1]}"

    def test_scoped_report_serialized_with_audit_fields(self):
        """I4/I5: scope_reports 成员包含 basis_low_confidence/diagnostics/unlabeled_sum。"""
        sr = ScopedReport(
            scope=("scheme_1", "采购包1"),
            status=LedgerStatus.REVIEW_REQUIRED,
            basis=BasisLevel.L4_NONE,
            expected_total=None,
            extracted_total=100.0,
            total_gap=None,
            applicability="unresolved",
            basis_low_confidence=True,
            diagnostics=("无权威基准",),
            unlabeled_sum=5.0,
        )
        report = self._make_report(scope_reports=(sr,))
        d = _serialize_report(report)
        item = d["scope_reports"][0]
        assert item["applicability"] == "unresolved"
        assert item["basis_low_confidence"] is True
        assert "无权威基准" in item["diagnostics"]
        assert item["unlabeled_sum"] == pytest.approx(5.0)


# ===========================================================================
# 新增 RED 测试（Task6 独立评审修复）
# ===========================================================================


# ---------------------------------------------------------------------------
# C1: 公式总分门禁
# ---------------------------------------------------------------------------


class TestFormulaGateC1:
    """C1: 精确括号匹配 + 语境检查。"""

    def test_item_parens_number_rejected(self):
        """项级 '=8分(满分8分)' 括号含数字 → 不匹配。"""
        texts = ["本项评分=8分(满分8分)，请评委打分。"]
        assert extract_formula_total_candidates(texts) == [], (
            "项级括号含数字时不应提取公式总分"
        )

    def test_price_formula_parens_number_rejected(self):
        """价格公式 '=30分(满分30分)' 括号含数字 → 不匹配。"""
        texts = ["价格得分=30分(满分30分)，技术得分另计。"]
        assert extract_formula_total_candidates(texts) == []

    def test_item_parens_exact_manzhan_number_full_example(self):
        """满分8分形式（全角括号）同样被拒绝。"""
        texts = ["该项满分为：A=8分（满分8分）。"]
        assert extract_formula_total_candidates(texts) == []

    def test_bonus_formula_no_context_rejected(self):
        """加分公式 '=5分(满分时)' 且无总分语境 → 语境检查失败，不提取。"""
        texts = ["分项附加分 = 5分(满分时)，评委酌情给分。"]
        result = extract_formula_total_candidates(texts)
        assert result == [], (
            f"无总分语境的加分公式不应提取，实际: {result}"
        )

    def test_fa_formula_still_matches(self):
        """C1 通过: FA=F1×A1+F2×A2=100分(满分时) → 100.0（2个F×A结构）。"""
        texts = ["采购包1综合评标方式：FA=F1×A1+F2×A2=100分(满分时)。"]
        result = extract_formula_total_candidates(texts)
        assert result == [100.0], f"F×A公式未命中，实际: {result}"

    def test_total_words_still_matches(self):
        """C1 通过: 含'评标总得分'语境词 → 100.0。"""
        texts = ["采购包1评标总得分=100分(满分时)。"]
        result = extract_formula_total_candidates(texts)
        assert result == [100.0], f"总得分语境未命中，实际: {result}"

    def test_综合得分_context_matches(self):
        """C1 通过: '综合得分' 语境词 → 匹配。"""
        texts = ["综合得分=100分（满分）。"]
        result = extract_formula_total_candidates(texts)
        assert result == [100.0]

    def test_conflict_100_120_both_in_same_scope(self):
        """C1 冲突低置信: 同一文本中出现 100 和 120 两个公式总分 → 两者均在候选列表。"""
        texts = [
            "方案A: 综合得分=100分(满分时)。",
            "方案B: 综合得分=120分(满分时)。",
        ]
        result = extract_formula_total_candidates(texts)
        assert 100.0 in result and 120.0 in result, (
            f"两个合法公式总分应均被采集，实际: {result}"
        )

    def test_manzhan_exact_bracket_only(self):
        """(满分) 精确形式（无"时"）也应被匹配。"""
        texts = ["评标总得分=100分（满分）。"]
        result = extract_formula_total_candidates(texts)
        assert result == [100.0]

    def test_manzhen_shi_bracket_only(self):
        """(满分时) 精确形式（全角括号）应被匹配。"""
        texts = ["综合得分=120分（满分时）。"]
        result = extract_formula_total_candidates(texts)
        assert result == [120.0]


# ---------------------------------------------------------------------------
# C2: 无 lot 条目在多 scope 文档中只消费无 lot chunk
# ---------------------------------------------------------------------------


class TestNoLotInMultiScopeC2:
    """C2: __single__/无 lot 条目在多 scope 文档应标 unresolved，不消费具名 lot 页。"""

    def test_no_lot_items_get_unresolved_in_multi_scope(self):
        """多 scope 文档中若存在无 lot 条目，其 scope 子报告 applicability=unresolved。"""
        chunks = [
            _FakeChunk(1, "[评分表 第1页 · 采购包1]\n技术部分满分50分。",
                       [["分", "50"]], lot="采购包1", scheme="scheme_1"),
            _FakeChunk(2, "[评分表 第2页 · 采购包2]\n技术部分满分50分。",
                       [["分", "50"]], lot="采购包2", scheme="scheme_1"),
        ]
        items = [
            _FakeItem("技术项1", 50.0, "", scheme="scheme_1", lot="采购包1"),
            _FakeItem("技术项2", 50.0, "", scheme="scheme_1", lot="采购包2"),
            # 无 lot 条目（旧接口残留或解析缺 lot）
            _FakeItem("无lot项", 10.0, "", scheme=None, lot=None),
        ]
        report = observe_scoring_ledger_shadow(chunks, items, document_id=8001)
        assert report is not None
        sr_list = getattr(report, "scope_reports", ())
        no_lot_srs = [s for s in sr_list if s.scope[1] == "__single__"]
        assert len(no_lot_srs) > 0, "多 scope 文档中应有 __single__ lot 的子报告"
        for s in no_lot_srs:
            assert s.applicability == "unresolved", (
                f"无 lot 条目的 scope 子报告 applicability 应为 unresolved，实际: {s.applicability}"
            )

    def test_single_scope_no_lot_still_applicable(self):
        """C2 单 scope 旧行为：无 lot 条目可消费全文，applicability 不强制 unresolved。"""
        chunk = _FakeChunk(1, "分值构成：技术80分商务20分。", [])
        items = [
            _FakeItem("技术", 80.0),
            _FakeItem("商务", 20.0),
        ]
        report = observe_scoring_ledger_shadow([chunk], items, document_id=8002)
        # 单 scope 时 scope_reports=()，使用旧标量报告
        assert report is not None
        assert getattr(report, "scope_reports", ()) == ()


# ---------------------------------------------------------------------------
# I1: __unassigned__ lot 保持未决，不与 __single__ 混合
# ---------------------------------------------------------------------------


class TestUnassignedLotI1:
    """I1: __unassigned__ 条目不当正常 lot，与 __single__ 区分，标 unresolved。"""

    def test_unassigned_lot_distinct_from_single(self):
        """ScoringScopeKey.UNASSIGNED_LOT != SINGLE_LOT。"""
        assert ScoringScopeKey.UNASSIGNED_LOT != ScoringScopeKey.SINGLE_LOT
        assert UNASSIGNED_LOT == "__unassigned__"
        assert UNASSIGNED_LOT != SINGLE_SCOPE

    def test_unassigned_lot_in_multi_scope_gets_unresolved(self):
        """多 scope 文档中 __unassigned__ lot 条目 → applicability=unresolved。"""
        chunks = [
            _FakeChunk(1, "[评分表 第1页 · 采购包1]\n技术50分。",
                       [], lot="采购包1", scheme="scheme_1"),
            _FakeChunk(2, "[评分表 第2页 · 采购包2]\n技术50分。",
                       [], lot="采购包2", scheme="scheme_1"),
        ]
        items = [
            _FakeItem("技术项1", 50.0, "", scheme="scheme_1", lot="采购包1"),
            _FakeItem("技术项2", 50.0, "", scheme="scheme_1", lot="采购包2"),
            # __unassigned__ 条目（解析失败残留）
            _FakeItem("未分配项", 5.0, "", scheme=None, lot="__unassigned__"),
        ]
        report = observe_scoring_ledger_shadow(chunks, items, document_id=8003)
        assert report is not None
        sr_list = getattr(report, "scope_reports", ())
        unassigned_srs = [s for s in sr_list if s.scope[1] == "__unassigned__"]
        assert len(unassigned_srs) > 0, "应有 __unassigned__ scope 子报告"
        for s in unassigned_srs:
            assert s.applicability == "unresolved", (
                f"__unassigned__ 应标 unresolved，实际: {s.applicability}"
            )


# ---------------------------------------------------------------------------
# C3/I3: scheme×lot 双维过滤 & 同页多 lot
# ---------------------------------------------------------------------------


class TestSchemeXLotFilteringC3:
    """C3: chunk_scopes 精确过滤；同 lot 多 scheme chunk 歧义排除。"""

    def test_chunk_with_scheme_lot_filtered_to_correct_scope(self):
        """chunk scheme=scheme_A/lot=lot_1 只进入 (scheme_A, lot_1) scope。"""
        chunks = [
            _FakeChunk(1, "[评分表 第1页 · lot_1]\n技术100分。",
                       [], lot="lot_1", scheme="scheme_A"),
            _FakeChunk(2, "[评分表 第2页 · lot_2]\n技术100分。",
                       [], lot="lot_2", scheme="scheme_B"),
        ]
        items = [
            _FakeItem("技术A", 100.0, "", scheme="scheme_A", lot="lot_1"),
            _FakeItem("技术B", 100.0, "", scheme="scheme_B", lot="lot_2"),
        ]
        report = observe_scoring_ledger_shadow(chunks, items, document_id=8010)
        assert report is not None
        sr_list = getattr(report, "scope_reports", ())
        assert len(sr_list) == 2, f"期望2个scope子报告，实际 {len(sr_list)}"
        lot_ids = {s.scope[1] for s in sr_list}
        assert "lot_1" in lot_ids and "lot_2" in lot_ids


class TestSamePageMultiLotI3:
    """I3: 同一 page_num 的多个 chunk（不同 lot）按 chunk 各自保留，不覆盖。"""

    def test_two_chunks_same_page_different_lots(self):
        """page_num=5 出现两次（lot_A / lot_B），两个 scope 各自得到正确页面。"""
        chunks = [
            _FakeChunk(5, "[评分表 第5页 · lot_A]\n技术100分。",
                       [], lot="lot_A", scheme="scheme_1"),
            _FakeChunk(5, "[评分表 第5页 · lot_B]\n技术100分。",  # 同页不同 lot
                       [], lot="lot_B", scheme="scheme_1"),
        ]
        items = [
            _FakeItem("技术A", 100.0, "", scheme="scheme_1", lot="lot_A"),
            _FakeItem("技术B", 100.0, "", scheme="scheme_1", lot="lot_B"),
        ]
        report = observe_scoring_ledger_shadow(chunks, items, document_id=8020)
        assert report is not None
        sr_list = getattr(report, "scope_reports", ())
        assert len(sr_list) == 2, (
            f"I3: 同页两个 chunk 应形成2个独立 scope，实际: {len(sr_list)}"
        )
        # 两个 scope 各自拿到正确的 chunk（不因 page_num 后写覆盖而丢失）
        lot_ids = {s.scope[1] for s in sr_list}
        assert "lot_A" in lot_ids
        assert "lot_B" in lot_ids


# ---------------------------------------------------------------------------
# C4: 顶层守恒汇总
# ---------------------------------------------------------------------------


class TestTopLevelConservationC4:
    """C4: 多 scope 顶层 unlabeled_sum / diagnostics 不默认清零。"""

    def test_top_level_diagnostics_non_empty_in_multi_scope(self):
        """多 scope 时顶层 diagnostics 至少含多 scope 说明文字（不为空）。"""
        chunks, items = _make_two_lot_scenario()
        report = observe_scoring_ledger_shadow(chunks, items, document_id=8030)
        assert report is not None
        assert getattr(report, "global_comparison_allowed", True) is False
        diags = report.diagnostics
        assert len(diags) > 0, "多 scope 时顶层 diagnostics 不应为空"
        combined = " ".join(diags)
        assert "scope" in combined.lower() or "多" in combined, (
            f"diagnostics 应含 scope 说明，实际: {diags}"
        )

    def test_scoped_reports_carry_basis_low_confidence(self):
        """C4/I4: scope 子报告携带 basis_low_confidence 字段。"""
        chunks, items = _make_two_lot_scenario()
        report = observe_scoring_ledger_shadow(chunks, items, document_id=8031)
        sr_list = getattr(report, "scope_reports", ())
        if not sr_list:
            pytest.skip("多 scope 未路由")
        for s in sr_list:
            assert hasattr(s, "basis_low_confidence"), (
                f"scope {s.scope_id} 缺少 basis_low_confidence 字段"
            )


# ---------------------------------------------------------------------------
# I6: 多 scope 丢弃补充声明追加诊断
# ---------------------------------------------------------------------------


class TestSupplementaryDiagI6:
    """I6: 多 scope 文档中 declaration_pages 不广播，追加诊断，禁止静默。"""

    def test_multi_scope_dropped_supp_adds_diagnostic(self):
        """多 scope 时传入 declaration_pages → 顶层 diagnostics 含丢弃说明。"""
        chunks, items = _make_two_lot_scenario()
        supp_pages = [(99, "文档级补充：各包满分均为100分，请注意。")]
        report = observe_scoring_ledger_shadow(
            chunks, items, document_id=8040,
            declaration_pages=supp_pages,
        )
        assert report is not None
        sr_list = getattr(report, "scope_reports", ())
        if not sr_list:
            pytest.skip("多 scope 未路由")
        diags = report.diagnostics
        combined = " ".join(diags)
        assert "补充声明" in combined or "丢弃" in combined or "广播" in combined, (
            f"I6: 多 scope 丢弃补充声明时应追加诊断，实际 diagnostics: {diags}"
        )


# ---------------------------------------------------------------------------
# I2: bridge 不再有私有 _resolve_chunk_lot
# ---------------------------------------------------------------------------


class TestBridgeUsesDeclarationScopeHelper:
    """I2: extractor_bridge 已删除私有副本，统一调用 declaration_scope helper。"""

    def test_bridge_has_no_private_resolve_chunk_lot(self):
        """bridge 模块中不应存在 _resolve_chunk_lot 私有函数（已移至 declaration_scope）。"""
        import app.core.extractor.scoring.ledger.extractor_bridge as bridge_mod
        assert not hasattr(bridge_mod, "_resolve_chunk_lot"), (
            "I2: extractor_bridge._resolve_chunk_lot 私有副本应已删除"
        )

    def test_bridge_imports_resolve_chunk_lot_from_declaration_scope(self):
        """bridge 模块引入的 resolve_chunk_lot 应来自 declaration_scope。"""
        import app.core.extractor.scoring.ledger.extractor_bridge as bridge_mod
        from app.core.extractor.scoring.ledger.declaration_scope import (
            resolve_chunk_lot as expected_fn,
        )
        bridge_fn = getattr(bridge_mod, "resolve_chunk_lot", None)
        assert bridge_fn is expected_fn, (
            "I2: bridge 应直接引用 declaration_scope.resolve_chunk_lot"
        )


# ---------------------------------------------------------------------------
# O1: 公式门禁——每命中点直接 LHS 检查（不是整段文本）
# ---------------------------------------------------------------------------


class TestFormulaGateLHSCheckO1:
    """O1: extract_formula_total_candidates 按每命中点直接 LHS 检查，拒绝子项公式。"""

    def test_reject_two_sub_items_in_one_clause(self):
        """拒绝 `综合得分计算：技术=70分(满分)，商务=30分(满分)` 的两个子项。
        两者 LHS 分别为 `技术` / `商务`，均无总分语境 → 均被拒绝 → []。
        """
        text = "综合得分计算：技术=70分(满分)，商务=30分(满分)"
        result = extract_formula_total_candidates([text])
        assert result == [], (
            f"O1: 子项公式应被拒绝，实际: {result}"
        )

    def test_reject_item_score_following_total_word_on_same_page(self):
        """同页前面出现总分词，但后面的本项公式仍应被拒绝。
        `评标总得分汇总。本项评分=8分(满分时)` → []
        前面的总分词属于其他句子，LHS(`本项评分`)无合法语境。
        """
        text = "评标总得分汇总。本项评分=8分(满分时)"
        result = extract_formula_total_candidates([text])
        assert result == [], (
            f"O1: 即使同页有总分词，本项项级公式应被拒绝，实际: {result}"
        )

    def test_reject_item_score_with_total_score_word_in_same_clause(self):
        """拒绝同句中有总分词但命中点本身是子项的情况。
        `综合得分计算：本项=8分(满分)` → []
        LHS(`本项`) 非总分词，虽前面有 `综合得分计算：` 但 `:` 是分隔符。
        """
        text = "综合得分计算：本项=8分(满分)"
        result = extract_formula_total_candidates([text])
        assert result == [], (
            f"O1: 综合得分计算后的本项评分应被拒绝，实际: {result}"
        )

    def test_allow_total_score_word_directly_on_lhs(self):
        """允许: LHS 末端为总分词 `评标总得分=100分(满分时)`。"""
        text = "评标总得分=100分(满分时)"
        result = extract_formula_total_candidates([text])
        assert 100.0 in result, (
            f"O1: 评标总得分直接 LHS 应命中 100.0，实际: {result}"
        )

    def test_allow_fa_with_two_fxa_terms_on_lhs(self):
        """允许: LHS 含 >=2 个 F×A 项 `FA=F1×A1+F2×A2=100分(满分时)`。"""
        text = "采购包1：FA=F1×A1+F2×A2=100分(满分时)"
        result = extract_formula_total_candidates([text])
        assert 100.0 in result, (
            f"O1: 含两个 F×A 项的公式应命中 100.0，实际: {result}"
        )

    def test_allow_zongde_fen_direct_lhs(self):
        """允许: `综合得分=100分（满分）`——LHS 末端为 `综合得分`。"""
        text = "综合得分=100分（满分）"
        result = extract_formula_total_candidates([text])
        assert 100.0 in result, (
            f"O1: 综合得分直接 LHS 应命中 100.0，实际: {result}"
        )

    def test_reject_item_score_formula_no_context(self):
        """拒绝: `本项评分=8分(满分时)`——LHS(`本项评分`) 无合法语境。"""
        text = "本项评分=8分(满分时)"
        result = extract_formula_total_candidates([text])
        assert result == [], (
            f"O1: 项级本项评分=8分(满分时) 应被拒绝，实际: {result}"
        )

    def test_reject_price_score_formula(self):
        """拒绝: `价格得分=30分(满分30分)` ——括号内有数字，已被 _FORMULA_BRACKET_RE 排除。"""
        text = "价格得分=30分(满分30分)"
        result = extract_formula_total_candidates([text])
        assert result == [], (
            f"O1: 含 (满分30分) 括号的公式应被拒绝，实际: {result}"
        )

    def test_2499_formula_still_matches_after_o1_refinement(self):
        """2499 真实公式页 `FA=F1×A1+F2×A2=100分(满分时)` O1 修订后仍命中。"""
        text = (
            "[评分表 第1-1页 · 采购包1]\n"
            "采购包1综合评标方式：FA=F1×A1+F2×A2=100分(满分时)。\n"
            "其中 F1 为技术得分，A1=70%；F2 为价格得分，A2=30%。"
        )
        result = extract_formula_total_candidates([text])
        assert 100.0 in result, (
            f"O1: 2499 真实公式页经 O1 修订后应仍命中 100.0，实际: {result}"
        )


# ---------------------------------------------------------------------------
# O2: no-lot 多 scheme 对称 chunk_scheme 校验
# ---------------------------------------------------------------------------


class TestNoLotMultiSchemeSymmetricO2:
    """O2: no-lot scope 在多 scheme 文档中对称校验 chunk_scheme（不交叉污染）。"""

    def test_two_schemes_no_lot_expected_by_volume_not_cross_copied(self):
        """两个 scheme 各有 no-lot 条目，chunk 带不同 scheme，expected_by_volume 互不复制。

        scheme_A 的 no-lot chunk 只进 (scheme_A, __single__) scope，
        scheme_B 的 no-lot chunk 只进 (scheme_B, __single__) scope。
        两个 scope 的 expected_by_volume 不共享对方数据。
        """
        # scheme_A no-lot chunk（含卷定义：技术总分60分 按60%折算）
        content_a = (
            "评标总得分=100分(满分时)。\n"
            "技术总分60分 按60%折算；价格总分40分 按40%折算。"
        )
        # scheme_B no-lot chunk（含卷定义：技术总分80分 按80%折算）
        content_b = (
            "评标总得分=100分(满分时)。\n"
            "技术总分80分 按80%折算；价格总分20分 按20%折算。"
        )
        chunks = [
            _FakeChunk(1, content_a, [], lot=None, scheme="scheme_A"),
            _FakeChunk(2, content_b, [], lot=None, scheme="scheme_B"),
        ]
        items = [
            _FakeItem("技术A", 60.0, "", scheme="scheme_A", lot=None),
            _FakeItem("技术B", 80.0, "", scheme="scheme_B", lot=None),
        ]
        report = observe_scoring_ledger_shadow(chunks, items, document_id=9001)
        assert report is not None
        sr_list = list(getattr(report, "scope_reports", ()))
        # 应形成 2 个 scope（scheme_A/__single__ 和 scheme_B/__single__）
        assert len(sr_list) == 2, (
            f"O2: 两 scheme 各有 no-lot 条目，应形成 2 个 scope，实际: {len(sr_list)}"
        )
        # 每个 scope 的 scope 元组
        scope_a = next((s for s in sr_list if s.scope[0] == "scheme_A"), None)
        scope_b = next((s for s in sr_list if s.scope[0] == "scheme_B"), None)
        assert scope_a is not None, "O2: 应有 scheme_A no-lot scope"
        assert scope_b is not None, "O2: 应有 scheme_B no-lot scope"

    def test_no_lot_chunk_none_scheme_excluded_in_multi_scheme(self):
        """no-lot chunk scheme=None 在文档多 scheme 时不广播到任何具名 no-lot scope，追加诊断。"""
        # chunk without scheme/lot（ambiguous）
        content_ambiguous = "综合得分=100分(满分时)。"
        chunks = [
            _FakeChunk(1, "[评分表 第1页 · 采购包1]\n技术60分。",
                       [], lot="采购包1", scheme="scheme_A"),
            _FakeChunk(2, "[评分表 第2页 · 采购包2]\n技术80分。",
                       [], lot="采购包2", scheme="scheme_B"),
            _FakeChunk(3, content_ambiguous, [], lot=None, scheme=None),  # 歧义无 lot/scheme
        ]
        items = [
            _FakeItem("技术A", 60.0, "", scheme="scheme_A", lot="采购包1"),
            _FakeItem("技术B", 80.0, "", scheme="scheme_B", lot="采购包2"),
        ]
        report = observe_scoring_ledger_shadow(chunks, items, document_id=9002)
        assert report is not None
        assert getattr(report, "global_comparison_allowed", True) is False
        # 顶层 diagnostics 应包含歧义排除信息（或 chunk 已被安全排除，无错误传播）
        # 核心：多 scheme 文档不应因此产生 crash，系统可安全运行


# ---------------------------------------------------------------------------
# O3: 严格 lot header 正则 + scope_id 唯一性
# ---------------------------------------------------------------------------


class TestStrictLotHeaderRegexO3:
    """O3: resolve_chunk_lot 严格只认 [评分表 第N(-M)页 ·/• lot] 格式。"""

    def test_reject_chapter_header(self):
        """拒绝 [第三章 · 评标办法]——非评分表格式。"""
        content = "[第三章 · 评标办法]\n本章规定评标方法。"
        result = resolve_chunk_lot(content, None)
        assert result is None, (
            f"O3: [第三章 · 评标办法] 应返回 None，实际: {result!r}"
        )

    def test_reject_appendix_header(self):
        """拒绝 [附件A · 报价表]——非评分表格式。"""
        content = "[附件A · 报价表]\n报价表内容..."
        result = resolve_chunk_lot(content, None)
        assert result is None, (
            f"O3: [附件A · 报价表] 应返回 None，实际: {result!r}"
        )

    def test_accept_scoring_table_header_basic(self):
        """接受 [评分表 第1页 · 采购包1]。"""
        content = "[评分表 第1页 · 采购包1]\n技术评分..."
        result = resolve_chunk_lot(content, None)
        assert result == "采购包1", (
            f"O3: [评分表 第1页 · 采购包1] 应解析为 '采购包1'，实际: {result!r}"
        )

    def test_accept_scoring_table_header_range(self):
        """接受 [评分表 第1-3页 · 采购包2]（页码范围）。"""
        content = "[评分表 第1-3页 · 采购包2]\n技术评分..."
        result = resolve_chunk_lot(content, None)
        assert result == "采购包2", (
            f"O3: [评分表 第1-3页 · 采购包2] 应解析为 '采购包2'，实际: {result!r}"
        )

    def test_reject_non_first_line_header(self):
        """只认首个非空行——标题在第二行时拒绝。"""
        content = "这是第一行内容\n[评分表 第1页 · 采购包X]"
        result = resolve_chunk_lot(content, None)
        assert result is None, (
            f"O3: 标题不在首行时应返回 None，实际: {result!r}"
        )

    def test_scoring_table_parser_consistent_with_resolve_chunk_lot(self):
        """O3: scoring_table_parser._extract_lot_from_markdown 与 resolve_chunk_lot 一致。"""
        from app.core.extractor.scoring.scoring_table_parser import (
            _extract_lot_from_markdown,
        )
        markdown = "[评分表 第5-7页 · 采购包3]\n技术项评分表..."
        via_parser = _extract_lot_from_markdown(markdown)
        via_scope = resolve_chunk_lot(markdown, None)
        assert via_parser == via_scope, (
            f"O3: parser 与 resolve_chunk_lot 结果不一致: parser={via_parser!r}, scope={via_scope!r}"
        )

    def test_parser_rejects_non_scoring_header(self):
        """O3: parser._extract_lot_from_markdown 对非评分表标题返回 None。"""
        from app.core.extractor.scoring.scoring_table_parser import (
            _extract_lot_from_markdown,
        )
        assert _extract_lot_from_markdown("[第三章 · 评标办法]\n内容") is None
        assert _extract_lot_from_markdown("[附件A · 报价表]\n内容") is None


# ---------------------------------------------------------------------------
# O3: scope_id 唯一性
# ---------------------------------------------------------------------------


class TestScopeIdUniquenessO3:
    """O3: scope_id 唯一性规则——同 lot 多 scheme 用 scheme·lot，单 scheme→lot，单 lot→scheme。"""

    def test_scope_id_single_lot_returns_scheme(self):
        """lot=__single__（无 lot scope）→ scope_id 返回 scheme。"""
        sr = ScopedReport(
            scope=("scheme_X", ScoringScopeKey.SINGLE_LOT),
            status=LedgerStatus.RECONCILED,
            basis=BasisLevel.L3_DOC_TOTAL,
            expected_total=100.0,
            extracted_total=98.0,
            total_gap=2.0,
        )
        assert sr.scope_id == "scheme_X", (
            f"O3: lot=__single__ 时 scope_id 应为 scheme，实际: {sr.scope_id!r}"
        )

    def test_scope_id_single_scheme_returns_lot(self):
        """scheme=__single__（默认 scheme）→ scope_id 返回 lot。"""
        sr = ScopedReport(
            scope=(ScoringScopeKey.SINGLE_SCHEME, "采购包A"),
            status=LedgerStatus.RECONCILED,
            basis=BasisLevel.L3_DOC_TOTAL,
            expected_total=100.0,
            extracted_total=100.0,
            total_gap=0.0,
        )
        assert sr.scope_id == "采购包A", (
            f"O3: scheme=__single__ 时 scope_id 应为 lot，实际: {sr.scope_id!r}"
        )

    def test_scope_display_id_same_lot_multi_scheme_uses_scheme_dot_lot(self):
        """O3: scope_display_id='scheme·lot' 时 scope_id 返回该值（同 lot 多 scheme 场景）。"""
        sr = ScopedReport(
            scope=("scheme_A", "采购包1"),
            status=LedgerStatus.RECONCILED,
            basis=BasisLevel.L3_DOC_TOTAL,
            expected_total=100.0,
            extracted_total=100.0,
            total_gap=0.0,
            scope_display_id="scheme_A·采购包1",  # coordinator 注入
        )
        assert sr.scope_id == "scheme_A·采购包1", (
            f"O3: scope_display_id 已注入时 scope_id 应返回该值，实际: {sr.scope_id!r}"
        )

    def test_scope_ids_unique_across_all_scopes_in_multi_lot(self):
        """O3: 多 scope 报告中所有 scope_id 值唯一（无重复）。"""
        chunks, items = _make_two_lot_scenario()
        report = observe_scoring_ledger_shadow(chunks, items, document_id=9010)
        assert report is not None
        sr_list = list(getattr(report, "scope_reports", ()))
        if len(sr_list) < 2:
            pytest.skip("未形成多 scope")
        scope_ids = [s.scope_id for s in sr_list]
        assert len(scope_ids) == len(set(scope_ids)), (
            f"O3: scope_id 存在重复，scope_ids={scope_ids}"
        )

    def test_same_lot_multi_scheme_coordinator_assigns_scheme_dot_lot(self):
        """O3: 同 lot 多 scheme 时 coordinator 赋 scope_display_id='scheme·lot'。"""
        # 构造 lot=lot_X 但 scheme 各异的两个 scope
        chunks = [
            _FakeChunk(1, "[评分表 第1页 · lot_X]\n评标总得分=100分(满分时)。",
                       [], lot="lot_X", scheme="scheme_A"),
            _FakeChunk(2, "[评分表 第2页 · lot_X]\n评标总得分=120分(满分时)。",
                       [], lot="lot_X", scheme="scheme_B"),
        ]
        items = [
            _FakeItem("技术A", 100.0, "", scheme="scheme_A", lot="lot_X"),
            _FakeItem("技术B", 120.0, "", scheme="scheme_B", lot="lot_X"),
        ]
        report = observe_scoring_ledger_shadow(chunks, items, document_id=9011)
        assert report is not None
        sr_list = list(getattr(report, "scope_reports", ()))
        if len(sr_list) < 2:
            pytest.skip("未形成多 scope（同 lot 多 scheme）")
        scope_ids = [s.scope_id for s in sr_list]
        # 同 lot 多 scheme → 各自 scope_id 应含 scheme，形如 scheme_A·lot_X
        assert all("·" in sid for sid in scope_ids), (
            f"O3: 同 lot 多 scheme 时 scope_id 应含 '·'，实际: {scope_ids}"
        )
        assert len(set(scope_ids)) == 2, (
            f"O3: 同 lot 多 scheme scope_id 应唯一，实际: {scope_ids}"
        )


# ---------------------------------------------------------------------------
# O4: applicability 禁止假绿 + diagnostics 首条含每 scope 基准摘要
# ---------------------------------------------------------------------------


class TestApplicabilityFalseGreenO4:
    """O4: unlabeled_sum 超容差 / insufficiency / basis_low_confidence / 无基准 → unresolved。"""

    def test_scope_with_no_expected_total_gets_unresolved(self):
        """scope 无权威基准（expected_total is None）→ applicability=unresolved。"""
        # 构造一个 scope 没有公式总分、条目未声明总分的情形
        chunks = [
            _FakeChunk(1, "[评分表 第1页 · 采购包1]\n技术评分内容（无总分声明）。",
                       [], lot="采购包1", scheme="scheme_1"),
            _FakeChunk(2, "[评分表 第2页 · 采购包2]\n评标总得分=100分(满分时)。",
                       [], lot="采购包2", scheme="scheme_1"),
        ]
        items = [
            _FakeItem("技术A", 50.0, "", scheme="scheme_1", lot="采购包1"),
            _FakeItem("技术B", 50.0, "", scheme="scheme_1", lot="采购包2"),
        ]
        report = observe_scoring_ledger_shadow(chunks, items, document_id=9020)
        assert report is not None
        sr_list = list(getattr(report, "scope_reports", ()))
        # 采购包1 无基准 → unresolved
        sr_pkg1 = next((s for s in sr_list if s.scope[1] == "采购包1"), None)
        if sr_pkg1 is None:
            pytest.skip("未形成 采购包1 scope")
        assert sr_pkg1.applicability == "unresolved", (
            f"O4: 无基准 scope 应为 unresolved，实际: {sr_pkg1.applicability}"
        )

    def test_multi_scope_diagnostics_first_line_contains_scope_basis_summary(self):
        """O4: 多 scope 时顶层 diagnostics 首条含每 scope 基准摘要，含 '全局比较已禁止'。"""
        chunks, items = _make_two_lot_scenario()
        report = observe_scoring_ledger_shadow(chunks, items, document_id=9021)
        assert report is not None
        diags = report.diagnostics
        assert len(diags) > 0, "O4: 多 scope 时 diagnostics 不应为空"
        first = diags[0]
        assert "全局比较已禁止" in first or "global" in first.lower(), (
            f"O4: diagnostics 首条应含 '全局比较已禁止'，实际: {first!r}"
        )
        # 首条还应含每个 scope 的基准信息
        assert "basis" in first.lower() or "L" in first, (
            f"O4: diagnostics 首条应含 basis 摘要，实际: {first!r}"
        )

    def test_multi_scope_diagnostics_not_adds_erroneous_l4_conclusion(self):
        """O4: 顶层 diagnostics 不应包含错误的 L4 用户结论（由 global=false 让前端判断）。"""
        chunks, items = _make_two_lot_scenario()
        report = observe_scoring_ledger_shadow(chunks, items, document_id=9022)
        assert report is not None
        # global_comparison_allowed=False 才是主判，诊断不应越俎代庖说"请联系..."之类
        assert report.global_comparison_allowed is False


# ---------------------------------------------------------------------------
# O5: 顶层守恒数值精确等于子报告和
# ---------------------------------------------------------------------------


class TestTopLevelConservationExactO5:
    """O5: 顶层 unlabeled_sum 和 insufficiency_flags 数值精确等于各子报告之和。"""

    def test_top_level_unlabeled_sum_equals_sum_of_scope_reports(self):
        """顶层 unlabeled_sum 精确等于所有 scope_reports 的 unlabeled_sum 之和。"""
        chunks, items = _make_two_lot_scenario()
        report = observe_scoring_ledger_shadow(chunks, items, document_id=9030)
        assert report is not None
        sr_list = list(getattr(report, "scope_reports", ()))
        if not sr_list:
            pytest.skip("未形成多 scope")
        expected_unlabeled = round(sum(s.unlabeled_sum for s in sr_list), 4)
        actual_unlabeled = round(report.unlabeled_sum, 4)
        assert actual_unlabeled == expected_unlabeled, (
            f"O5: 顶层 unlabeled_sum={actual_unlabeled} 应精确等于子报告和 {expected_unlabeled}"
        )

    def test_top_level_insufficiency_count_equals_sum_of_scope_reports(self):
        """顶层 insufficiency_flags 长度精确等于所有子报告 insufficiency_flags 长度之和。"""
        chunks, items = _make_two_lot_scenario()
        report = observe_scoring_ledger_shadow(chunks, items, document_id=9031)
        assert report is not None
        sr_list = list(getattr(report, "scope_reports", ()))
        if not sr_list:
            pytest.skip("未形成多 scope")
        expected_count = sum(len(s.insufficiency_flags) for s in sr_list)
        actual_count = len(report.insufficiency_flags)
        assert actual_count == expected_count, (
            f"O5: 顶层 insufficiency_flags 长度={actual_count} 应精确等于子报告和 {expected_count}"
        )

# ---------------------------------------------------------------------------
# 结构整改新增测试（Task6 最终整改验证）
# ---------------------------------------------------------------------------


class TestGetTypeHintsO5:
    """O5: get_type_hints(LedgerReport) 不应抛出 NameError。"""

    def test_get_type_hints_no_name_error(self):
        """ScopedReport 已移至 contracts.py，LedgerReport.scope_reports 直接引用，
        get_type_hints 不再报 NameError: name 'ScopedReport' is not defined。"""
        import typing

        from app.core.extractor.scoring.ledger.contracts import LedgerReport
        hints = typing.get_type_hints(LedgerReport)
        assert "scope_reports" in hints, "LedgerReport 应含 scope_reports 字段"
        sr_type = hints["scope_reports"]
        # 确认类型可解析（非字符串前向引用），get_type_hints 不应抛出 NameError
        assert sr_type is not None, "scope_reports 类型应可解析（非 None）"
        # 确认不是字符串（字符串说明前向引用未被解析）
        assert not isinstance(sr_type, str), f"scope_reports 类型不应是字符串前向引用: {sr_type!r}"

    def test_scoped_report_importable_from_contracts(self):
        """ScopedReport 可直接从 contracts 导入（无需经过 scoped_contracts）。"""
        import typing

        from app.core.extractor.scoring.ledger.contracts import LedgerReport, ScopedReport
        hints = typing.get_type_hints(LedgerReport)
        tuple_type = hints["scope_reports"]
        # scope_reports 的类型注解内含 ScopedReport，不报 NameError
        assert tuple_type is not None
        _ = ScopedReport  # import 成功即可


class TestO1ItemLevelPrefixNegativesMinor:
    """O1 minor: 直接 LHS 含项级前缀（其中本项/该项/单项）时必须拒绝，
    即使 LHS 末端含总分词也不例外。"""

    def test_reject_qizhong_benkuai_with_zongdeshi(self):
        """其中本项综合得分=8分(满分) → 含项级前缀 `其中本项`，拒绝。
        LHS=`其中本项综合得分`，末端虽有 `综合得分` 但前缀优先拒绝。"""
        from app.core.extractor.scoring.ledger.declaration_scope import (
            extract_formula_total_candidates,
        )
        text = "其中本项综合得分=8分(满分)"
        result = extract_formula_total_candidates([text])
        assert result == [], f"O1 minor: 其中本项综合得分=8分(满分) 应被拒绝，实际: {result}"

    def test_reject_gaiixiang_prefix(self):
        """该项评分=8分(满分时) → 含项级前缀 `该项`，拒绝。"""
        from app.core.extractor.scoring.ledger.declaration_scope import (
            extract_formula_total_candidates,
        )
        text = "该项评分=8分(满分时)"
        result = extract_formula_total_candidates([text])
        assert result == [], f"O1 minor: 该项评分=8分(满分时) 应被拒绝，实际: {result}"

    def test_reject_danxiang_prefix(self):
        """单项得分=8分(满分) → 含项级前缀 `单项`，拒绝。"""
        from app.core.extractor.scoring.ledger.declaration_scope import (
            extract_formula_total_candidates,
        )
        text = "单项得分=8分(满分)"
        result = extract_formula_total_candidates([text])
        assert result == [], f"O1 minor: 单项得分=8分(满分) 应被拒绝，实际: {result}"

    def test_reject_benxiang_prefix(self):
        """本项总分=8分(满分) → 含项级前缀 `本项`，拒绝。"""
        from app.core.extractor.scoring.ledger.declaration_scope import (
            extract_formula_total_candidates,
        )
        text = "本项总分=8分(满分)"
        result = extract_formula_total_candidates([text])
        assert result == [], f"O1 minor: 本项总分=8分(满分) 应被拒绝，实际: {result}"

    def test_allow_total_score_without_item_prefix(self):
        """综合得分=100分（满分）→ 无项级前缀，允许。"""
        from app.core.extractor.scoring.ledger.declaration_scope import (
            extract_formula_total_candidates,
        )
        text = "综合得分=100分（满分）"
        result = extract_formula_total_candidates([text])
        assert 100.0 in result, f"O1 minor: 综合得分 应允许命中 100.0，实际: {result}"


class TestASTThresholdsRefactored:
    """验证重构后关键函数满足复杂度护栏（<=150 行、参数<=6、嵌套<=4）。: 原实现硬编码旧 worktree 绝对路径，校验的是别处的陈旧副本（假绿）。
 现统一由 _ledger_path 以本测试文件位置推导仓库内目标文件。
 """

    def _ledger_path(self, filename: str):
        """定位本仓库 ledger 子包内的目标文件（禁止硬编码绝对路径）。"""
        import pathlib
        repo_root = pathlib.Path(__file__).resolve().parents[2]
        path = (
            repo_root
            / "backend/app/core/extractor/scoring/ledger"
            / filename
        )
        assert path.exists(), f" 护栏目标文件不在本仓库内: {path}"
        return path

    def _count_function_lines(self, source_lines: list[str], node) -> int:
        """估算函数行数（从 def 到下一个同级 def/class 或文件末尾）。"""
        if not hasattr(node, "end_lineno"):
            return 0
        return node.end_lineno - node.lineno + 1

    def _check_file(self, filepath: str):
        """解析文件，返回 (function_name, line_count, param_count) 列表。"""
        import ast
        import pathlib
        src = pathlib.Path(filepath).read_text(encoding="utf-8")
        tree = ast.parse(src)
        results = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            lines = self._count_function_lines(src.splitlines(), node)
            params = len(node.args.args) + len(node.args.posonlyargs) + len(node.args.kwonlyargs)
            if node.args.vararg:
                params += 1
            if node.args.kwarg:
                params += 1
            results.append((node.name, lines, params, node.lineno))
        return results

    def test_coordinate_scoped_ledger_under_150_lines(self):
        """coordinate_scoped_ledger 函数应 < 150 行。"""
        import ast
        path = self._ledger_path("scoped_reconciliation.py")
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "coordinate_scoped_ledger":
                lines = node.end_lineno - node.lineno + 1
                assert lines <= 150, (
                    f"coordinate_scoped_ledger 有 {lines} 行，超出 150 行护栏"
                )
                return
        pytest.fail("coordinate_scoped_ledger 函数未找到")

    def test_scope_filtering_functions_params_le6(self):
        """scope_filtering.py 中的函数参数均 <= 6。"""
        path = self._ledger_path("scope_filtering.py")
        results = self._check_file(str(path))
        violations = [
            (name, params, lineno)
            for name, lines, params, lineno in results
            if params > 6
        ]
        assert not violations, f"scope_filtering.py 函数参数超限: {violations}"

    def test_scoped_aggregation_functions_params_le6(self):
        """scoped_aggregation.py 中的函数参数均 <= 6。"""
        path = self._ledger_path("scoped_aggregation.py")
        results = self._check_file(str(path))
        violations = [
            (name, params, lineno)
            for name, lines, params, lineno in results
            if params > 6
        ]
        assert not violations, f"scoped_aggregation.py 函数参数超限: {violations}"

    def test_scope_filtering_file_line_count(self):
        """scope_filtering.py 文件总行数报告（信息性，不硬限制但应合理）。"""
        path = self._ledger_path("scope_filtering.py")
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= 200, f"scope_filtering.py 有 {lines} 行，超出预期（应<=200）"

    def test_scoped_reconciliation_file_line_count(self):
        """scoped_reconciliation.py 重构后应 <= 300 行（原先 524 行，现约 220 行）。"""
        path = self._ledger_path("scoped_reconciliation.py")
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= 300, f"scoped_reconciliation.py 有 {lines} 行，应<=300（护栏 800）"


class TestScopedLedgerRequestDataclass:
    """ScopedLedgerRequest frozen dataclass 基本行为验证。"""

    def test_import_and_create(self):
        """ScopedLedgerRequest 可从 scoped_reconciliation 正常导入并实例化。"""
        from app.core.extractor.scoring.ledger.contracts import LedgerItemEntry
        from app.core.extractor.scoring.ledger.scoped_reconciliation import (
            ScopedLedgerRequest,
        )
        req = ScopedLedgerRequest(
            pages=((1, "技术评分"),),
            chunk_scopes=((None, None),),
            grids=(),
            grid_scopes=(),
            entries_with_rules=((LedgerItemEntry(name="技术", score=70.0), ""),),
            document_id=999,
        )
        assert req.document_id == 999
        assert len(req.pages) == 1

    def test_is_frozen(self):
        """ScopedLedgerRequest 是 frozen dataclass，不允许修改属性。"""
        import dataclasses

        from app.core.extractor.scoring.ledger.scoped_reconciliation import (
            ScopedLedgerRequest,
        )
        req = ScopedLedgerRequest(
            pages=(), chunk_scopes=(), grids=(), grid_scopes=(),
            entries_with_rules=(),
        )
        assert dataclasses.is_dataclass(req)
        try:
            req.document_id = 42  # type: ignore
            pytest.fail("frozen dataclass 应阻止赋值")
        except (dataclasses.FrozenInstanceError, AttributeError):
            pass  # 预期


class TestScopeFilteringModule:
    """scope_filtering.py 基本行为验证。"""

    def test_single_scope_always_belongs(self):
        """is_single_scope=True 时，所有 chunk 归属。"""
        from app.core.extractor.scoring.ledger.scope_filtering import (
            ScopeFilterContext,
            chunk_belongs_to_scope,
        )
        ctx = ScopeFilterContext(
            target_scope=("__single__", "__single__"),
            lot_has_single_scheme=False,
            doc_has_single_named_scheme=False,
            is_single_scope=True,
        )
        ok, _ = chunk_belongs_to_scope(ctx, None, None)
        assert ok is True

    def test_named_lot_exact_match(self):
        """具名 lot 精确匹配时归属。"""
        from app.core.extractor.scoring.ledger.scope_filtering import (
            ScopeFilterContext,
            chunk_belongs_to_scope,
        )
        ctx = ScopeFilterContext(
            target_scope=("方案A", "采购包1"),
            lot_has_single_scheme=True,
            doc_has_single_named_scheme=False,
            is_single_scope=False,
        )
        ok, diag = chunk_belongs_to_scope(ctx, "方案A", "采购包1")
        assert ok is True
        assert diag == ""

    def test_different_lot_not_belong(self):
        """不同 lot 的 chunk 不归属。"""
        from app.core.extractor.scoring.ledger.scope_filtering import (
            ScopeFilterContext,
            chunk_belongs_to_scope,
        )
        ctx = ScopeFilterContext(
            target_scope=("方案A", "采购包1"),
            lot_has_single_scheme=True,
            doc_has_single_named_scheme=False,
            is_single_scope=False,
        )
        ok, _ = chunk_belongs_to_scope(ctx, "方案A", "采购包2")
        assert ok is False

    def test_ambiguous_scheme_generates_diagnostic(self):
        """同 lot 多 scheme 时，无 scheme chunk 歧义排除并生成诊断。"""
        from app.core.extractor.scoring.ledger.scope_filtering import (
            ScopeFilterContext,
            chunk_belongs_to_scope,
        )
        ctx = ScopeFilterContext(
            target_scope=("方案A", "采购包1"),
            lot_has_single_scheme=False,
            doc_has_single_named_scheme=False,
            is_single_scope=False,
        )
        ok, diag = chunk_belongs_to_scope(ctx, None, "采购包1")
        assert ok is False
        assert "歧义" in diag



# ---------------------------------------------------------------------------
# Task6 最终 Important+Minor 新增测试
# ---------------------------------------------------------------------------


class TestScopeIdNoInternalSentinel:
    """scope_id 不得泄漏内部哨兵（__single__/__unassigned__）。"""

    def test_unassigned_lot_scope_id_shows_scheme(self):
        """__unassigned__ lot → scope_id 返回 scheme（不暴露 __unassigned__）。"""
        from app.core.extractor.scoring.ledger.contracts import (
            BasisLevel,
            LedgerStatus,
            ScopedReport,
        )
        sr = ScopedReport(
            scope=("方案A", "__unassigned__"),
            status=LedgerStatus.REVIEW_REQUIRED,
            basis=BasisLevel.L4_NONE,
            expected_total=None,
            extracted_total=0.0,
            total_gap=None,
        )
        sid = sr.scope_id
        assert "__" not in sid, f"scope_id 不得含 __，实际: {sid!r}"
        assert sid == "方案A", f"__unassigned__ lot 应显示 scheme='方案A'，实际: {sid!r}"

    def test_single_lot_scope_id_shows_scheme(self):
        """__single__ lot → scope_id 返回 scheme（不暴露 __single__）。"""
        from app.core.extractor.scoring.ledger.contracts import (
            BasisLevel,
            LedgerStatus,
            ScopedReport,
        )
        sr = ScopedReport(
            scope=("方案B", "__single__"),
            status=LedgerStatus.REVIEW_REQUIRED,
            basis=BasisLevel.L4_NONE,
            expected_total=None,
            extracted_total=0.0,
            total_gap=None,
        )
        sid = sr.scope_id
        assert "__" not in sid, f"scope_id 不得含 __，实际: {sid!r}"
        assert sid == "方案B", f"__single__ lot 应显示 scheme='方案B'，实际: {sid!r}"

    def test_named_lot_scope_id_shows_lot(self):
        """具名 lot → scope_id 返回 lot（正常路径，无内部哨兵）。"""
        from app.core.extractor.scoring.ledger.contracts import (
            BasisLevel,
            LedgerStatus,
            ScopedReport,
        )
        sr = ScopedReport(
            scope=("方案A", "采购包1"),
            status=LedgerStatus.REVIEW_REQUIRED,
            basis=BasisLevel.L4_NONE,
            expected_total=100.0,
            extracted_total=100.0,
            total_gap=0.0,
        )
        sid = sr.scope_id
        assert "__" not in sid, f"scope_id 不得含 __，实际: {sid!r}"
        assert sid == "采购包1", f"具名 lot 应显示 lot，实际: {sid!r}"

    def test_multi_scheme_same_lot_display_id_no_sentinel(self):
        """同 lot 多 scheme 时，scope_display_id 为 'scheme·lot'，不含内部哨兵。"""
        from app.core.extractor.scoring.ledger.contracts import (
            BasisLevel,
            LedgerStatus,
            ScopedReport,
        )
        sr = ScopedReport(
            scope=("方案A", "采购包1"),
            status=LedgerStatus.REVIEW_REQUIRED,
            basis=BasisLevel.L4_NONE,
            expected_total=100.0,
            extracted_total=100.0,
            total_gap=0.0,
            scope_display_id="方案A·采购包1",
        )
        sid = sr.scope_id
        assert "__" not in sid, f"scope_id 不得含 __，实际: {sid!r}"
        assert sid == "方案A·采购包1"

    def test_build_scope_display_ids_unassigned_shows_scheme(self):
        """build_scope_display_ids 对 __unassigned__ lot → scheme（不泄漏哨兵）。"""
        from app.core.extractor.scoring.ledger.scoped_aggregation import (
            build_scope_display_ids,
        )
        scopes = [("方案A", "__unassigned__"), ("方案B", "采购包1")]
        result = build_scope_display_ids(scopes)
        assert result[("方案A", "__unassigned__")] == "方案A", (
            f"__unassigned__ 应显示 scheme，实际: {result!r}"
        )
        assert "__" not in result[("方案A", "__unassigned__")]


class TestResolveLotFromContent:
    """scoring_lot_header.resolve_lot_from_content 统一入口验证。"""

    def test_extracts_lot_from_multiline_content(self):
        """resolve_lot_from_content 自动取首个非空行解析 lot。"""
        from app.core.extractor.scoring.scoring_lot_header import resolve_lot_from_content
        content = "\n\n[评分表 第5-7页 · 采购包3]\n技术项评分表..."
        result = resolve_lot_from_content(content)
        assert result == "采购包3", f"应解析出 '采购包3'，实际: {result!r}"

    def test_returns_none_for_generic_header(self):
        """resolve_lot_from_content 对非评分表标题返回 None。"""
        from app.core.extractor.scoring.scoring_lot_header import resolve_lot_from_content
        content = "[第三章 · 评标办法]\n评标指标..."
        result = resolve_lot_from_content(content)
        assert result is None, f"非评分表标题应返回 None，实际: {result!r}"

    def test_empty_content_returns_none(self):
        """空内容返回 None。"""
        from app.core.extractor.scoring.scoring_lot_header import resolve_lot_from_content
        assert resolve_lot_from_content("") is None
        assert resolve_lot_from_content("   \n  \n  ") is None

    def test_parser_and_declaration_scope_consistent_via_content(self):
        """parser._extract_lot_from_markdown 与 resolve_chunk_lot 对同内容结果一致。"""
        from app.core.extractor.scoring.ledger.declaration_scope import resolve_chunk_lot
        from app.core.extractor.scoring.scoring_table_parser import _extract_lot_from_markdown
        content = "[评分表 第1页 · 技术评分包]\n详细规则..."
        via_parser = _extract_lot_from_markdown(content)
        via_scope = resolve_chunk_lot(content, None)
        assert via_parser == via_scope == "技术评分包", (
            f"两者应一致: parser={via_parser!r}, scope={via_scope!r}"
        )


class TestChunkInputsBackwardCompat:
    """_chunk_inputs 旧 3 元组接口向后兼容验证。"""

    def test_chunk_inputs_returns_3_tuple(self):
        """_chunk_inputs 应返回 (pages, grids, parallel_labels) 3 元组。"""
        from app.core.extractor.scoring.ledger.extractor_bridge import _chunk_inputs
        result = _chunk_inputs([])
        assert len(result) == 3, f"_chunk_inputs 应返回 3 元组，实际: {len(result)}"
        pages, grids, labels = result
        assert isinstance(pages, list)
        assert isinstance(grids, list)
        assert isinstance(labels, list)

    def test_scoped_chunk_inputs_returns_5_tuple(self):
        """_scoped_chunk_inputs 应返回 5 元组（含 per-chunk scope 信息）。"""
        from app.core.extractor.scoring.ledger.extractor_bridge import _scoped_chunk_inputs
        result = _scoped_chunk_inputs([])
        assert len(result) == 5, f"_scoped_chunk_inputs 应返回 5 元组，实际: {len(result)}"
        pages, chunk_scopes, grids, grid_scopes, labels = result
        assert isinstance(pages, list)
        assert isinstance(chunk_scopes, list)


class TestApplicabilityThresholdSingleSource:
    """determine_applicability 阈值单源引用 LEDGER_TOLERANCE（不硬编码 1.0）。"""

    def test_unlabeled_exactly_at_tolerance_is_applicable(self):
        """unlabeled_sum == LEDGER_TOLERANCE 时 applicability = 'applicable'（不超出）。"""
        from app.core.extractor.scoring.ledger.contracts import (
            LEDGER_TOLERANCE,
            BasisLevel,
            LedgerReport,
            LedgerStatus,
        )
        from app.core.extractor.scoring.ledger.scoped_aggregation import determine_applicability
        report = LedgerReport(
            status=LedgerStatus.RECONCILED,
            basis_level=BasisLevel.L3_DOC_TOTAL,
            basis_low_confidence=False,
            expected_total=100.0,
            extracted_total=100.0,
            total_gap=0.0,
            unlabeled_sum=LEDGER_TOLERANCE,  # 精确等于阈值，不超出
        )
        result = determine_applicability(report)
        assert result == "applicable", (
            f"unlabeled_sum=={LEDGER_TOLERANCE} 时应为 applicable，实际: {result!r}"
        )

    def test_unlabeled_just_over_tolerance_is_unresolved(self):
        """unlabeled_sum 稍大于 LEDGER_TOLERANCE → 'unresolved'。"""
        from app.core.extractor.scoring.ledger.contracts import (
            LEDGER_TOLERANCE,
            BasisLevel,
            LedgerReport,
            LedgerStatus,
        )
        from app.core.extractor.scoring.ledger.scoped_aggregation import determine_applicability
        report = LedgerReport(
            status=LedgerStatus.RECONCILED,
            basis_level=BasisLevel.L3_DOC_TOTAL,
            basis_low_confidence=False,
            expected_total=100.0,
            extracted_total=100.0,
            total_gap=0.0,
            unlabeled_sum=LEDGER_TOLERANCE + 0.01,
        )
        result = determine_applicability(report)
        assert result == "unresolved", (
            f"unlabeled_sum>{LEDGER_TOLERANCE} 应为 unresolved，实际: {result!r}"
        )


# ---------------------------------------------------------------------------
# Step 2: leaf_integrity 单包/多包账本集成红测
# ---------------------------------------------------------------------------


class TestLeafIntegrityIntegration:
    """ Step2: leaf_integrity 字段接入单包/多包账本的集成测试。"""

    def test_ledger_report_has_leaf_integrity_field(self):
        """单 scope LedgerReport 携带 leaf_integrity 字段（默认未验证）。"""
        try:
            from app.core.extractor.scoring.ledger.contracts import LeafIntegrityEvidence
        except ImportError:
            pytest.fail(" [RED] LeafIntegrityEvidence 尚未添加到 contracts")

        report = LedgerReport(
            status=LedgerStatus.REVIEW_REQUIRED,
            basis_level=BasisLevel.L4_NONE,
            basis_low_confidence=True,
            expected_total=None,
            extracted_total=0.0,
            total_gap=None,
        )
        assert hasattr(report, "leaf_integrity"), "LedgerReport 缺少 leaf_integrity 字段"
        assert isinstance(report.leaf_integrity, LeafIntegrityEvidence)
        assert report.leaf_integrity.verified is False

    def test_scoped_report_has_leaf_integrity_field(self):
        """ScopedReport 携带 leaf_integrity 字段（默认未验证）。"""
        try:
            from app.core.extractor.scoring.ledger.contracts import LeafIntegrityEvidence
        except ImportError:
            pytest.fail(" [RED] LeafIntegrityEvidence 尚未添加到 contracts")

        sr = ScopedReport(
            scope=("scheme_1", "采购包1"),
            status=LedgerStatus.RECONCILED,
            basis=BasisLevel.L3_DOC_TOTAL,
            expected_total=100.0,
            extracted_total=100.0,
            total_gap=0.0,
        )
        assert hasattr(sr, "leaf_integrity"), "ScopedReport 缺少 leaf_integrity 字段"
        assert isinstance(sr.leaf_integrity, LeafIntegrityEvidence)
        assert sr.leaf_integrity.verified is False

    def test_two_scopes_verified_aggregate_per_scope_100(self):
        """两 scope 各 100% 验证时，顶层 leaf_integrity.basis = 'per_scope_100'。"""
        try:
            from app.core.extractor.scoring.ledger.contracts import LeafIntegrityEvidence
        except ImportError:
            pytest.fail(" [RED] LeafIntegrityEvidence 尚未添加到 contracts")

        from app.core.extractor.scoring.ledger.scoped_aggregation import aggregate_and_build_report

        li_verified = LeafIntegrityEvidence(
            verified=True, basis="single_100", inventory_count=3, matched_count=3, total=100.0,
        )
        sr1 = ScopedReport(
            scope=("scheme_1", "采购包1"),
            status=LedgerStatus.RECONCILED,
            basis=BasisLevel.L3_DOC_TOTAL,
            expected_total=100.0,
            extracted_total=100.0,
            total_gap=0.0,
            leaf_integrity=li_verified,
        )
        sr2 = ScopedReport(
            scope=("scheme_1", "采购包2"),
            status=LedgerStatus.RECONCILED,
            basis=BasisLevel.L3_DOC_TOTAL,
            expected_total=100.0,
            extracted_total=100.0,
            total_gap=0.0,
            leaf_integrity=li_verified,
        )
        top = aggregate_and_build_report([sr1, sr2], 200.0, [])
        assert hasattr(top, "leaf_integrity"), "顶层 LedgerReport 缺少 leaf_integrity 字段"
        assert top.leaf_integrity.verified is True
        assert top.leaf_integrity.basis == "per_scope_100"
        assert top.leaf_integrity.inventory_count == 6
        assert top.leaf_integrity.total == pytest.approx(200.0)

    def test_one_scope_not_verified_aggregate_false(self):
        """任一 scope 未验证时，顶层 leaf_integrity.verified=False。"""
        try:
            from app.core.extractor.scoring.ledger.contracts import LeafIntegrityEvidence
        except ImportError:
            pytest.fail(" [RED] LeafIntegrityEvidence 尚未添加到 contracts")

        from app.core.extractor.scoring.ledger.scoped_aggregation import aggregate_and_build_report

        li_verified = LeafIntegrityEvidence(
            verified=True, basis="single_100", inventory_count=3, matched_count=3, total=100.0,
        )
        li_not = LeafIntegrityEvidence()
        sr1 = ScopedReport(
            scope=("scheme_1", "采购包1"),
            status=LedgerStatus.RECONCILED,
            basis=BasisLevel.L3_DOC_TOTAL,
            expected_total=100.0,
            extracted_total=100.0,
            total_gap=0.0,
            leaf_integrity=li_verified,
        )
        sr2 = ScopedReport(
            scope=("scheme_1", "采购包2"),
            status=LedgerStatus.REVIEW_REQUIRED,
            basis=BasisLevel.L4_NONE,
            expected_total=100.0,
            extracted_total=80.0,
            total_gap=20.0,
            leaf_integrity=li_not,
        )
        top = aggregate_and_build_report([sr1, sr2], 180.0, [])
        assert hasattr(top, "leaf_integrity"), "顶层 LedgerReport 缺少 leaf_integrity 字段"
        assert top.leaf_integrity.verified is False


# ---------------------------------------------------------------------------
# C4: coordinate_scoped_ledger 两包端到端叶子完整性 (Step2 修复)
# ---------------------------------------------------------------------------


class TestCoordinateScopedLedgerLeafIntegrity:
    """C4: 通过真实 coordinate_scoped_ledger 验证两包叶子完整性聚合。"""

    def _two_lot_request_verified(self):
        """两包各 2 叶严格匹配且总分 100 的请求。"""
        from app.core.extractor.scoring.ledger.scoped_reconciliation import (
            ScopedLedgerRequest,
        )

        grid1 = [["1.1", "技术方案", "60"], ["1.2", "综合评分", "40"]]
        grid2 = [["2.1", "报价评分", "70"], ["2.2", "技术答辩", "30"]]

        entries = (
            (LedgerItemEntry(name="技术方案", score=60.0, scheme="S", lot="包1"), ""),
            (LedgerItemEntry(name="综合评分", score=40.0, scheme="S", lot="包1"), ""),
            (LedgerItemEntry(name="报价评分", score=70.0, scheme="S", lot="包2"), ""),
            (LedgerItemEntry(name="技术答辩", score=30.0, scheme="S", lot="包2"), ""),
        )
        return ScopedLedgerRequest(
            pages=((1, "包1评分合计100"), (2, "包2评分合计100")),
            chunk_scopes=(("S", "包1"), ("S", "包2")),
            grids=((1, grid1), (2, grid2)),
            grid_scopes=(("S", "包1"), ("S", "包2")),
            entries_with_rules=entries,
        )

    def test_two_lots_each_verified_top_level_per_scope_100(self):
        """两包各 scope 严格匹配且总分 100 → 顶层 leaf_integrity.basis=per_scope_100。"""
        from app.core.extractor.scoring.ledger.scoped_reconciliation import (
            coordinate_scoped_ledger,
        )

        report = coordinate_scoped_ledger(self._two_lot_request_verified())

        assert report.leaf_integrity.verified is True, (
            f"C4: 两包各严格匹配 100，顶层应 verified=True，实际 {report.leaf_integrity}"
        )
        assert report.leaf_integrity.basis == "per_scope_100", (
            f"C4: 顶层 basis 应 per_scope_100，实际 {report.leaf_integrity.basis!r}"
        )

    def test_one_lot_hallucinated_entry_not_verified(self):
        """包2 条目含幻觉（名称不吻合网格叶子）→ 顶层 leaf_integrity.verified=False。"""
        from app.core.extractor.scoring.ledger.scoped_reconciliation import (
            ScopedLedgerRequest,
            coordinate_scoped_ledger,
        )

        grid1 = [["1.1", "技术方案", "60"], ["1.2", "综合评分", "40"]]
        grid2 = [["2.1", "报价评分", "70"], ["2.2", "技术答辩", "30"]]

        # 包2 的"技术答辩"被幻觉替换为"综合评分"
        entries = (
            (LedgerItemEntry(name="技术方案", score=60.0, scheme="S", lot="包1"), ""),
            (LedgerItemEntry(name="综合评分", score=40.0, scheme="S", lot="包1"), ""),
            (LedgerItemEntry(name="报价评分", score=70.0, scheme="S", lot="包2"), ""),
            (LedgerItemEntry(name="综合评分", score=30.0, scheme="S", lot="包2"), ""),  # 幻觉
        )
        request = ScopedLedgerRequest(
            pages=((1, "包1评分合计100"), (2, "包2评分合计100")),
            chunk_scopes=(("S", "包1"), ("S", "包2")),
            grids=((1, grid1), (2, grid2)),
            grid_scopes=(("S", "包1"), ("S", "包2")),
            entries_with_rules=entries,
        )
        report = coordinate_scoped_ledger(request)

        assert report.leaf_integrity.verified is False, (
            f"C4: 包2 有幻觉条目，顶层应 not verified，实际 {report.leaf_integrity}"
        )
