"""输入长度超限兜底工具。

仅在云端返回 input-too-long 类 400 时触发语义级重试。
首次请求必须沿用业务层原始 payload，禁止提前裁剪。
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar

from loguru import logger
from openai import APIStatusError

TPayload = TypeVar("TPayload")
TResult = TypeVar("TResult")

_INPUT_TOO_LONG_PATTERNS = (
    re.compile(r"range of input length", re.IGNORECASE),
    re.compile(r"input length", re.IGNORECASE),
    re.compile(r"prompt too long", re.IGNORECASE),
    re.compile(r"context length", re.IGNORECASE),
    # Qwen 字节上限 / Doubao 消息 token 上限（既有模式不覆盖）
    re.compile(r"max bytes", re.IGNORECASE),
    re.compile(r"max message tokens", re.IGNORECASE),
)


def is_input_too_long_error(error: APIStatusError) -> bool:
    """识别云端服务返回的输入长度超限类 400。"""
    if getattr(error, "status_code", None) != 400:
        return False

    message = str(error)
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        message = f"{message} {json.dumps(body, ensure_ascii=False)}"
    return any(pattern.search(message) for pattern in _INPUT_TOO_LONG_PATTERNS)


def _format_log_meta(meta: Mapping[str, Any] | None) -> str:
    if not meta:
        return ""

    parts: list[str] = []
    for key, value in meta.items():
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, (list, tuple, set)):
            rendered = "/".join(str(item) for item in value)
        else:
            rendered = str(value)
        parts.append(f"{key}={rendered}")
    return ", ".join(parts)


async def retry_on_input_too_long_400(
    *,
    operation_name: str,
    initial_payload: TPayload,
    send_request: Callable[[TPayload], Awaitable[TResult]],
    reduce_payload_semantically: Callable[[TPayload, int], tuple[TPayload, Mapping[str, Any] | None]],
    max_retries: int,
    describe_payload: Callable[[TPayload], Mapping[str, Any] | None] | None = None,
) -> TResult:
    """仅在 input-too-long 400 时按业务语义收缩 payload 后重试。

    Args:
        operation_name: 日志上下文
        initial_payload: 首次请求 payload，必须保持原样
        send_request: 实际发送函数
        reduce_payload_semantically: 语义收缩器，入参为上轮 payload 和第几次重试(从1开始)
        max_retries: 最多语义重试次数，不含首次原样发送
        describe_payload: 可选的 payload 观测器，用于日志
    """
    current_payload = initial_payload

    for retry_attempt in range(max_retries + 1):
        try:
            return await send_request(current_payload)
        except APIStatusError as exc:
            if not is_input_too_long_error(exc) or retry_attempt >= max_retries:
                raise

            next_payload, reduction_meta = reduce_payload_semantically(
                current_payload,
                retry_attempt + 1,
            )
            before_meta = describe_payload(current_payload) if describe_payload else None
            after_meta = describe_payload(next_payload) if describe_payload else None

            before_log = _format_log_meta(before_meta)
            after_log = _format_log_meta(after_meta)
            reduction_log = _format_log_meta(reduction_meta)

            suffix_parts = [
                f"attempt {retry_attempt + 1}/{max_retries}",
                before_log and f"before[{before_log}]",
                after_log and f"after[{after_log}]",
                reduction_log and f"changes[{reduction_log}]",
            ]
            suffix = ", ".join(part for part in suffix_parts if part)
            logger.warning(
                f"{operation_name}: 命中输入过长400，触发语义收缩重试 ({suffix})"
            )
            current_payload = next_payload

    raise RuntimeError(f"{operation_name} 输入兜底重试逻辑异常")
