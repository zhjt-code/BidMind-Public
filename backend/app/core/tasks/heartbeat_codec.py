"""
职责: 心跳字段跨 Redis 的 bool 编解码与显式真值判定。
不做: 不读写 Redis、不组装业务进度、不决定 skip-vl 策略。
"""

from __future__ import annotations

import json
from typing import Any

_TRUE_TOKENS = frozenset({"true"})
_FALSE_TOKENS = frozenset({"false"})


def encode_heartbeat_field(value: Any) -> str:
    """编码心跳字段。bool 走 JSON token，避免 str(True)=='True' 无法 json.loads。"""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return json.dumps(value)
    return str(value)


def decode_heartbeat_field(value: Any) -> Any:
    """还原心跳字段。仅转换 true/false 词面，不碰 stage/progress。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _TRUE_TOKENS:
            return True
        if token in _FALSE_TOKENS:
            return False
    return value


def is_explicit_true(value: Any) -> bool:
    """跳过类声明只认显式真值，拒绝 bool('False') 那种非空即真。"""
    decoded = decode_heartbeat_field(value)
    return decoded is True
