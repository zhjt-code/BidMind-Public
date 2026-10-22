"""固定模板 LLM-first 识别调用。

职责: 调用 writing.fixed_template_recognize 节点识别候选页中的全部固定模板。
不做: 不定位章节、不做后置校验、不访问数据库。
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from app.core.ai import get_review_client
from app.core.ai.prompt_registry import (
    resolve_runtime_params,
    resolve_system_prompt,
    resolve_user_template,
)
from app.core.writer.fixed_template.prompts import (
    FIXED_TEMPLATE_RECOGNIZE_SYSTEM,
    FIXED_TEMPLATE_RECOGNIZE_USER,
)
from app.core.writer.fixed_template.section_locator import SectionRange

NODE_KEY = "writing.fixed_template_recognize"
_DEFAULT_TEMPERATURE = 0.1
_DEFAULT_MAX_TOKENS = 6000


async def recognize_templates(
    chunks_text: str,
    ranges: list[SectionRange],
    *,
    project_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """调用 LLM 识别全部固定模板，失败时返回空数组。"""
    if not chunks_text.strip():
        return []
    system_msg = resolve_system_prompt(NODE_KEY, FIXED_TEMPLATE_RECOGNIZE_SYSTEM)
    user_template = resolve_user_template(NODE_KEY, FIXED_TEMPLATE_RECOGNIZE_USER)
    try:
        user_msg = user_template.format(
            chunks_text=chunks_text,
            page_range_hint=_page_range_hint(ranges),
            volume_type_hint=_volume_type_hint(ranges),
        )
    except (KeyError, IndexError, ValueError) as exc:
        logger.warning("fixed_template recognize prompt 格式化失败: {}", exc)
        return []
    temperature, max_tokens = resolve_runtime_params(
        NODE_KEY, _DEFAULT_TEMPERATURE, _DEFAULT_MAX_TOKENS,
    )
    try:
        result = await get_review_client(project_id=project_id).chat_json(
            user_message=user_msg,
            system_message=system_msg,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("fixed_template recognize LLM 调用失败: {}", exc)
        return []
    items = _normalize_recognize_response(result)
    if not items:
        logger.warning(
            "fixed_template recognize 返回归一化后为空: top_type={}, top_keys={}",
            type(result).__name__,
            sorted(result.keys()) if isinstance(result, dict) else "n/a",
        )
    return items


_RECOGNIZE_WRAPPER_KEYS = ("templates", "items", "data", "entries", "result")
_RECOGNIZE_ITEM_FIELDS = ("template_key", "title", "raw_text", "source_page_start")


def _normalize_recognize_response(result: Any) -> list[dict[str, Any]]:
    """兼容 LLM 输出的多种形变, 返回 dict 列表。: 真实样本中 LLM JSON 输出形态是连续光谱 (参考):
 - 顶层数组 [{...}, {...}]
 - wrapper 对象 {"templates": [...]} / {"items": [...]} / 同类别名
 - 裸单条 dict {"template_key": ..., "title": ..., "raw_text": ...}
 本函数把所有形态统一归一为 dict 列表; 完全无法归一时返回空列表,
 由 catalog 的兜底通道接管。
 """
    if isinstance(result, list):
        items: list[Any] = result
    elif isinstance(result, dict):
        items = []
        for key in _RECOGNIZE_WRAPPER_KEYS:
            value = result.get(key)
            if isinstance(value, list):
                items = value
                break
        if not items:
            # 裸单条 dict: 含识别核心字段任 2 项即视为单元素数组
            hit = sum(1 for field in _RECOGNIZE_ITEM_FIELDS if field in result)
            if hit >= 2:
                items = [result]
    else:
        items = []
    return [item for item in items if isinstance(item, dict)]


def _page_range_hint(ranges: list[SectionRange]) -> str:
    if not ranges:
        return "unknown"
    return ",".join(f"{item.page_start}-{item.page_end}" for item in ranges)


def _volume_type_hint(ranges: list[SectionRange]) -> str:
    volumes = sorted({item.volume_type for item in ranges if item.volume_type})
    return ",".join(volumes) if volumes else "unknown"
