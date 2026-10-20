"""串标情形账本单元测试。

覆盖：build_document_facts、build_pair_scenario_ledger、summarize_ledger_gate。

形态：「某侧事实字段缺失 → gate 规则变为 not_checkable → summarize_ledger_gate 阻断」。
四个夹具实体不同（legal_representative / personnel_certs+id_tails / complete / asset_hashes）
均由同一机制（missing_fact_groups → _build_entry → ledger entry 状态）覆盖，
符合泛化护栏要求。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.core.collusion.rules.facts import DocumentFacts, build_document_facts
from app.core.collusion.rules.ledger import (
    ScenarioLedger,
    build_pair_scenario_ledger,
    summarize_ledger_gate,
)
from app.core.collusion.rules.registry import RuleRegistry, parse_rule_registry

# ── 固件数据路径 ──────────────────────────────────────────────────────────────
_FIXTURES_PATH = (
    Path(__file__).parent / "fixtures" / "collusion_rules" / "scenario_extractions.json"
)


def _load_fixture(name: str) -> dict[str, Any]:
    """加载场景提取固件（a/b 双侧）。"""
    with _FIXTURES_PATH.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    return data[name]


# ── 内联规则注册表 ──────────────────────────────────────────────────────────────

def _base_rule(**overrides: Any) -> dict[str, Any]:
    """构造最小合法规则 dict。"""
    base: dict[str, Any] = {
        "rule_id": "D1-001",
        "dimension": "d1_personnel",
        "name": "占位",
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


# 四条内联规则：
# D1-001：gate 规则，依赖 legal_representative（测夹具 1：legal_rep_missing）
# D1-002：gate 规则，依赖 any-of [personnel_certs, personnel_id_tails]（测夹具 2：cert_missing）
# D1-003：非 gate 规则，依赖 personnel_names（普通规则对照）
# D1-004：gate 规则，依赖 asset_hashes（collectable=False），应为 unimplemented 且不阻断

_INLINE_RULES = [
    _base_rule(
        rule_id="D1-001",
        name="法定代表人一致性核查",
        required_facts={"a": ["legal_representative"], "b": ["legal_representative"]},
        ledger_gate=True,
    ),
    _base_rule(
        rule_id="D1-002",
        name="人员证书或身份证核查",
        required_facts={
            "a": [["personnel_certs", "personnel_id_tails"]],
            "b": [["personnel_certs", "personnel_id_tails"]],
        },
        ledger_gate=True,
    ),
    _base_rule(
        rule_id="D1-003",
        name="人员姓名一致性核查",
        required_facts={"a": ["personnel_names"], "b": ["personnel_names"]},
        ledger_gate=False,
    ),
    _base_rule(
        rule_id="D1-004",
        name="附件哈希一致性核查",
        required_facts={"a": ["asset_hashes"], "b": ["asset_hashes"]},
        ledger_gate=True,
    ),
]

_REGISTRY: RuleRegistry = parse_rule_registry(
    {"d1_personnel": {"dimension": "d1_personnel", "version": "test", "rules": _INLINE_RULES}},
    {"version": "test", "patterns": []},
)


def _make_facts(doc_id: int, doc_name: str, extraction: dict) -> DocumentFacts:
    """构建 DocumentFacts（has_text_chunks=False，避免 text_chunks 干扰）。"""
    return build_document_facts(doc_id, doc_name, extraction, has_text_chunks=False)


# ── 夹具 1：legal_rep_missing ─────────────────────────────────────────────────

class TestLegalRepMissing:
    """形态：A 侧公司信息缺少法定代表人 → gate 规则 not_checkable → 阻断。"""

    def setup_method(self) -> None:
        fixture = _load_fixture("legal_rep_missing")
        self.facts_a = _make_facts(1, "投标A.pdf", fixture["a"])
        self.facts_b = _make_facts(2, "投标B.pdf", fixture["b"])
        self.ledger: ScenarioLedger = build_pair_scenario_ledger(
            _REGISTRY, self.facts_a, self.facts_b
        )

    def test_a_lacks_legal_representative(self) -> None:
        """A 的 present_facts 不含 legal_representative。"""
        assert "legal_representative" not in self.facts_a.present_facts

    def test_b_has_legal_representative(self) -> None:
        """B 的 present_facts 含 legal_representative。"""
        assert "legal_representative" in self.facts_b.present_facts

    def test_d1_001_is_not_checkable(self) -> None:
        """D1-001（依赖 legal_representative）状态为 not_checkable。"""
        entry = next(e for e in self.ledger.entries if e.rule_id == "D1-001")
        assert entry.status == "not_checkable"
        assert ("legal_representative",) in entry.missing_a

    def test_summarize_gate_blocked(self) -> None:
        """summarize_ledger_gate 返回 blocked=True。"""
        result = summarize_ledger_gate(self.ledger, _REGISTRY)
        assert result["blocked"] is True
        assert "D1-001" in result["blocked_rule_ids"]

    def test_missing_fact_labels_contains_legal_rep(self) -> None:
        """missing_fact_labels 包含「法定代表人」。"""
        result = summarize_ledger_gate(self.ledger, _REGISTRY)
        assert "法定代表人" in result["missing_fact_labels"]


# ── 夹具 2：cert_missing（同形态第二例，实体不同）────────────────────────────

class TestCertMissing:
    """形态：A 侧人员缺少证书编号与身份证 → any-of gate 规则 not_checkable → 阻断。"""

    def setup_method(self) -> None:
        fixture = _load_fixture("cert_missing")
        self.facts_a = _make_facts(3, "投标C.pdf", fixture["a"])
        self.facts_b = _make_facts(4, "投标D.pdf", fixture["b"])
        self.ledger: ScenarioLedger = build_pair_scenario_ledger(
            _REGISTRY, self.facts_a, self.facts_b
        )

    def test_a_lacks_personnel_certs_and_id_tails(self) -> None:
        """A 的 present_facts 不含 personnel_certs 且不含 personnel_id_tails。"""
        assert "personnel_certs" not in self.facts_a.present_facts
        assert "personnel_id_tails" not in self.facts_a.present_facts

    def test_b_has_certs_and_id_tails(self) -> None:
        """B 的 present_facts 含 personnel_certs 和 personnel_id_tails。"""
        assert "personnel_certs" in self.facts_b.present_facts
        assert "personnel_id_tails" in self.facts_b.present_facts

    def test_d1_002_is_not_checkable(self) -> None:
        """D1-002（依赖 any-of 组）状态为 not_checkable，missing_a 含完整 any-of 组。"""
        entry = next(e for e in self.ledger.entries if e.rule_id == "D1-002")
        assert entry.status == "not_checkable"
        # any-of 组中两个事实均应出现在缺失组内
        flat_missing_a = {f for g in entry.missing_a for f in g}
        assert "personnel_certs" in flat_missing_a
        assert "personnel_id_tails" in flat_missing_a

    def test_summarize_gate_blocked(self) -> None:
        """summarize_ledger_gate 返回 blocked=True。"""
        result = summarize_ledger_gate(self.ledger, _REGISTRY)
        assert result["blocked"] is True
        assert "D1-002" in result["blocked_rule_ids"]

    def test_missing_fact_labels_contains_both(self) -> None:
        """missing_fact_labels 同时包含「人员证书编号」和「人员身份证尾号」。"""
        result = summarize_ledger_gate(self.ledger, _REGISTRY)
        labels = result["missing_fact_labels"]
        assert "人员证书编号" in labels
        assert "人员身份证尾号" in labels


# ── 夹具 3：complete（全字段齐备）────────────────────────────────────────────

class TestComplete:
    """形态：双侧字段齐备 → 可采集 gate 规则均 checkable → 不阻断。"""

    def setup_method(self) -> None:
        fixture = _load_fixture("complete")
        self.facts_a = _make_facts(5, "投标E.pdf", fixture["a"])
        self.facts_b = _make_facts(6, "投标F.pdf", fixture["b"])
        self.ledger: ScenarioLedger = build_pair_scenario_ledger(
            _REGISTRY, self.facts_a, self.facts_b
        )

    def test_a_has_key_facts(self) -> None:
        """A 含 legal_representative、personnel_certs、personnel_id_tails。"""
        assert "legal_representative" in self.facts_a.present_facts
        assert "personnel_certs" in self.facts_a.present_facts
        assert "personnel_id_tails" in self.facts_a.present_facts

    def test_d1_001_checkable(self) -> None:
        """D1-001（法定代表人）状态为 checkable。"""
        entry = next(e for e in self.ledger.entries if e.rule_id == "D1-001")
        assert entry.status == "checkable"

    def test_d1_002_checkable(self) -> None:
        """D1-002（证书 any-of 组）状态为 checkable。"""
        entry = next(e for e in self.ledger.entries if e.rule_id == "D1-002")
        assert entry.status == "checkable"

    def test_summarize_gate_not_blocked(self) -> None:
        """字段齐备时 summarize_ledger_gate 返回 blocked=False。"""
        result = summarize_ledger_gate(self.ledger, _REGISTRY)
        assert result["blocked"] is False
        assert result["blocked_rule_ids"] == []


# ── 夹具 4：non-collectable 事实（unimplemented 不阻断）──────────────────────

class TestNonCollectableUnimplemented:
    """形态：gate 规则依赖 non-collectable 事实 → unimplemented → 不阻断。"""

    def setup_method(self) -> None:
        # 使用 complete fixture（双侧字段充足），测试 D1-004 的 unimplemented 行为
        fixture = _load_fixture("complete")
        self.facts_a = _make_facts(7, "投标G.pdf", fixture["a"])
        self.facts_b = _make_facts(8, "投标H.pdf", fixture["b"])
        self.ledger: ScenarioLedger = build_pair_scenario_ledger(
            _REGISTRY, self.facts_a, self.facts_b
        )

    def test_d1_004_is_unimplemented(self) -> None:
        """D1-004（asset_hashes 为 non-collectable）状态为 unimplemented。"""
        entry = next(e for e in self.ledger.entries if e.rule_id == "D1-004")
        assert entry.status == "unimplemented"

    def test_unimplemented_gate_does_not_block(self) -> None:
        """unimplemented 状态的 gate 规则不使 blocked 为 True。"""
        result = summarize_ledger_gate(self.ledger, _REGISTRY)
        # D1-001/D1-002 在 complete 夹具下均 checkable，D1-004 为 unimplemented → 不阻断
        assert result["blocked"] is False
        assert "D1-004" not in result["blocked_rule_ids"]


# ── build_document_facts 边界场景 ─────────────────────────────────────────────

class TestBuildDocumentFactsEdgeCases:
    def test_none_extraction_returns_empty_present_facts(self) -> None:
        """extraction 为 None 时不抛异常，且 present_facts 为空。"""
        facts = build_document_facts(1, "x.pdf", None)
        assert isinstance(facts, DocumentFacts)
        assert facts.present_facts == frozenset()

    def test_empty_dict_extraction_returns_empty(self) -> None:
        """extraction 为空 dict 时 present_facts 为空。"""
        facts = build_document_facts(2, "y.pdf", {})
        assert facts.present_facts == frozenset()

    def test_non_collectable_never_in_present(self) -> None:
        """即使 extraction 含 asset_hashes 相关字段，non-collectable 事实也不进 present_facts。"""
        from app.core.collusion.rules.contracts import FACT_FIELDS

        facts = build_document_facts(
            3, "z.pdf",
            {"company_info": {"company_name": "测试公司"}, "personnel_list": []},
            has_text_chunks=True,
        )
        non_collectable_names = {k for k, v in FACT_FIELDS.items() if not v.collectable}
        assert facts.present_facts.isdisjoint(non_collectable_names)


# ── ScenarioLedger.to_dict 结构验证 ──────────────────────────────────────────

class TestScenarioLedgerToDict:
    def test_to_dict_excludes_checkable_from_entries(self) -> None:
        """to_dict() 的 entries 列表不包含 status='checkable' 的条目。"""
        fixture = _load_fixture("legal_rep_missing")
        facts_a = _make_facts(9, "投标I.pdf", fixture["a"])
        facts_b = _make_facts(10, "投标J.pdf", fixture["b"])
        ledger = build_pair_scenario_ledger(_REGISTRY, facts_a, facts_b)

        d = ledger.to_dict()
        for entry_dict in d["entries"]:
            assert entry_dict["status"] != "checkable"

    def test_to_dict_counts_include_all_statuses(self) -> None:
        """to_dict() 的 counts 包含全量统计，不因过滤而丢失 checkable 计数。"""
        fixture = _load_fixture("legal_rep_missing")
        facts_a = _make_facts(11, "投标K.pdf", fixture["a"])
        facts_b = _make_facts(12, "投标L.pdf", fixture["b"])
        ledger = build_pair_scenario_ledger(_REGISTRY, facts_a, facts_b)

        d = ledger.to_dict()
        counts = d["counts"]
        total = sum(counts.values())
        assert total == len(ledger.entries)
        # 全局计数必须包含 checkable 键
        assert "checkable" in counts

    def test_to_dict_version_field(self) -> None:
        """to_dict() 包含 version 字段，值为 1。"""
        fixture = _load_fixture("complete")
        facts_a = _make_facts(13, "投标M.pdf", fixture["a"])
        facts_b = _make_facts(14, "投标N.pdf", fixture["b"])
        ledger = build_pair_scenario_ledger(_REGISTRY, facts_a, facts_b)

        d = ledger.to_dict()
        assert d["version"] == 1
