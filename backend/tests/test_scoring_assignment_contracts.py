"""P2-2 TDD: assignment/contracts.py 契约定义测试（核心构造部分）。

职责: 验证 EvidenceAtom/Declaration/CandidateFact/Assignment 构造、字段完整性、不可变性。
不做: 不测试 LLM、数据库、API 路由、具体 producer 或任何采纳/裁决逻辑。
      InvariantReport/Serialization/PhysicalKey 测试见 test_scoring_assignment_contracts_status.py。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.scoring

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# EvidenceAtom 构造与不可变性
# ---------------------------------------------------------------------------


class TestEvidenceAtomConstruction:
    """EvidenceAtom 构造、字段完整性、不可变。"""

    def test_basic_construction(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import EvidenceAtom

        atom = EvidenceAtom(
            physical_key="d1:p3:table:pos0:abcd1234567890ab",
            raw_text="技术评分 49分",
            page=3,
            source_channel="table",
        )
        assert atom.physical_key == "d1:p3:table:pos0:abcd1234567890ab"
        assert atom.page == 3
        assert atom.source_channel == "table"
        assert atom.raw_text == "技术评分 49分"

    def test_optional_fields_exist_and_default(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import EvidenceAtom

        atom = EvidenceAtom(
            physical_key="d1:p3:text:pos0:abcd1234567890ab",
            raw_text="价格评分",
            page=3,
            source_channel="text",
        )
        assert atom.bbox is None
        assert atom.row_idx is None
        assert atom.col_idx is None
        assert atom.numeric_value is None
        assert atom.aliases == ()

    def test_all_optional_fields_assigned(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import EvidenceAtom

        atom = EvidenceAtom(
            physical_key="d1:p3:table:r2c1:abcd1234567890ab",
            raw_text="商务评分 21",
            page=3,
            source_channel="table",
            bbox=(10.0, 20.0, 100.0, 35.0),
            row_idx=2,
            col_idx=1,
            numeric_value=21.0,
            aliases=("d1:p3:text:pos0:abcd1234", "d1:p3:ocr:pos0:abcd5678"),
        )
        assert atom.numeric_value == 21.0
        assert atom.bbox == (10.0, 20.0, 100.0, 35.0)
        assert atom.row_idx == 2
        assert atom.col_idx == 1
        assert len(atom.aliases) == 2

    def test_frozen_immutable(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import EvidenceAtom

        atom = EvidenceAtom(
            physical_key="d1:p1:table:pos0:aabb",
            raw_text="test",
            page=1,
            source_channel="table",
        )
        with pytest.raises((AttributeError, TypeError)):
            atom.page = 5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Declaration 构造
# ---------------------------------------------------------------------------


class TestDeclarationConstruction:
    """Declaration 构造与字段完整性。"""

    def test_basic_construction(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import Declaration

        decl = Declaration(
            ref="d1:p2:decl:commercial",
            lot="__single__",
            scheme="__single__",
            volume="commercial",
            declared_total=21.0,
            authority="deterministic",
            evidence_atom_keys=("d1:p2:table:pos0:abcd1234",),
        )
        assert decl.declared_total == 21.0
        assert decl.volume == "commercial"
        assert decl.authority == "deterministic"
        assert len(decl.evidence_atom_keys) == 1

    def test_declared_by_volume_field_exists(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import Declaration

        decl = Declaration(
            ref="d1:p2:decl:total",
            lot="__single__",
            scheme="__single__",
            volume="unspecified",
            declared_total=100.0,
            declared_by_volume=(
                ("commercial", 21.0),
                ("technical", 49.0),
                ("price", 30.0),
            ),
            authority="deterministic",
            evidence_atom_keys=(),
        )
        assert len(decl.declared_by_volume) == 3
        volumes = [v for v, _ in decl.declared_by_volume]
        assert "commercial" in volumes
        assert "technical" in volumes
        assert "price" in volumes

    def test_frozen_immutable(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import Declaration

        decl = Declaration(
            ref="d1:p1:decl:total",
            lot="__single__",
            scheme="__single__",
            volume="unspecified",
            declared_total=100.0,
            authority="deterministic",
            evidence_atom_keys=(),
        )
        with pytest.raises((AttributeError, TypeError)):
            decl.declared_total = 50.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CandidateFact 构造与约束
# ---------------------------------------------------------------------------


class TestCandidateFactConstruction:
    """CandidateFact 构造、字段约束、fact_type 枚举。"""

    def test_basic_construction(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import CandidateFact

        fact = CandidateFact(
            candidate_key="cf:d1:abcdef123456",
            lot="__single__",
            scheme="__single__",
            volume="technical",
            fact_type="base_score",
            name="业绩评分",
            description="近三年业绩得分",
            score_value=5.0,
            evidence_atom_keys=("d1:p3:table:pos0:abcd1234",),
            source_channel="table",
            confidence=0.95,
            page_refs=(3,),
        )
        assert fact.fact_type == "base_score"
        assert fact.score_value == 5.0
        assert fact.confidence == 0.95

    def test_all_valid_fact_types(self) -> None:
        """VALID_FACT_TYPES 覆盖 P2/P3 计划要求的全部类型。"""
        from app.core.extractor.scoring.assignment.contracts import VALID_FACT_TYPES

        required = {"base_score", "price_formula", "group_total", "deduction", "bonus"}
        assert required.issubset(VALID_FACT_TYPES)

    def test_invalid_fact_type_rejected(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import CandidateFact

        with pytest.raises((ValueError, TypeError)):
            CandidateFact(
                candidate_key="cf:d1:xyz",
                lot="__single__",
                scheme="__single__",
                volume="technical",
                fact_type="invented_type",
                name="测试",
                description="",
                score_value=5.0,
                evidence_atom_keys=("d1:p1:table:pos0:xxxx",),
                source_channel="table",
            )

    def test_empty_evidence_atom_keys_rejected(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import CandidateFact

        with pytest.raises((ValueError, TypeError)):
            CandidateFact(
                candidate_key="cf:d1:xyz",
                lot="__single__",
                scheme="__single__",
                volume="technical",
                fact_type="base_score",
                name="测试",
                description="",
                score_value=5.0,
                evidence_atom_keys=(),
                source_channel="table",
            )

    def test_confidence_out_of_range_rejected(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import CandidateFact

        with pytest.raises((ValueError, TypeError)):
            CandidateFact(
                candidate_key="cf:d1:xyz",
                lot="__single__",
                scheme="__single__",
                volume="technical",
                fact_type="base_score",
                name="测试",
                description="",
                score_value=5.0,
                evidence_atom_keys=("d1:p1:table:pos0:xxxx",),
                source_channel="table",
                confidence=1.5,
            )

    def test_frozen_immutable(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import CandidateFact

        fact = CandidateFact(
            candidate_key="cf:d1:abc",
            lot="__single__",
            scheme="__single__",
            volume="technical",
            fact_type="base_score",
            name="测试",
            description="",
            score_value=5.0,
            evidence_atom_keys=("d1:p1:table:pos0:xxxx",),
            source_channel="table",
        )
        with pytest.raises((AttributeError, TypeError)):
            fact.score_value = 10.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Assignment 构造与状态约束
# ---------------------------------------------------------------------------


class TestAssignmentConstruction:
    """Assignment 构造、三态 status、mode 字段。"""

    def test_basic_construction(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import Assignment

        asgn = Assignment(
            assignment_id="asgn:d1:001",
            lot="__single__",
            scheme="__single__",
            assigned_fact_keys=("cf:d1:aaa", "cf:d1:bbb"),
            residual_atom_keys=(),
            conflict_atom_keys=(),
            status="PROVISIONAL",
            declaration_refs=("d1:p2:decl:commercial",),
            mode="kernel",
        )
        assert asgn.status == "PROVISIONAL"
        assert asgn.mode == "kernel"
        assert len(asgn.assigned_fact_keys) == 2

    def test_valid_statuses_set(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import VALID_ASSIGNMENT_STATUSES

        assert VALID_ASSIGNMENT_STATUSES == {"VERIFIED", "PROVISIONAL", "FAILED"}

    def test_invalid_status_rejected(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import Assignment

        with pytest.raises((ValueError, TypeError)):
            Assignment(
                assignment_id="asgn:d1:001",
                lot="__single__",
                scheme="__single__",
                assigned_fact_keys=(),
                residual_atom_keys=(),
                conflict_atom_keys=(),
                status="UNKNOWN",
            )

    def test_all_three_statuses_accepted(self) -> None:
        from app.core.extractor.scoring.assignment.contracts import Assignment

        for status in ("VERIFIED", "PROVISIONAL", "FAILED"):
            asgn = Assignment(
                assignment_id=f"asgn:d1:{status}",
                lot="__single__",
                scheme="__single__",
                assigned_fact_keys=(),
                residual_atom_keys=(),
                conflict_atom_keys=(),
                status=status,
            )
            assert asgn.status == status
