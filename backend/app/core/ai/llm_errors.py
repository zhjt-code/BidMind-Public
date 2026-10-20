"""
职责: 定义 LLM 调用层可调度异常信号（空响应等）。
不做: 不发起 HTTP/LLM 调用、不解析 JSON、不选择通道。
"""

from __future__ import annotations

from typing import Optional


class EmptyLLMResponseError(Exception):
    """标识 chat_json 清洗后无可解析文本的空响应。

    与 json.JSONDecodeError（有正文但非法 JSON）区分：空响应更接近通道/模型
    可靠性故障，dispatcher 应换通道续试，而不是当作同通道语义重试。
    """

    def __init__(
        self,
        message: str,
        *,
        model_name: str = "",
        finish_reason: Optional[str] = None,
        raw_len: int = 0,
        think_consumed: bool = False,
    ) -> None:
        super().__init__(message)
        self.model_name = model_name
        self.finish_reason = finish_reason
        self.raw_len = raw_len
        self.think_consumed = think_consumed
