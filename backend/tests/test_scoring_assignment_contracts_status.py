"""P2-2 TDD: assignment/contracts.py 契约定义测试（状态/序列化/physical_key 部分）。

职责: 验证 InvariantReport 三态 status、所有契约类型 JSON 序列化、
      make_physical_key 确定性与跨通道去别名字段。
不做: 不测试 LLM、数据库、API 路由。
      EvidenceAtom/Declaration/CandidateFact/Assignment 构造测试见
      test_scoring_assignment_contracts.py。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

pytestmark = pytest.mark.scoring

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# InvariantReport 三态 status（核心 TDD 必须覆盖项）
# ---------------------------------------------------------------------------


class TestInvariantReportThreeState:
    """InvariantReport 三态 status 严格约束。"""

    def test_verified_status(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import InvariantReport

        report = InvariantReport(
            assignment_id="asgn:d1:001",
            status="VERIFIED",
            checks=(),
            violations=(),
            residual_atom_keys=(),
            conflict_atom_keys=(),
            note="all invariants pass",
        )
        assert report.status == "VERIFIED"
        assert report.is_verified
        assert not report.is_provisional
        assert not report.is_failed

    def test_provisional_status(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import InvariantReport

        report = InvariantReport(
            assignment_id="asgn:d1:001",
            status="PROVISIONAL",
            checks=(),
            violations=("evidence_gap:commercial",),
            residual_atom_keys=("d1:p5:text:pos0:abcd1234",),
            conflict_atom_keys=(),
            note="commercial volume gap 3.0",
        )
        assert report.status == "PROVISIONAL"
        assert report.is_provisional
        assert not report.is_verified
        assert not report.is_failed

    def test_failed_status(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import InvariantReport

        report = InvariantReport(
            assignment_id="asgn:d1:001",
            status="FAILED",
            checks=(),
            violations=("no_evidence",),
            residual_atom_keys=(),
            conflict_atom_keys=(),
            note="no evidence atoms found",
        )
        assert report.is_failed
        assert not report.is_verified

    def test_invalid_status_rejected(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import InvariantReport

        with pytest.raises((ValueError, TypeError)):
            InvariantReport(
                assignment_id="asgn:d1:001",
                status="UNKNOWN",
                checks=(),
                violations=(),
                residual_atom_keys=(),
                conflict_atom_keys=(),
                note="",
            )

    def test_verified_with_violations_rejected(self) -> None:
        """VERIFIED 状态下 violations 非空应被拒绝（硬约束）。"""
        from app.core.extractor.scoring.assignment.contracts import InvariantReport

        with pytest.raises((ValueError, TypeError)):
            InvariantReport(
                assignment_id="asgn:d1:001",
                status="VERIFIED",
                checks=(),
                violations=("some_violation",),
                residual_atom_keys=(),
                conflict_atom_keys=(),
                note="",
            )

    def test_verified_with_residuals_rejected(self) -> None:
        """VERIFIED 状态下 residual_atom_keys 非空应被拒绝（硬约束）。"""
        from app.core.extractor.scoring.assignment.contracts import InvariantReport

        with pytest.raises((ValueError, TypeError)):
            InvariantReport(
                assignment_id="asgn:d1:001",
                status="VERIFIED",
                checks=(),
                violations=(),
                residual_atom_keys=("d1:p1:table:pos0:xxxx",),
                conflict_atom_keys=(),
                note="",
            )

    def test_invariant_check_construction(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import InvariantCheck, InvariantReport

        checks = (
            InvariantCheck(check_name="evidence_coverage", passed=True, note="ok"),
            InvariantCheck(check_name="score_conservation", passed=False, note="gap 3.0"),
        )
        report = InvariantReport(
            assignment_id="asgn:d1:001",
            status="PROVISIONAL",
            checks=checks,
            violations=("score_conservation: gap 3.0",),
            residual_atom_keys=(),
            conflict_atom_keys=(),
            note="partial",
        )
        assert len(report.checks) == 2
        assert report.checks[0].passed
        assert not report.checks[1].passed


# ---------------------------------------------------------------------------
# 序列化（dataclass asdict + JSON round-trip）
# ---------------------------------------------------------------------------


class TestSerializability:
    """所有契约类型可通过 dataclasses.asdict 序列化为 dict，并通过 JSON round-trip。"""

    def test_evidence_atom_serializable(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import EvidenceAtom

        atom = EvidenceAtom(
            physical_key="d1:p3:table:pos0:abcd1234",
            raw_text="商务评分 21分",
            page=3,
            source_channel="table",
            numeric_value=21.0,
        )
        d = asdict(atom)
        loaded = json.loads(json.dumps(d))
        assert loaded["page"] == 3
        assert loaded["numeric_value"] == 21.0
        assert loaded["source_channel"] == "table"

    def test_declaration_serializable(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import Declaration

        decl = Declaration(
            ref="d1:p2:decl:total",
            lot="__single__",
            scheme="__single__",
            volume="unspecified",
            declared_total=100.0,
            authority="deterministic",
            evidence_atom_keys=(),
        )
        d = asdict(decl)
        loaded = json.loads(json.dumps(d))
        assert loaded["declared_total"] == 100.0

    def test_candidate_fact_serializable(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import CandidateFact

        fact = CandidateFact(
            candidate_key="cf:d1:abc",
            lot="__single__",
            scheme="__single__",
            volume="technical",
            fact_type="base_score",
            name="业绩",
            description="近3年",
            score_value=5.0,
            evidence_atom_keys=("d1:p3:table:pos0:abcd",),
            source_channel="table",
        )
        d = asdict(fact)
        loaded = json.loads(json.dumps(d))
        assert loaded["score_value"] == 5.0
        assert loaded["fact_type"] == "base_score"

    def test_invariant_report_serializable(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import InvariantReport

        report = InvariantReport(
            assignment_id="asgn:d1:001",
            status="VERIFIED",
            checks=(),
            violations=(),
            residual_atom_keys=(),
            conflict_atom_keys=(),
            note="ok",
        )
        d = asdict(report)
        loaded = json.loads(json.dumps(d))
        assert loaded["status"] == "VERIFIED"
        assert loaded["note"] == "ok"

    def test_assignment_serializable(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import Assignment

        asgn = Assignment(
            assignment_id="asgn:d1:001",
            lot="__single__",
            scheme="__single__",
            assigned_fact_keys=("cf:d1:aaa",),
            residual_atom_keys=(),
            conflict_atom_keys=(),
            status="FAILED",
            mode="legacy",
        )
        d = asdict(asgn)
        loaded = json.loads(json.dumps(d))
        assert loaded["status"] == "FAILED"
        assert loaded["mode"] == "legacy"


# ---------------------------------------------------------------------------
# physical_key 跨通道去别名字段（P2-2/P2-4 设计点）
# ---------------------------------------------------------------------------


class TestPhysicalKeyHelper:
    """make_physical_key 确定性、通道区分、EvidenceAtom.aliases 跨通道去别名。"""

    def test_make_physical_key_deterministic(self) -> None:
        """相同输入幂等产出相同 key。"""
        from app.core.extractor.scoring.assignment.contracts import make_physical_key

        key1 = make_physical_key(doc_id=1, page=3, source_channel="text", text="技术评分")
        key2 = make_physical_key(doc_id=1, page=3, source_channel="text", text="技术评分")
        assert key1 == key2

    def test_make_physical_key_different_channels_differ(self) -> None:
        """同页同文本但不同通道的 physical_key 不同（跨通道去别名前各自独立）。"""
        from app.core.extractor.scoring.assignment.contracts import make_physical_key

        key_table = make_physical_key(doc_id=1, page=3, source_channel="table", text="技术评分")
        key_text = make_physical_key(doc_id=1, page=3, source_channel="text", text="技术评分")
        assert key_table != key_text

    def test_make_physical_key_starts_with_doc_page(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import make_physical_key

        key = make_physical_key(doc_id=42, page=7, source_channel="table", text="价格")
        assert key.startswith("d42:p7:")

    def test_make_physical_key_with_bbox(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import make_physical_key

        key = make_physical_key(
            doc_id=1, page=3, source_channel="table",
            bbox=(10.0, 20.0, 100.0, 30.0), text="商务评分"
        )
        assert key.startswith("d1:p3:")
        key2 = make_physical_key(
            doc_id=1, page=3, source_channel="table",
            bbox=(11.0, 20.0, 100.0, 30.0), text="商务评分"
        )
        assert key != key2

    def test_make_physical_key_with_row_col(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import make_physical_key

        key = make_physical_key(
            doc_id=1, page=3, source_channel="table",
            row_idx=2, col_idx=1, text="49"
        )
        assert "r2" in key or key.startswith("d1:p3:")

    def test_evidence_atom_aliases_field_for_cross_channel(self) -> None:
        """EvidenceAtom.aliases 承载跨通道同源物理证据的别名键（P2-4 去别名声明位置）。"""
        from app.core.extractor.scoring.assignment.contracts import EvidenceAtom, make_physical_key

        key_table = make_physical_key(
            doc_id=1, page=3, source_channel="table",
            bbox=(10.0, 20.0, 100.0, 30.0), text="技术评分49分"
        )
        key_text = make_physical_key(
            doc_id=1, page=3, source_channel="text",
            text="技术评分49分"
        )
        key_ocr = make_physical_key(
            doc_id=1, page=3, source_channel="ocr",
            text="技术评分49分"
        )

        atom = EvidenceAtom(
            physical_key=key_table,
            raw_text="技术评分49分",
            page=3,
            source_channel="table",
            numeric_value=49.0,
            aliases=(key_text, key_ocr),
        )
        assert key_text in atom.aliases
        assert key_ocr in atom.aliases
        assert atom.physical_key == key_table
