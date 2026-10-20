"""LLM 调用结果分类 (P0-A 可靠性观测)

职责: 把 LLM 物理请求的结果/异常归类为标准 outcome 枚举, 作为可靠性统计的单一口径
不做: 不做重试决策、不落库、不修改通道状态
"""

from __future__ import annotations

import asyncio
import json
import re
from enum import Enum
from typing import Any, Optional

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class LlmOutcome(str, Enum):
    """单次物理 attempt 的结果分类"""

    VALID = "valid"
    EMPTY = "empty"
    INVALID_JSON = "invalid_json"
    SCHEMA_INVALID = "schema_invalid"
    HTTP_ERROR = "http_error"
    TRANSPORT_ERROR = "transport_error"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    AUTH_ERROR = "auth_error"
    DEGRADED_PLAIN_CHAT = "degraded_plain_chat"
    CANCELLED = "cancelled"


def classify_response(response: Any) -> LlmOutcome:
    """按原始 ChatCompletion 响应判定 valid / empty。

 None、空串、纯 <think> 内容均计 empty（对齐 空响应语义）。
 结构异常（无 choices）保守计 valid, 交由上层校验环节继续判定。
 """
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        return LlmOutcome.VALID
    if content is None:
        return LlmOutcome.EMPTY
    stripped = _THINK_RE.sub("", content).strip()
    return LlmOutcome.VALID if stripped else LlmOutcome.EMPTY


def classify_exception(
    exc: BaseException,
) -> tuple[LlmOutcome, Optional[int], Optional[str]]:
    """把异常归类为 (outcome, http_status, rate_limit_kind)。

    429 细分复用 rate_limit_signal 的四分类; APITimeoutError 继承
    APIConnectionError, 必须先判 timeout 再判 transport。
    """
    if isinstance(exc, (RateLimitError,)) or (
        isinstance(exc, APIStatusError) and exc.status_code == 429
    ):
        from app.core.ai.rate_limit_signal import (
            RateLimitKind,
            classify_rate_limit_error,
        )
        signal = classify_rate_limit_error(exc)
        kind = signal.kind.value
        if signal.kind is RateLimitKind.QUOTA_EXHAUSTED:
            return LlmOutcome.QUOTA_EXHAUSTED, 429, kind
        if signal.kind is RateLimitKind.AUTH_OR_CONFIG:
            return LlmOutcome.AUTH_ERROR, 429, kind
        return LlmOutcome.RATE_LIMITED, 429, kind

    if isinstance(exc, APIStatusError):
        status = exc.status_code
        if status in (401, 403):
            return LlmOutcome.AUTH_ERROR, status, None
        return LlmOutcome.HTTP_ERROR, status, None

    if isinstance(exc, (APITimeoutError, asyncio.TimeoutError)):
        return LlmOutcome.TIMEOUT, None, None

    if isinstance(exc, APIConnectionError):
        return LlmOutcome.TRANSPORT_ERROR, None, None

    if isinstance(exc, json.JSONDecodeError):
        return LlmOutcome.INVALID_JSON, None, None

    if isinstance(exc, asyncio.CancelledError):
        return LlmOutcome.CANCELLED, None, None

    # 延迟导入避免循环依赖(cloud_client -> llm_outcome)
    try:
        from app.core.ai.llm_errors import EmptyLLMResponseError
        if isinstance(exc, EmptyLLMResponseError):
            return LlmOutcome.EMPTY, None, None
    except ImportError:
        pass

    return LlmOutcome.TRANSPORT_ERROR, None, None
