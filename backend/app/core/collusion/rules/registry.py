"""
职责: 加载并校验串标规则与联合指纹数据。
不做: 检测命中、风险判定、可核查性判定。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.collusion.dimension_keys import COLLUSION_DIM_KEYS_SET
from app.core.collusion.rules.contracts import PatternSpec, RuleSpec

_DATA_DIR = Path(__file__).with_name("data")
_DIM_FILES = (
    "d1.json", "d2.json", "d3.json", "d4.json",
    "d5.json", "d6.json", "d7.json", "d8.json",
)


@dataclass(frozen=True)
class RuleRegistry:
    """编译后的串标规则注册表（不可变快照）。"""

    rules: tuple[RuleSpec, ...]
    patterns: tuple[PatternSpec, ...]

    def by_id(self, rule_id: str) -> RuleSpec | None:
        """按 rule_id 查询单条规则；未找到返回 None。"""
        for rule in self.rules:
            if rule.rule_id == rule_id:
                return rule
        return None

    def by_dimension(self, dim: str) -> tuple[RuleSpec, ...]:
        """返回指定维度的所有规则。"""
        return tuple(r for r in self.rules if r.dimension == dim)

    def count_by_dimension(self) -> dict[str, int]:
        """各维度规则数量统计（有规则的维度才出现在结果中）。"""
        counts: dict[str, int] = {}
        for rule in self.rules:
            counts[rule.dimension] = counts.get(rule.dimension, 0) + 1
        return counts

    def gate_rules(self) -> tuple[RuleSpec, ...]:
        """返回所有 ledger_gate=True 的规则。"""
        return tuple(r for r in self.rules if r.ledger_gate)


def _load_json_file(path: Path) -> dict[str, Any]:
    """读取 JSON 文件（UTF-8 编码）。"""
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def _validate_rule_specs(
    file_dim: str,
    raw_rules: list[dict[str, Any]],
) -> list[RuleSpec]:
    """解析并字段级校验单维度的规则列表。

    Args:
        file_dim: 文件顶层声明的 dimension（用于一致性核查）
        raw_rules: 原始规则 dict 列表

    Raises:
        ValueError: 规则校验失败或 dimension 与文件声明不一致
    """
    specs: list[RuleSpec] = []
    for raw in raw_rules:
        try:
            spec = RuleSpec.model_validate(raw)
        except ValidationError as exc:
            rule_id = raw.get("rule_id", "<unknown>")
            raise ValueError(f"规则 {rule_id!r} 校验失败: {exc}") from exc
        if spec.dimension != file_dim:
            raise ValueError(
                f"规则 {spec.rule_id!r} 的 dimension={spec.dimension!r} "
                f"与文件顶层 dimension={file_dim!r} 不一致"
            )
        specs.append(spec)
    return specs


def _validate_cross_rules(
    all_rules: tuple[RuleSpec, ...],
    all_ids: frozenset[str],
) -> None:
    """校验 cross_rules 引用合法性（不能指向自身或不存在的规则）。"""
    for rule in all_rules:
        for ref in rule.cross_rules:
            if ref == rule.rule_id:
                raise ValueError(
                    f"规则 {rule.rule_id!r} 的 cross_rules 引用了自身"
                )
            if ref not in all_ids:
                raise ValueError(
                    f"规则 {rule.rule_id!r} 的 cross_rules 引用了不存在的规则: {ref!r}"
                )


def _validate_patterns(
    patterns: tuple[PatternSpec, ...],
    all_ids: frozenset[str],
) -> None:
    """校验 pattern 中 rule_ids 引用的合法性（dimension/min_level 已在 PatternGroupSpec 中校验）。"""
    for pat in patterns:
        for grp in pat.required_rule_groups:
            if grp.rule_ids is not None:
                for rid in grp.rule_ids:
                    if rid not in all_ids:
                        raise ValueError(
                            f"Pattern {pat.pattern_id!r} 引用了不存在的 rule_id: {rid!r}"
                        )


def parse_rule_registry(
    rule_payloads: dict[str, dict[str, Any]],
    pattern_payload: dict[str, Any],
) -> RuleRegistry:
    """从内联数据构建 RuleRegistry（供测试与 load_rule_registry 调用）。

    Args:
        rule_payloads: 维度名 -> 文件内容 dict（含 "dimension"/"version"/"rules" 键）
        pattern_payload: patterns.json 文件内容 dict（含 "version"/"patterns" 键）

    Raises:
        ValueError: 任何规则或模式校验失败
    """
    all_specs: list[RuleSpec] = []
    seen_ids: set[str] = set()

    for _dim_key, payload in rule_payloads.items():
        file_dim = payload.get("dimension", "")
        if file_dim not in COLLUSION_DIM_KEYS_SET:
            raise ValueError(f"文件顶层 dimension={file_dim!r} 不在已知维度列表中")
        raw_rules: list[dict[str, Any]] = payload.get("rules") or []
        specs = _validate_rule_specs(file_dim, raw_rules)
        for spec in specs:
            if spec.rule_id in seen_ids:
                raise ValueError(f"rule_id 重复: {spec.rule_id!r}")
            seen_ids.add(spec.rule_id)
            all_specs.append(spec)

    all_rules = tuple(all_specs)
    all_ids = frozenset(seen_ids)
    _validate_cross_rules(all_rules, all_ids)

    raw_patterns: list[dict[str, Any]] = pattern_payload.get("patterns") or []
    try:
        patterns = tuple(PatternSpec.model_validate(p) for p in raw_patterns)
    except ValidationError as exc:
        raise ValueError(f"Pattern 校验失败: {exc}") from exc
    _validate_patterns(patterns, all_ids)

    return RuleRegistry(rules=all_rules, patterns=patterns)


@lru_cache(maxsize=1)
def load_rule_registry() -> RuleRegistry:
    """从 rules/data/ 目录加载并缓存规则注册表。

    首次调用后结果缓存在进程级，后续调用直接返回缓存。
    数据目录路径：与本文件同级的 data/ 子目录。
    """
    rule_payloads: dict[str, dict[str, Any]] = {}
    for filename in _DIM_FILES:
        payload = _load_json_file(_DATA_DIR / filename)
        file_dim: str = payload.get("dimension", "")
        rule_payloads[file_dim] = payload
    pattern_payload = _load_json_file(_DATA_DIR / "patterns.json")
    return parse_rule_registry(rule_payloads, pattern_payload)
