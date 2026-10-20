"""噪音规则分类器（评审意义降级护栏，类别感知）。

职责: 用可配置规则库(noise_rules.yaml)+确定性正则(合同模板占位符)对一条招标要求
 做"评审意义降级"兜底——命中明确告知/程序/禁止性规则时返回 procedural /
 informational。支持"全局规则 + 按类别规则"，使降噪覆盖全部六类要求而非仅商务。
不做: 不升级为 actionable、不豁免否决项(否决项豁免由 relevance_resolver 负责)、
 不调用 LLM、不访问 DB。

定位: 作为 LLM `review_relevance` 标注的规则护栏（LLM 漏标时纠偏），
 参考 教训——业务高风险输出需保留规则兜底通道。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger

_NOISE_RULES: Optional[dict] = None

_RELEVANCE_LEVELS = ("procedural", "informational")

# 合同模板占位符（P1）: 招标文件引用的未填空合同模板条款，双方数值尚未
# 约定，投标文件无从响应 → informational。高特异性正则，正常要求不会误命中。
_TEMPLATE_PLACEHOLDER_RE = re.compile(
    r"\[\s*具体[^\]]{0,12}\]"      # [具体比例] / [具体金额] / [具体数额]
    r"|\[\s*(?:金额|比例|数额|百分比|天数|日期)\s*\]"
    r"|【\s*具体[^】]{0,12}】"
    r"|_{3,}"                        # ____ 连续下划线留空
    r"|\[\s{2,}\]"                   # [  ] 空方括号
)


def detect_template_placeholder(description: str | None) -> bool:
    """判定描述是否含未填写的合同模板占位符（命中即应降级 informational）。"""
    if not description:
        return False
    return bool(_TEMPLATE_PLACEHOLDER_RE.search(description))


def load_noise_rules() -> dict:
    """加载并缓存 noise_rules.yaml。"""
    global _NOISE_RULES
    if _NOISE_RULES is None:
        yaml_path = Path(__file__).parent / "noise_rules.yaml"
        try:
            with yaml_path.open(encoding="utf-8") as fh:
                _NOISE_RULES = yaml.safe_load(fh) or {}
        except Exception as exc:  # 配置缺失不应阻断提取
            logger.warning("noise_rules.yaml 加载失败，降噪护栏降级为空: {}", exc)
            _NOISE_RULES = {}
    return _NOISE_RULES


def _collect_keywords(rules: dict, level: str, category: str | None) -> list[str]:
    """汇总某降级级别的关键词：全局 + 该类别。

    兼容旧版扁平结构（顶层直接 procedural/informational 视为全局）。
    """
    keywords: list[str] = []

    global_rules = rules.get("global")
    if not isinstance(global_rules, dict):
        # 旧版扁平结构兜底：顶层 procedural/informational 当作全局
        global_rules = rules if ("procedural" in rules or "informational" in rules) else {}
    keywords.extend(global_rules.get(level, []) or [])

    if category:
        by_category = rules.get("by_category") or {}
        cat_rules = by_category.get(category) or {}
        if isinstance(cat_rules, dict):
            keywords.extend(cat_rules.get(level, []) or [])

    return keywords


def classify_noise_relevance(
    description: str | None,
    keywords: list[str] | None = None,
    category: str | None = None,
) -> Optional[str]:
    """规则兜底判定一条要求的"降级"评审意义。

    Args:
        description: 要求描述。
        keywords: 关键词列表（参与匹配）。
        category: 要求分类(QUAL/TECH/COMM/PERF/STAFF/FORMAT)，用于命中按类别规则；
                  None 时仅用全局规则。

    Returns:
        "procedural" / "informational" 命中时返回对应级别；未命中返回 None。
    """
    text = (description or "") + " " + " ".join(str(k) for k in (keywords or []))
    if not text.strip():
        return None

    # 确定性正则优先: 未填占位符的合同模板条款（, 如"赔偿[具体比例]%"）
    if detect_template_placeholder(description):
        return "informational"

    rules = load_noise_rules()
    for level in _RELEVANCE_LEVELS:
        for keyword in _collect_keywords(rules, level, category):
            if keyword and keyword in text:
                return level
    return None
