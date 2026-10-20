"""
职责: chat_json 原始响应的文本清洗（think 标签剥离 + markdown 代码围栏剥离）
不做: 不解析 JSON、不修复、不发请求、不做重试决策
"""

from __future__ import annotations

import re


def strip_think_and_code_fences(raw_text: str) -> tuple[str, bool]:
    """剥离 think 标签与 markdown 代码围栏，返回 (正文, think 是否把正文洗空)。"""
    raw_len = len(raw_text)
    content = raw_text.strip()
    think_consumed = False
    if "<think>" in content:
        stripped_think = re.sub(
            r"<think>.*?</think>", "", content, flags=re.DOTALL
        ).strip()
        think_consumed = raw_len > 0 and not stripped_think
        content = stripped_think
    else:
        content = content.strip()

    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    return content.strip(), think_consumed
