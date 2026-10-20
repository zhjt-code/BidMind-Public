"""P3-3 不变量校验器单元测试（TDD 红→绿）。"""

from __future__ import annotations

import pytest

from app.core.extractor.scoring.assignment.contracts import (
    Assignment,
    CandidateFact,
    Declaration,
    EvidenceAtom,
    make_physical_key,
)
from app.core.extractor.scoring.assignment.invariants import (
    CHECK_NAMES,
    check_invariants,
)

pytestmark = pytest.mark.scoring


# ---------------------------------------------------------------------------
# 夹具工厂
# ---------------------------------------------------------------------------

def _atom(idx: int = 0, value: float | None = None) -> EvidenceAtom:
    return EvidenceAtom(
        physical_key=make_physical_key(1, 1, "table", row_idx=idx, col_idx=0),
        raw_text=f"项{idx}", page=1, source_channel="table", numeric_value=value,
    )


def _fact(key: str, lot: str = "L1", scheme: str = "S1", volume: str = "commercial",
          fact_type: str = "base_score", score: float = 10.0,
          atoms: tuple[str, ...] | None = None) -> CandidateFact:
    return CandidateFact(
        candidate_key=key, lot=lot, scheme=scheme, volume=volume, fact_type=fact_type,
        name=f"项-{key}", description="test", score_value=score,
        evidence_atom_keys=atoms if atoms is not None else (f"atm_{key}",),
        source_channel="table",
    )


def _asgn(fact_keys: tuple[str, ...] = (), residuals: tuple[str, ...] = (),
          conflicts: tuple[str, ...] = (), status: str = "VERIFIED",
          lot: str = "L1", scheme: str = "S1") -> Assignment:
    return Assignment(
        assignment_id="asgn:t:1", lot=lot, scheme=scheme,
        assigned_fact_keys=fact_keys, residual_atom_keys=residuals,
        conflict_atom_keys=conflicts, status=status,
    )


def _decl(lot: str = "L1", scheme: str = "S1", total: float | None = None,
          by_vol: tuple[tuple[str, float], ...] = (),
          authority: str = "deterministic") -> Declaration:
    return Declaration(
        ref=f"d1:p1:decl:{lot}:{scheme}", lot=lot, scheme=scheme,
        volume="unspecified", declared_total=total, declared_by_volume=by_vol,
        authority=authority,
    )


def _clean():
    a1, a2, a3 = _atom(0, 30.0), _atom(1, 20.0), _atom(2, 50.0)
    f1 = _fact("F1", score=30.0, atoms=(a1.physical_key,))
    f2 = _fact("F2", score=20.0, atoms=(a2.physical_key,))
    f3 = _fact("F3", score=50.0, atoms=(a3.physical_key,))
    fbk = {f.candidate_key: f for f in [f1, f2, f3]}
    abk = {a.physical_key: a for a in [a1, a2, a3]}
    return _asgn(fact_keys=("F1", "F2", "F3"), status="VERIFIED"), fbk, abk, [_decl(total=100.0)]


# ---------------------------------------------------------------------------
# 1. VERIFIED 黄金路径
# ---------------------------------------------------------------------------

class TestVerifiedPath:
    def test_clean_is_verified(self):
        r = check_invariants(*_clean())
        assert r.status == "VERIFIED" and r.is_verified
        assert r.violations == () and r.residual_atom_keys == ()
        assert all(c.passed for c in r.checks)

    def test_seven_checks_with_correct_names(self):
        r = check_invariants(*_clean())
        assert [c.check_name for c in r.checks] == list(CHECK_NAMES)
        assert len(r.checks) == 7

    def test_verified_note_human_readable(self):
        r = check_invariants(*_clean())
        assert r.note and ("通过" in r.note or "零" in r.note)

    def test_assignment_id_preserved(self):
        asgn, fbk, abk, decls = _clean()
        assert check_invariants(asgn, fbk, abk, decls).assignment_id == asgn.assignment_id


# ---------------------------------------------------------------------------
# 2. evidence_coverage — 硬失败 → FAILED
# ---------------------------------------------------------------------------

class TestEvidenceCoverage:
    def test_missing_fact_key_is_failed(self):
        asgn, fbk, abk, decls = _clean()
        del fbk["F1"]
        r = check_invariants(asgn, fbk, abk, decls)
        chk = next(c for c in r.checks if c.check_name == "evidence_coverage")
        assert not chk.passed and r.status == "FAILED" and "F1" in chk.note

    def test_atoms_absent_from_abk_is_failed(self):
        a1 = _atom(0)
        f1 = _fact("F1", score=10.0, atoms=("ghost",))
        r = check_invariants(
            _asgn(fact_keys=("F1",)), {"F1": f1}, {a1.physical_key: a1},
            [_decl(total=10.0)],
        )
        assert r.status == "FAILED"

    def test_partial_atoms_sufficient(self):
        """只要有一条 atom 在 abk 中即可通过。"""
        a1 = _atom(0)
        f1 = _fact("F1", score=10.0, atoms=("ghost", a1.physical_key))
        r = check_invariants(
            _asgn(fact_keys=("F1",)), {"F1": f1}, {a1.physical_key: a1}, [_decl(total=10.0)],
        )
        chk = next(c for c in r.checks if c.check_name == "evidence_coverage")
        assert chk.passed

    def test_empty_assignment_passes_coverage(self):
        r = check_invariants(_asgn(), {}, {}, [])
        assert next(c for c in r.checks if c.check_name == "evidence_coverage").passed


# ---------------------------------------------------------------------------
# 3. score_conservation — 软失败 → PROVISIONAL
# ---------------------------------------------------------------------------

class TestScoreConservation:
    def test_mismatch_is_provisional(self):
        a1, a2 = _atom(0), _atom(1)
        f1 = _fact("F1", score=30.0, atoms=(a1.physical_key,))
        f2 = _fact("F2", score=20.0, atoms=(a2.physical_key,))
        r = check_invariants(
            _asgn(fact_keys=("F1", "F2"), status="PROVISIONAL"),
            {"F1": f1, "F2": f2}, {a1.physical_key: a1, a2.physical_key: a2},
            [_decl(total=60.0)],  # 实际 50，差 10
        )
        chk = next(c for c in r.checks if c.check_name == "score_conservation")
        assert not chk.passed and r.status == "PROVISIONAL"
        assert "差值" in chk.note

    def test_no_declaration_skips(self):
        a1 = _atom(0)
        f1 = _fact("F1", score=30.0, atoms=(a1.physical_key,))
        r = check_invariants(_asgn(fact_keys=("F1",)), {"F1": f1}, {a1.physical_key: a1}, [])
        assert next(c for c in r.checks if c.check_name == "score_conservation").passed

    def test_within_tolerance_passes(self):
        a1 = _atom(0)
        f1 = _fact("F1", score=10.005, atoms=(a1.physical_key,))
        r = check_invariants(
            _asgn(fact_keys=("F1",)), {"F1": f1}, {a1.physical_key: a1}, [_decl(total=10.0)]
        )
        assert next(c for c in r.checks if c.check_name == "score_conservation").passed



# ---------------------------------------------------------------------------
# 4. unique_consumption — 软失败 → PROVISIONAL
# ---------------------------------------------------------------------------

class TestUniqueConsumption:
    def test_shared_atom_is_provisional(self):
        a1 = _atom(0)
        f1 = _fact("F1", score=30.0, atoms=(a1.physical_key,))
        f2 = _fact("F2", score=30.0, atoms=(a1.physical_key,))
        r = check_invariants(
            _asgn(fact_keys=("F1", "F2"), status="PROVISIONAL"),
            {"F1": f1, "F2": f2}, {a1.physical_key: a1}, [],
        )
        chk = next(c for c in r.checks if c.check_name == "unique_consumption")
        assert not chk.passed and r.status == "PROVISIONAL" and "2" in chk.note

    def test_disjoint_atoms_passes(self):
        a1, a2 = _atom(0), _atom(1)
        f1 = _fact("F1", score=30.0, atoms=(a1.physical_key,))
        f2 = _fact("F2", score=20.0, atoms=(a2.physical_key,))
        r = check_invariants(
            _asgn(fact_keys=("F1", "F2")),
            {"F1": f1, "F2": f2}, {a1.physical_key: a1, a2.physical_key: a2},
            [_decl(total=50.0)],
        )
        assert next(c for c in r.checks if c.check_name == "unique_consumption").passed


# ---------------------------------------------------------------------------
# 5. scope_isolation — 硬失败 → FAILED
# ---------------------------------------------------------------------------

class TestScopeIsolation:
    def test_wrong_lot_is_failed(self):
        a1 = _atom(0)
        f1 = _fact("F1", lot="L2", score=10.0, atoms=(a1.physical_key,))
        r = check_invariants(
            _asgn(fact_keys=("F1",), lot="L1", scheme="S1", status="PROVISIONAL"),
            {"F1": f1}, {a1.physical_key: a1}, [],
        )
        chk = next(c for c in r.checks if c.check_name == "scope_isolation")
        assert not chk.passed and r.status == "FAILED"

    def test_wrong_scheme_is_failed(self):
        a1 = _atom(0)
        f1 = _fact("F1", lot="L1", scheme="S2", score=10.0, atoms=(a1.physical_key,))
        r = check_invariants(
            _asgn(fact_keys=("F1",), lot="L1", scheme="S1", status="PROVISIONAL"),
            {"F1": f1}, {a1.physical_key: a1}, [],
        )
        assert r.status == "FAILED"

    def test_correct_scope_passes(self):
        a1 = _atom(0)
        f1 = _fact("F1", lot="L1", scheme="S1", score=10.0, atoms=(a1.physical_key,))
        r = check_invariants(
            _asgn(fact_keys=("F1",), lot="L1", scheme="S1"),
            {"F1": f1}, {a1.physical_key: a1}, [_decl(total=10.0)],
        )
        assert next(c for c in r.checks if c.check_name == "scope_isolation").passed


# ---------------------------------------------------------------------------
# 6. hierarchy_conservation — 软失败 → PROVISIONAL
# ---------------------------------------------------------------------------

class TestHierarchyConservation:
    def test_group_and_leaf_share_atom_is_provisional(self):
        shared = _atom(0)
        fg = _fact("FG", fact_type="group_total", score=100.0, atoms=(shared.physical_key,))
        fl = _fact("FL", fact_type="base_score", score=40.0, atoms=(shared.physical_key,))
        r = check_invariants(
            _asgn(fact_keys=("FG", "FL"), status="PROVISIONAL"),
            {"FG": fg, "FL": fl}, {shared.physical_key: shared}, [],
        )
        chk = next(c for c in r.checks if c.check_name == "hierarchy_conservation")
        assert not chk.passed and r.status == "PROVISIONAL"
        assert shared.physical_key in chk.note

    def test_distinct_atoms_passes(self):
        a1, a2 = _atom(0), _atom(1)
        fg = _fact("FG", fact_type="group_total", score=100.0, atoms=(a1.physical_key,))
        fl = _fact("FL", fact_type="base_score", score=40.0, atoms=(a2.physical_key,))
        r = check_invariants(
            _asgn(fact_keys=("FG", "FL")),
            {"FG": fg, "FL": fl}, {a1.physical_key: a1, a2.physical_key: a2}, [],
        )
        assert next(c for c in r.checks if c.check_name == "hierarchy_conservation").passed


# ---------------------------------------------------------------------------
# 7. conditional_closure — 软失败 → PROVISIONAL
# ---------------------------------------------------------------------------

class TestConditionalClosure:
    def test_volume_mismatch_is_provisional(self):
        a1, a2 = _atom(0), _atom(1)
        f1 = _fact("F1", volume="commercial", score=30.0, atoms=(a1.physical_key,))
        f2 = _fact("F2", volume="technical", score=20.0, atoms=(a2.physical_key,))
        # 商务卷声明 40，实际 30
        r = check_invariants(
            _asgn(fact_keys=("F1", "F2"), status="PROVISIONAL"),
            {"F1": f1, "F2": f2}, {a1.physical_key: a1, a2.physical_key: a2},
            [_decl(by_vol=(("commercial", 40.0), ("technical", 20.0)))],
        )
        chk = next(c for c in r.checks if c.check_name == "conditional_closure")
        assert not chk.passed and r.status == "PROVISIONAL"
        assert "commercial" in chk.note

    def test_no_volume_breakdown_skips(self):
        a1 = _atom(0)
        f1 = _fact("F1", score=30.0, atoms=(a1.physical_key,))
        r = check_invariants(
            _asgn(fact_keys=("F1",)), {"F1": f1}, {a1.physical_key: a1},
            [_decl(total=30.0)],  # 无 declared_by_volume
        )
        assert next(c for c in r.checks if c.check_name == "conditional_closure").passed

    def test_llm_assisted_skips_closure(self):
        a1 = _atom(0)
        f1 = _fact("F1", volume="commercial", score=30.0, atoms=(a1.physical_key,))
        r = check_invariants(
            _asgn(fact_keys=("F1",), status="PROVISIONAL"),
            {"F1": f1}, {a1.physical_key: a1},
            [_decl(by_vol=(("commercial", 50.0),), authority="llm_assisted")],
        )
        assert next(c for c in r.checks if c.check_name == "conditional_closure").passed


# ---------------------------------------------------------------------------
# 8. substantial_uniqueness — 软失败 → PROVISIONAL
# ---------------------------------------------------------------------------

class TestSubstantialUniqueness:
    def test_multiple_solutions_marker(self):
        a1 = _atom(0)
        f1 = _fact("F1", score=10.0, atoms=(a1.physical_key,))
        r = check_invariants(
            _asgn(fact_keys=("F1",), conflicts=("multiple_solutions",), status="PROVISIONAL"),
            {"F1": f1}, {a1.physical_key: a1}, [],
        )
        chk = next(c for c in r.checks if c.check_name == "substantial_uniqueness")
        assert not chk.passed and r.status == "PROVISIONAL"
        assert chk.note and ("唯一" in chk.note or "多" in chk.note)

    def test_no_marker_passes(self):
        a1 = _atom(0)
        f1 = _fact("F1", score=10.0, atoms=(a1.physical_key,))
        r = check_invariants(
            _asgn(fact_keys=("F1",)), {"F1": f1}, {a1.physical_key: a1}, [_decl(total=10.0)]
        )
        assert next(c for c in r.checks if c.check_name == "substantial_uniqueness").passed


# ---------------------------------------------------------------------------
# 9. 残差/冲突汇总与 FAILED 传播
# ---------------------------------------------------------------------------

class TestResidualConflictSummary:
    def test_residuals_propagated(self):
        a1, a2 = _atom(0), _atom(1)
        f1 = _fact("F1", score=10.0, atoms=(a1.physical_key,))
        r = check_invariants(
            _asgn(fact_keys=("F1",), residuals=(a2.physical_key,), status="PROVISIONAL"),
            {"F1": f1}, {a1.physical_key: a1, a2.physical_key: a2},
            [_decl(total=10.0)],
        )
        assert r.status == "PROVISIONAL" and a2.physical_key in r.residual_atom_keys

    def test_kernel_failed_stays_failed(self):
        r = check_invariants(
            _asgn(conflicts=("no_solution",), status="FAILED"), {}, {}, []
        )
        assert r.status == "FAILED" and r.is_failed

    def test_conflict_keys_in_provisional(self):
        a1 = _atom(0)
        f1 = _fact("F1", score=10.0, atoms=(a1.physical_key,))
        r = check_invariants(
            _asgn(fact_keys=("F1",), conflicts=("multiple_solutions",), status="PROVISIONAL"),
            {"F1": f1}, {a1.physical_key: a1}, [],
        )
        assert "multiple_solutions" in r.conflict_atom_keys


# ---------------------------------------------------------------------------
# 10. violations 与 note 的可读性（PROVISIONAL/FAILED）
# ---------------------------------------------------------------------------

class TestViolationsAndNote:
    def test_violations_contain_check_names(self):
        a1 = _atom(0)
        f1 = _fact("F1", score=30.0, atoms=(a1.physical_key,))
        r = check_invariants(
            _asgn(fact_keys=("F1",), status="PROVISIONAL"),
            {"F1": f1}, {a1.physical_key: a1},
            [_decl(total=100.0)],  # 差 70 → 守恒失败
        )
        assert r.status == "PROVISIONAL"
        assert any("score_conservation" in v for v in r.violations)

    def test_failed_violations_contain_check_name(self):
        a1 = _atom(0)
        f1 = _fact("F1", lot="WRONG", score=10.0, atoms=(a1.physical_key,))
        r = check_invariants(
            _asgn(fact_keys=("F1",), lot="L1", scheme="S1", status="PROVISIONAL"),
            {"F1": f1}, {a1.physical_key: a1}, [],
        )
        assert r.status == "FAILED"
        assert any("scope_isolation" in v for v in r.violations)

    def test_provisional_note_non_empty(self):
        a1 = _atom(0)
        f1 = _fact("F1", score=30.0, atoms=(a1.physical_key,))
        r = check_invariants(
            _asgn(fact_keys=("F1",), residuals=("orphan",), status="PROVISIONAL"),
            {"F1": f1}, {a1.physical_key: a1}, [_decl(total=50.0)],
        )
        assert r.note and len(r.note) > 5


# ---------------------------------------------------------------------------
# 11. InvariantReport 契约：VERIFIED 时强制空
# ---------------------------------------------------------------------------

class TestReportContract:
    def test_verified_violations_and_residuals_empty(self):
        r = check_invariants(*_clean())
        assert r.status == "VERIFIED"
        assert r.violations == () and r.residual_atom_keys == ()

    def test_check_names_match_module_constant(self):
        r = check_invariants(*_clean())
        assert [c.check_name for c in r.checks] == list(CHECK_NAMES)
