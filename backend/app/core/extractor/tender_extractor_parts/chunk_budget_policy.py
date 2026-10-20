"""
职责: 招标分块提取的输出token预算自适应决策与有损JSON修复信号判定。
不做: 不发起LLM调用、不解析JSON、不读写缓存与数据库。
"""

from __future__ import annotations

import json
from enum import Enum

from app.core.contracts.degradation_signal import (
    DegradationSignal,
    drain_signals,
)

CHUNK_EXTRACT_INITIAL_MAX_TOKENS = 2000
RESCUE_INITIAL_MAX_TOKENS = 4000
CHUNK_EXTRACT_TOKEN_CEILING = 8192

LOSSY_REPAIR_TYPES = frozenset({
    "truncated_array",
    "truncated_object",
    "before_error",
    "before_error_object",
    "nested_object_rescue",
})


class ChunkJsonErrorKind(str, Enum):
    """招标分块提取 JSONDecodeError 的重试策略分类。"""

    TRUNCATION = "truncation"
    EMPTY_RESPONSE = "empty_response"
    MALFORMED = "malformed"


def initial_chunk_max_tokens(retry_mode: str | None) -> int:
    """返回分块提取首轮输出预算。"""
    if retry_mode == "validation_rescue":
        return RESCUE_INITIAL_MAX_TOKENS
    return CHUNK_EXTRACT_INITIAL_MAX_TOKENS


def escalate_chunk_max_tokens(current_max_tokens: int) -> int:
    """截断重试时将输出预算翻倍，并受统一上限约束。"""
    return min(current_max_tokens * 2, CHUNK_EXTRACT_TOKEN_CEILING)


def classify_chunk_extract_json_error(
    error: json.JSONDecodeError,
) -> ChunkJsonErrorKind:
    """区分截断、空响应与普通格式错误。

    `Expecting value ... (char 0)` 通常表示空响应或无 JSON 起点，
    对这类错误扩容没有意义，必须与真正截断分开。
    """
    msg = str(error)
    if error.pos == 0 and "Expecting value" in msg:
        return ChunkJsonErrorKind.EMPTY_RESPONSE
    if "Unterminated" in msg:
        return ChunkJsonErrorKind.TRUNCATION

    doc = (error.doc or "").strip()
    near_doc_end = error.pos >= max(len(doc) - 1, 0)
    if "Expecting ',' delimiter" in msg and near_doc_end:
        return ChunkJsonErrorKind.TRUNCATION
    if (
        "Expecting property name enclosed in double quotes" in msg
        and doc.endswith(("{", ","))
    ):
        return ChunkJsonErrorKind.TRUNCATION
    if "Expecting value" in msg and doc.endswith(("[", "{", ",", ":")):
        return ChunkJsonErrorKind.TRUNCATION
    return ChunkJsonErrorKind.MALFORMED


def pop_lossy_repair_signals() -> list[DegradationSignal]:
    """取出当前协程的有损 JSON 修复信号。"""
    return [
        signal
        for signal in drain_signals()
        if signal.repair_type in LOSSY_REPAIR_TYPES
    ]
