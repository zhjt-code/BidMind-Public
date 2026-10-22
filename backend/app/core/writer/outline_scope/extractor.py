"""
职责: 从已检索到的招标原文抽取与当前投标章节相关的纲要级标题。
不做: 不选章节窗口、不挂目录树、不写库。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.core.ai.input_guard import retry_on_input_too_long_400
from app.core.ai.prompt_registry import resolve_runtime_params
from app.core.writer.outline_scope.contracts import ScopeTitle, sanitize_scope_title
from app.core.writer.prompts.outline_scope import get_scope_title_prompt
from app.core.writer.text_budget import shrink_semantic_text as _shrink_semantic_text
from app.models.chunk import Chunk


def render_scope_text(chunks: list[Chunk]) -> str:
    """把需求章 chunks 拼成抽取用正文。"""

    parts: list[str] = []
    for chunk in chunks:
        content = str(getattr(chunk, "content", "") or "").strip()
        if not content:
            continue
        page = getattr(chunk, "page_num", None)
        prefix = f"[p{page}] " if page is not None else ""
        parts.append(prefix + content)
    return "\n\n".join(parts)


def parse_scope_title_payload(raw: Any) -> list[ScopeTitle]:
    """解析 LLM JSON；非法项丢弃。"""

    payload = raw
    if isinstance(payload, dict):
        payload = payload.get("titles") or payload.get("items") or []
    if not isinstance(payload, list):
        return []

    titles: list[ScopeTitle] = []
    seen: set[str] = set()
    for item in payload:
        if isinstance(item, str):
            raw_title, hint = item, ""
        elif isinstance(item, dict):
            raw_title = str(item.get("title") or "")
            hint = str(item.get("source_hint") or "")
        else:
            continue
        cleaned = sanitize_scope_title(raw_title)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        titles.append(ScopeTitle(title=cleaned, source_hint=hint))
    return titles


async def extract_scope_titles(
    chunks: list[Chunk],
    llm_client: Any,
    *,
    chapter_title: str = "",
) -> list[ScopeTitle]:
    """从相关招标原文抽取纲要标题；失败返回空列表。"""

    scope_text = render_scope_text(chunks)
    if not scope_text.strip() or llm_client is None:
        return []

    initial_payload = {"scope_text": scope_text, "chapter_title": chapter_title}

    def _reduce_payload(
        payload: dict[str, str],
        retry_round: int,
    ) -> tuple[dict[str, str], dict[str, object]]:
        reduced = dict(payload)
        reduced["scope_text"] = _shrink_semantic_text(reduced["scope_text"], min_lines=2)
        return reduced, {
            "removed_segments": ["scope_text"],
            "retry_attempt": retry_round,
        }

    async def _send_request(payload: dict[str, str]):
        system_msg, user_msg = get_scope_title_prompt(
            scope_text=payload["scope_text"],
            chapter_title=payload.get("chapter_title", ""),
        )
        temperature, max_tokens = resolve_runtime_params(
            "writing.outline_scope_titles", 0.1, 2000,
        )
        return await llm_client.chat_json(
            user_message=user_msg,
            system_message=system_msg,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    try:
        raw_result = await retry_on_input_too_long_400(
            operation_name="实质内容标题抽取",
            initial_payload=initial_payload,
            send_request=_send_request,
            reduce_payload_semantically=_reduce_payload,
            max_retries=1,
            describe_payload=lambda payload: {"scope_text": len(payload.get("scope_text", ""))},
        )
    except Exception as exc:
        logger.warning(f"实质内容标题抽取失败: {exc}")
        return []

    return parse_scope_title_payload(raw_result)
