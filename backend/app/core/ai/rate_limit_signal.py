"""LLM 429 分类信号。

职责: 把 OpenAI 兼容的 429/限流异常翻译为结构化分类信号与冷却建议
不做: 不修改通道状态、不执行速率控制、不感知 dispatcher 与 AIMD
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
import random
import re
from typing import Any, Mapping, Optional

from app.core.config import settings


class RateLimitKind(str, Enum):
    """429 语义分类。"""

    RATE_LIMIT = "rate_limit"
    QUOTA_EXHAUSTED = "quota_exhausted"
    AUTH_OR_CONFIG = "auth_or_config"
    UNKNOWN_429 = "unknown_429"


@dataclass(frozen=True)
class RateLimitSignal:
    """LLM 限流分类结果。"""

    kind: RateLimitKind
    retry_after_seconds: Optional[int] = None
    limit_requests: Optional[int] = None
    remaining_requests: Optional[int] = None
    reset_seconds: Optional[int] = None
    declared_rpm: Optional[int] = None
    provider_code: str = ""
    feeds_aimd: bool = True
    cooldown_seconds: int = 0


_RPM_LIMIT_RE = re.compile(r"RPM\s+limit\s+(\d+)", re.IGNORECASE)
_QUOTA_MARKERS = (
    "insufficient_quota",
    "quota exhausted",
    "current quota",
    "exceeded your current quota",
    "余额不足",
    "额度不足",
)
_AUTH_CONFIG_MARKERS = (
    "invalid_api_key",
    "invalid api key",
    "incorrect api key",
    "permission denied",
    "unauthorized",
    "forbidden",
    "model not found",
)


def classify_rate_limit_error(exc: Exception) -> RateLimitSignal:
    """把 OpenAI 兼容异常分类为可执行的限流信号。"""

    body = _extract_error_body(exc)
    headers = _extract_headers(exc)
    message = _joined_message(exc, body)
    lowered = message.lower()

    provider_code = _as_str(body.get("code"))
    error_type = _as_str(body.get("type")).lower()
    declared_rpm = _extract_declared_rpm(message)
    retry_after = _parse_retry_after(headers.get("retry-after"))
    limit_requests = _parse_int_header(headers.get("x-ratelimit-limit-requests"))
    remaining_requests = _parse_int_header(headers.get("x-ratelimit-remaining-requests"))
    reset_seconds = _parse_reset_seconds(headers.get("x-ratelimit-reset-requests"))

    if (
        "insufficient_quota" in error_type
        or "insufficient_quota" in provider_code.lower()
        or any(marker in lowered for marker in _QUOTA_MARKERS)
    ):
        kind = RateLimitKind.QUOTA_EXHAUSTED
        feeds_aimd = False
    elif (
        any(marker in lowered for marker in _AUTH_CONFIG_MARKERS)
        or provider_code.lower() in {"invalid_api_key", "model_not_found"}
    ):
        kind = RateLimitKind.AUTH_OR_CONFIG
        feeds_aimd = False
    elif (
        retry_after is not None
        or declared_rpm is not None
        or provider_code == "429001"
        or "rate limit" in lowered
        or "ratelimit" in lowered
        or "请求速率" in message
    ):
        kind = RateLimitKind.RATE_LIMIT
        feeds_aimd = True
    else:
        kind = RateLimitKind.UNKNOWN_429
        feeds_aimd = True

    base = _base_cooldown_seconds(kind, retry_after)
    cooldown = _apply_jitter(
        min(base, int(settings.LLM_RATE_LIMIT_COOLDOWN_MAX))
        if kind is not RateLimitKind.QUOTA_EXHAUSTED
        else base
    )

    return RateLimitSignal(
        kind=kind,
        retry_after_seconds=retry_after,
        limit_requests=limit_requests,
        remaining_requests=remaining_requests,
        reset_seconds=reset_seconds,
        declared_rpm=declared_rpm,
        provider_code=provider_code,
        feeds_aimd=feeds_aimd,
        cooldown_seconds=cooldown,
    )


def resolve_cooldown_seconds(signal: RateLimitSignal) -> int:
    """返回分类信号建议的冷却秒数。"""

    return signal.cooldown_seconds


def _extract_error_body(exc: Exception) -> dict[str, Any]:
    body = getattr(exc, "body", None)
    if isinstance(body, Mapping):
        error = body.get("error")
        if isinstance(error, Mapping):
            return dict(error)
        return dict(body)
    return {}


def _as_str(value: object) -> str:
    return "" if value is None else str(value)


def _extract_headers(exc: Exception) -> dict[str, str]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return {}
    return {str(key).lower(): str(value) for key, value in headers.items()}


def _joined_message(exc: Exception, body: Mapping[str, Any]) -> str:
    parts = [str(exc)]
    for key in ("message", "message_zh", "type", "code"):
        value = body.get(key)
        if value is not None:
            parts.append(str(value))
    return " ".join(parts)


def _extract_declared_rpm(message: str) -> Optional[int]:
    match = _RPM_LIMIT_RE.search(message)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _parse_retry_after(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    stripped = value.strip()
    if stripped.isdigit():
        return max(0, int(stripped))
    try:
        dt = parsedate_to_datetime(stripped)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(round(delta)))
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_int_header(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(float(value.strip()))
    except (TypeError, ValueError):
        return None


def _parse_reset_seconds(value: Optional[str]) -> Optional[int]:
    return _parse_retry_after(value) or _parse_int_header(value)


def _base_cooldown_seconds(
    kind: RateLimitKind,
    retry_after: Optional[int],
) -> int:
    if kind is RateLimitKind.QUOTA_EXHAUSTED:
        return int(settings.LLM_QUOTA_EXHAUSTED_COOLDOWN)
    if retry_after is not None:
        return max(1, retry_after)
    if kind is RateLimitKind.AUTH_OR_CONFIG:
        return int(settings.LLM_QUOTA_EXHAUSTED_COOLDOWN)
    return int(settings.LLM_RATE_LIMIT_COOLDOWN_DEFAULT)


def _apply_jitter(seconds: int, ratio: float = 0.2) -> int:
    if seconds <= 0:
        return 0
    if seconds >= int(settings.LLM_QUOTA_EXHAUSTED_COOLDOWN):
        return seconds
    return max(1, int(round(seconds * random.uniform(1 - ratio, 1 + ratio))))
