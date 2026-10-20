"""LLM 原始响应的多候选 JSON 回退。

职责: 从 LLM 原始文本响应中按出现顺序提取全部顶层 JSON 候选 payload，
 并在首个 payload Pydantic 校验失败时逐个尝试其余候选完成回退。
不做: 不做截断修复（归 cloud_client._try_repair_json）、不调用 LLM、
 不访问数据库、不记录调用指标。

背景: 模型在无约束模式下常在正式输出前回显 Prompt 内的 JSON 常量
（如评分基准 {"expected_total": 100.0}），"取首个完整 JSON"策略
（语义）会把回显对象当作答案、丢弃其后的真实输出。
本模块提供校验失败后的候选回退，不改变首个 payload 优先的既有语义。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger
from pydantic import BaseModel, ValidationError

# 单次响应最多扫描的候选数，防止病态输出导致 O(n^2) 扫描放大
_MAX_CANDIDATES = 8


def extract_json_payload_candidates(
    text: str, *, max_candidates: int = _MAX_CANDIDATES
) -> list[str]:
    """按出现顺序提取文本中全部顶层完整 JSON 对象/数组片段。

 使用标准 JSON 词法分析器（json.JSONDecoder.raw_decode），
 不使用裸括号计数（教训：字符串值内的 []/{} 会被误判）。

 Args:
 text: LLM 原始响应文本（可含 prose、markdown 围栏等）。
 max_candidates: 最多返回的候选数量。

 Returns:
 list[str]: 完整可解析的 JSON 片段列表，可能为空。
 """
    if not text:
        return []

    decoder = json.JSONDecoder()
    candidates: list[str] = []
    idx = 0
    length = len(text)
    while idx < length and len(candidates) < max_candidates:
        char = text[idx]
        if char in "[{":
            try:
                _, end = decoder.raw_decode(text[idx:])
                candidates.append(text[idx:idx + end])
                idx += end
                continue
            except json.JSONDecodeError:
                pass
        idx += 1
    return candidates


def recover_validated_payload(
    raw_text: str,
    response_model: type[BaseModel],
    *,
    skip_payload: Optional[dict[str, Any]] = None,
) -> Optional[BaseModel]:
    """在首个 payload 校验失败后，尝试用同响应中其余 JSON 候选完成校验。

    Args:
        raw_text: LLM 原始响应文本。
        response_model: 目标 Pydantic 模型类。
        skip_payload: 已经校验失败的 payload（通常是首个），
                      与之相等的候选直接跳过，避免重复失败。

    Returns:
        校验通过的模型实例；全部候选失败或无候选时返回 None。
    """
    candidates = extract_json_payload_candidates(raw_text)
    for index, candidate in enumerate(candidates):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        if skip_payload is not None and parsed == skip_payload:
            continue
        try:
            recovered = response_model(**parsed)
        except ValidationError:
            continue
        logger.warning(
            f"JSON候选回退成功: 首个payload校验失败, "
            f"候选#{index + 1}/{len(candidates)}通过 "
            f"{response_model.__name__} 校验"
        )
        return recovered
    return None


def rescue_first_nested_object(stripped: str) -> Optional[str]:
    """顶层对象无法抢救时, 返回首个完整内嵌 {...} 对象片段（最后手段,）。

 仅对 `{` 开头文本生效（保持 数组语义: 截断数组失败即失败,
 不得降级为单条内嵌对象）。典型场景: 首个 `{` 是非 JSON 的占位/花括号
 文本, 真实 JSON 对象在其后。调用方（cloud_client._try_repair_json 策略5）
 必须把本降级排在截断修复之后——截断顶层对象场景下内嵌对象是答案片段。

 Args:
 stripped: 已去除首尾空白、以 `{` 开头的原始文本。

 Returns:
 首个完整内嵌 JSON 对象片段; 无可用片段或文本非 `{` 开头时返回 None。
 """
    if not stripped.startswith("{"):
        return None
    decoder = json.JSONDecoder()
    idx = 1
    length = len(stripped)
    while idx < length:
        if stripped[idx] != "{":
            idx += 1
            continue
        try:
            _, end = decoder.raw_decode(stripped[idx:])
            return stripped[idx:idx + end]
        except json.JSONDecodeError:
            idx += 1
    return None


__all__ = [
    "extract_json_payload_candidates",
    "recover_validated_payload",
    "rescue_first_nested_object",
]
