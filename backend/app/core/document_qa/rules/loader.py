"""
职责: 加载并缓存文档质检 YAML 规则, 计算规则配置摘要(参与 run fingerprint)
不做: 不解释规则语义、不执行检查
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_RULES_DIR = Path(__file__).parent
_RULE_FILES = (
    "sequences.yaml", "hygiene.yaml", "entity_slots.yaml", "layout.yaml", "heading.yaml",
    "regions.yaml",
)


@lru_cache(maxsize=1)
def load_rules() -> dict[str, Any]:
    """加载全部规则文件, 返回 {文件名去后缀: 解析后的 dict}。"""
    rules: dict[str, Any] = {}
    for name in _RULE_FILES:
        path = _RULES_DIR / name
        with path.open("r", encoding="utf-8") as fp:
            rules[path.stem] = yaml.safe_load(fp) or {}
    return rules


@lru_cache(maxsize=1)
def rules_digest() -> str:
    """规则文件内容摘要: 规则一变, run fingerprint 即变, 触发重算。"""
    hasher = hashlib.sha256()
    for name in _RULE_FILES:
        hasher.update((_RULES_DIR / name).read_bytes())
    return hasher.hexdigest()[:16]
