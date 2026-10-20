"""LLM 429 分类信号单元测试。"""

from __future__ import annotations

from email.utils import formatdate
import sys
import time
from pathlib import Path

import httpx
from openai import APIStatusError

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai.rate_limit_signal import (  # noqa: E402
    RateLimitKind,
    classify_rate_limit_error,
)


def _make_error(
    message: str,
    *,
    body: object | None = None,
    headers: dict[str, str] | None = None,
    status_code: int = 429,
) -> APIStatusError:
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    response = httpx.Response(
        status_code=status_code,
        request=request,
        headers=headers or {},
    )
    return APIStatusError(message, response=response, body=body)


def test_insufficient_quota_does_not_feed_aimd() -> None:
    error = _make_error(
        "Error code: 429 - insufficient quota",
        body={
            "error": {
                "message": "You exceeded your current quota",
                "type": "insufficient_quota",
                "code": "insufficient_quota",
            }
        },
    )

    signal = classify_rate_limit_error(error)

    assert signal.kind is RateLimitKind.QUOTA_EXHAUSTED
    assert signal.provider_code == "insufficient_quota"
    assert signal.feeds_aimd is False
    assert signal.cooldown_seconds == 1800


def test_tencent_rpm_limit_uses_retry_after_and_declared_rpm() -> None:
    error = _make_error(
        "Error code: 429 - {'error': {'message': 'The request rate exceeds "
        "the current model RPM limit 60.', 'code': '429001'}}",
        body={
            "error": {
                "message": (
                    "The request rate exceeds the current model RPM limit 60."
                ),
                "code": "429001",
                "type": "gateway_error",
            }
        },
        headers={"Retry-After": "17"},
    )

    signal = classify_rate_limit_error(error)

    assert signal.kind is RateLimitKind.RATE_LIMIT
    assert signal.provider_code == "429001"
    assert signal.declared_rpm == 60
    assert signal.retry_after_seconds == 17
    assert signal.feeds_aimd is True
    assert 13.6 <= signal.cooldown_seconds <= 20.4


def test_http_date_retry_after_is_supported() -> None:
    retry_at = formatdate(time.time() + 45, usegmt=True)
    error = _make_error(
        "rate limit",
        body={"error": {"message": "rate limit"}},
        headers={"Retry-After": retry_at},
    )

    signal = classify_rate_limit_error(error)

    assert signal.kind is RateLimitKind.RATE_LIMIT
    assert 33 <= signal.cooldown_seconds <= 54


def test_unknown_429_uses_default_short_cooldown_and_feeds_aimd() -> None:
    error = _make_error("Error code: 429 - unknown")

    signal = classify_rate_limit_error(error)

    assert signal.kind is RateLimitKind.UNKNOWN_429
    assert signal.feeds_aimd is True
    assert 24 <= signal.cooldown_seconds <= 36
