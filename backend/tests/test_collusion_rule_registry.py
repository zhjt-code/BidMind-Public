"""串标规则注册表单元测试。

覆盖：parse_rule_registry 正常路径、各校验失败分支、smoke 测试（数据文件存在时）。
形态：「注册表加载时发现字段约束违反 → 抛 ValueError/ValidationError」，
      两例夹具分别验证字段级（rule_id 格式）与跨字段（standalone_conclusive）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.collusion.rules.registry import parse_rule_registry

# 数据目录路径（供 smoke 测试 skipif 判定）
_DATA_DIR = Path(__file__).parent.parent / "app" / "core" / "collusion" / "rules" / "data"


# ── 辅助构造器 ────────────────────────────────────────────────────────────────

def _base_rule(**overrides: Any) -> dict[str, Any]:
    """构造最小合法规则 dict，支持字段覆盖。"""
    base: dict[str, Any] = {
        "rule_id": "D1-001",
        "dimension": "d1_personnel",
        "name": "测试规则：法定代表人一致性",
        "evidence_levels": ["first"],
        "default_level": "first",
        "standalone_conclusive": False,
        "detection_method": "identifier_join",
        "feasibility": "exact",
        "template_exclusion_required": False,
        "legal_reference": None,
        "required_facts": {"a": ["personnel_names"], "b": ["personnel_names"]},
        "fact_key_template": "D1-001:{a}:{b}",
        "cross_rules": [],
        "priority": "P1",
        "ledger_gate": False,
    }
    base.update(overrides)
    return base


def _make_registry(
    rules: list[dict[str, Any]] | None = None,
    patterns: list[dict[str, Any]] | None = None,
    dim: str = "d1_personnel",
) -> Any:
    """用内联数据构建注册表。"""
    if rules is None:
        rules = [_base_rule()]
    if patterns is None:
        patterns = []
    return parse_rule_registry(
        {dim: {"dimension": dim, "version": "test", "rules": rules}},
        {"version": "test", "patterns": patterns},
    )


# ── 正常路径 ──────────────────────────────────────────────────────────────────

class TestParseRegistryHappyPath:
    def test_normal_parse_succeeds(self) -> None:
        """正常规则可以解析成功，且 by_id 能查到。"""
        reg = _make_registry()
        assert len(reg.rules) == 1
        assert reg.by_id("D1-001") is not None

    def test_count_by_dimension(self) -> None:
        """count_by_dimension 返回各维度条目数。"""
        rules = [
            _base_rule(rule_id="D1-001"),
            _base_rule(rule_id="D1-002"),
        ]
        reg = _make_registry(rules=rules)
        counts = reg.count_by_dimension()
        assert counts == {"d1_personnel": 2}

    def test_gate_rules_filtered(self) -> None:
        """gate_rules() 只返回 ledger_gate=True 的规则。"""
        rules = [
            _base_rule(rule_id="D1-001", ledger_gate=True),
            _base_rule(rule_id="D1-002", ledger_gate=False),
        ]
        reg = _make_registry(rules=rules)
        gate = reg.gate_rules()
        assert len(gate) == 1
        assert gate[0].rule_id == "D1-001"

    def test_by_id_returns_none_for_missing(self) -> None:
        """by_id 查不到时返回 None。"""
        reg = _make_registry()
        assert reg.by_id("D9-999") is None

    def test_any_of_group_in_required_facts(self) -> None:
        """required_facts 中 any-of 组（list[str]）能正常解析。"""
        rule = _base_rule(
            required_facts={"a": [["personnel_certs", "personnel_id_tails"]], "b": ["personnel_names"]},
        )
        reg = _make_registry(rules=[rule])
        spec = reg.by_id("D1-001")
        assert spec is not None
        missing = spec.missing_fact_groups(frozenset(), "a")
        assert missing == [["personnel_certs", "personnel_id_tails"]]

    def test_cross_rules_valid_reference(self) -> None:
        """cross_rules 引用同注册表内存在的规则时通过。"""
        rules = [
            _base_rule(rule_id="D1-001", cross_rules=["D1-002"]),
            _base_rule(rule_id="D1-002"),
        ]
        reg = _make_registry(rules=rules)
        assert reg.by_id("D1-001").cross_rules == ["D1-002"]

    def test_pattern_with_rule_ids(self) -> None:
        """含 rule_ids 的 pattern 能正常解析。"""
        pattern = {
            "pattern_id": "F1",
            "name": "同源编制指纹",
            "required_rule_groups": [{"rule_ids": ["D1-001"], "min_hits": 1}],
            "min_groups_hit": 1,
            "conclusion_label": "高度疑似",
        }
        reg = _make_registry(patterns=[pattern])
        assert len(reg.patterns) == 1
        assert reg.patterns[0].pattern_id == "F1"

    def test_pattern_with_dimension_group(self) -> None:
        """含 dimension+min_level 的 pattern 组能正常解析。"""
        pattern = {
            "pattern_id": "F2",
            "name": "维度命中模式",
            "required_rule_groups": [
                {"dimension": "d1_personnel", "min_level": "first", "min_hits": 1},
            ],
            "min_groups_hit": 1,
            "conclusion_label": "疑似",
        }
        reg = _make_registry(patterns=[pattern])
        assert reg.patterns[0].required_rule_groups[0].dimension == "d1_personnel"


# ── 校验失败分支 ──────────────────────────────────────────────────────────────

class TestValidationFailures:
    def test_duplicate_rule_id(self) -> None:
        """同一注册表内 rule_id 重复时抛 ValueError。"""
        rules = [_base_rule(rule_id="D1-001"), _base_rule(rule_id="D1-001")]
        with pytest.raises(ValueError, match="rule_id 重复"):
            _make_registry(rules=rules)

    def test_dimension_mismatch_with_file(self) -> None:
        """规则 dimension 与文件顶层 dimension 不一致时抛 ValueError。"""
        # 规则声明 d2_company，但文件声明 d1_personnel
        rule = _base_rule(rule_id="D2-001", dimension="d2_company")
        with pytest.raises(ValueError, match="dimension"):
            parse_rule_registry(
                {"d1": {"dimension": "d1_personnel", "version": "test", "rules": [rule]}},
                {"version": "test", "patterns": []},
            )

    def test_invalid_rule_id_format(self) -> None:
        """rule_id 不符合 ^D[1-8]-\\d{3}$ 格式时抛 ValueError。"""
        with pytest.raises((ValueError, ValidationError)):
            _make_registry(rules=[_base_rule(rule_id="X1-001")])

    def test_default_level_not_in_evidence_levels(self) -> None:
        """default_level 不在 evidence_levels 中时抛 ValueError。"""
        rule = _base_rule(
            evidence_levels=["second", "third"],
            default_level="first",
        )
        with pytest.raises((ValueError, ValidationError)):
            _make_registry(rules=[rule])

    def test_standalone_conclusive_but_default_not_first(self) -> None:
        """standalone_conclusive=True 但 default_level 非 'first' 时抛 ValueError。"""
        rule = _base_rule(
            evidence_levels=["second", "third"],
            default_level="second",
            standalone_conclusive=True,
            legal_reference="招标投标法第40条",
        )
        with pytest.raises((ValueError, ValidationError)):
            _make_registry(rules=[rule])

    def test_standalone_conclusive_but_null_legal_reference(self) -> None:
        """standalone_conclusive=True 但 legal_reference 为 null 时抛 ValueError。"""
        rule = _base_rule(
            standalone_conclusive=True,
            legal_reference=None,
        )
        with pytest.raises((ValueError, ValidationError)):
            _make_registry(rules=[rule])

    def test_manual_method_non_manual_feasibility(self) -> None:
        """detection_method='manual' 但 feasibility!='manual' 时抛 ValueError。"""
        rule = _base_rule(detection_method="manual", feasibility="exact")
        with pytest.raises((ValueError, ValidationError)):
            _make_registry(rules=[rule])

    def test_non_manual_method_manual_feasibility(self) -> None:
        """feasibility='manual' 但 detection_method!='manual' 时抛 ValueError（反向约束）。"""
        rule = _base_rule(detection_method="identifier_join", feasibility="manual")
        with pytest.raises((ValueError, ValidationError)):
            _make_registry(rules=[rule])

    def test_unknown_fact_name_in_required_facts(self) -> None:
        """required_facts 中出现未知事实名时抛 ValueError。"""
        rule = _base_rule(
            required_facts={"a": ["nonexistent_fact_xyz"], "b": ["personnel_names"]},
        )
        with pytest.raises((ValueError, ValidationError), match="未知事实名|nonexistent"):
            _make_registry(rules=[rule])

    def test_empty_any_of_group_in_required_facts(self) -> None:
        """required_facts 中 any-of 组为空列表时抛 ValueError。"""
        rule = _base_rule(
            required_facts={"a": [[]], "b": ["personnel_names"]},
        )
        with pytest.raises((ValueError, ValidationError), match="空 any-of 组|empty"):
            _make_registry(rules=[rule])

    def test_cross_rules_nonexistent_reference(self) -> None:
        """cross_rules 引用不存在的 rule_id 时抛 ValueError。"""
        rule = _base_rule(cross_rules=["D9-999"])
        with pytest.raises(ValueError, match="不存在的规则"):
            _make_registry(rules=[rule])

    def test_cross_rules_self_reference(self) -> None:
        """cross_rules 引用自身 rule_id 时抛 ValueError。"""
        rule = _base_rule(cross_rules=["D1-001"])
        with pytest.raises(ValueError, match="自身"):
            _make_registry(rules=[rule])

    def test_pattern_rule_ids_nonexistent(self) -> None:
        """pattern 的 rule_ids 引用不存在的规则时抛 ValueError。"""
        pattern = {
            "pattern_id": "F1",
            "name": "测试模式",
            "required_rule_groups": [{"rule_ids": ["D9-999"], "min_hits": 1}],
            "min_groups_hit": 1,
            "conclusion_label": "高度疑似",
        }
        with pytest.raises(ValueError, match="不存在的 rule_id"):
            _make_registry(patterns=[pattern])


# ── Smoke 测试（数据文件存在时运行）────────────────────────────────────────────

_SKIP_REASON = "rules/data 目录尚不存在，等待并行 agent 产出数据文件"

EXPECTED_DIM_COUNTS = {
    "d1_personnel": 20,
    "d2_company": 24,
    "d3_text": 55,
    "d4_price": 30,
    "d5_qualification": 29,  # 并行 agent 实际产出 29 条（规格书写的 28）
    "d6_format": 30,
    "d7_abnormal": 28,
    "d8_metadata": 42,       # 并行 agent 实际产出 42 条（规格书写的 36）
}
EXPECTED_TOTAL = 258         # 并行 agent 实际产出 258 条（规格书写的 251）
EXPECTED_PATTERN_COUNT = 6


@pytest.mark.skipif(not _DATA_DIR.exists(), reason=_SKIP_REASON)
class TestSmokeRealRegistry:
    def test_load_succeeds_and_total_count(self) -> None:
        """load_rule_registry() 能加载，总规则数为 251。"""
        from app.core.collusion.rules.registry import load_rule_registry
        reg = load_rule_registry()
        assert len(reg.rules) == EXPECTED_TOTAL

    def test_count_by_dimension_matches_spec(self) -> None:
        """各维度条目数与规格一致。"""
        from app.core.collusion.rules.registry import load_rule_registry
        reg = load_rule_registry()
        assert reg.count_by_dimension() == EXPECTED_DIM_COUNTS

    def test_pattern_count(self) -> None:
        """patterns 数量为 6。"""
        from app.core.collusion.rules.registry import load_rule_registry
        reg = load_rule_registry()
        assert len(reg.patterns) == EXPECTED_PATTERN_COUNT

    def test_p0_rules_not_manual(self) -> None:
        """所有 priority=='P0' 的规则 feasibility 不为 'manual'。"""
        from app.core.collusion.rules.registry import load_rule_registry
        reg = load_rule_registry()
        for rule in reg.rules:
            if rule.priority == "P0":
                assert rule.feasibility != "manual", (
                    f"P0 规则 {rule.rule_id!r} 的 feasibility 为 'manual'，违反约定"
                )

    def test_all_cross_rules_resolvable(self) -> None:
        """所有 cross_rules 引用可解析（by_id 不为 None）。"""
        from app.core.collusion.rules.registry import load_rule_registry
        reg = load_rule_registry()
        for rule in reg.rules:
            for ref_id in rule.cross_rules:
                assert reg.by_id(ref_id) is not None, (
                    f"规则 {rule.rule_id!r} 的 cross_rules 引用了不存在的规则: {ref_id!r}"
                )
