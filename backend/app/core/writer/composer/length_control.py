"""W1: free_writing 章节长度闭环控制。

职责: free_writing 章节的输出 token 预算计算与生成后字数偏差校正；
      提供纯函数供 free_writing_route 在生成前/生成后调用。
不做: 内容生成（Prompt 渲染、LLM 调用、HTML 规范化）、落库、Gate 裁决、
      data_fill / fixed_template 路径（这两路无字数闭环需求）。

设计要点:
- ``compute_section_max_tokens``: 把 target_word_count 与 settings 的
  tokens_per_char、上限、registry 默认值统一收敛到一个公式。
- ``ensure_section_length``: 计算纯文本字数（剥离 HTML 标签），偏差超
  动态软阈值区间时触发**一次**定向重生成；重生成失败或偏差更大时保留
  原稿并记录 degradation。MIN_RATIO=0 即关闭闭环。

历史教训:
    原实现仅在 Prompt 内写"字数控制在 X 字左右"，生成后只统计字数落库，
    无任何偏差检测；WRITING_MAX_SECTION_TOKENS=4000 与目录分配上限
    5000 字脱节，长章节可能因 token 截断烂尾。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from app.core.writer.section_composer import SectionComposer


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_PLACEHOLDER_RE = re.compile(r"\[待补充[^\]]*\]")


def count_plain_text(content: str) -> int:
    """剥离 HTML 标签、占位符与空白后的纯文本字符数。

    用于与 ``target_word_count``（基于纯中文计数）做偏差比对。
    """
    if not content:
        return 0
    text = _HTML_TAG_RE.sub("", content)
    text = _PLACEHOLDER_RE.sub("", text)
    text = re.sub(r"\s+", "", text)
    return len(text)


def compute_section_max_tokens(
    target_word_count: int | None,
    *,
    registry_max_tokens: int,
) -> int:
    """W1: 根据建议字数动态计算 free_writing 的 max_tokens 上限。

    公式: ``max(registry_max_tokens, target * tokens_per_char + 800)``
          再被 ``WRITING_MAX_SECTION_TOKENS_CAP`` 截断。

    Args:
        target_word_count: 目录阶段分配的章节建议字数；None/0 时回退到
            registry_max_tokens。
        registry_max_tokens: 从 ``resolve_runtime_params`` 拿到的默认上限
            （通常等于 ``settings.WRITING_MAX_SECTION_TOKENS``）。
    """
    from app.core.config import settings

    registry_default = max(0, int(registry_max_tokens or 0))
    cap = max(registry_default, int(settings.WRITING_MAX_SECTION_TOKENS_CAP))

    target = int(target_word_count or 0)
    if target <= 0:
        return min(cap, max(registry_default, settings.WRITING_MAX_SECTION_TOKENS))

    tokens_per_char = float(settings.WRITING_SECTION_TOKENS_PER_CHAR)
    desired = int(target * tokens_per_char) + 800
    return min(cap, max(registry_default, desired))


def resolve_section_length_tolerance(
    target_word_count: int | None,
) -> tuple[float, float]:
    """根据建议字数给出 free_writing 长度闭环软阈值。

    职责: 根据建议字数给出 free_writing 长度闭环软阈值。
    不做: 字数生成、Prompt 渲染、落库、data_fill/fixed_template 路径控制。
    """
    from app.core.config import settings

    min_ratio = float(settings.WRITING_WORD_COUNT_MIN_RATIO)
    configured_max = float(settings.WRITING_WORD_COUNT_MAX_RATIO)

    target = int(target_word_count or 0)
    if target <= 0:
        return min_ratio, configured_max
    if target <= 1500:
        dynamic_max = 2.0
    elif target <= 3000:
        dynamic_max = 1.8
    else:
        dynamic_max = 1.6
    return min_ratio, min(configured_max, dynamic_max)


def _record_length_degradation(node_id: int, reason: str) -> None:
    try:
        from app.core.observability.degradation_aggregator import (
            record_degradation as _record_degradation,
        )
        _record_degradation(
            kind="compose_length_deviation",
            ref=f"node_{node_id}:free_writing",
            reason=reason,
        )
    except Exception:
        pass


async def ensure_section_length(
    composer: "SectionComposer",
    node_id: int,
    node_title: str,
    content: str,
    target_word_count: int | None,
) -> str:
    """W1: 生成后字数闭环校验 + 至多一次定向重生成。

    返回调整后的 content；MIN_RATIO=0、目标缺失或偏差在容忍区间内时原样返回。
    重生成失败或偏差未改善时保留原稿并 ``record_degradation``。
    """
    from app.core.ai.prompt_registry import resolve_runtime_params
    from app.core.config import settings
    from app.core.writer.prompts.section_compose import get_length_adjust_prompt
    from app.core.writer.section_structure import normalize_section_html

    min_ratio, max_ratio = resolve_section_length_tolerance(target_word_count)
    if min_ratio <= 0 or not target_word_count or target_word_count <= 0:
        return content

    current = count_plain_text(content)
    if current == 0:
        return content

    ratio = current / float(target_word_count)
    if min_ratio <= ratio <= max_ratio:
        return content

    direction = "扩写" if ratio < min_ratio else "压缩"
    logger.info(
        "[长度闭环] node={} target={} current={} ratio={:.2f} tolerance={:.2f}-{:.2f} -> 触发{}",
        node_title, target_word_count, current, ratio, min_ratio, max_ratio, direction,
    )
    from app.core.writer.composer.compose_metrics import writer_compose_length_adjust_total

    writer_compose_length_adjust_total.labels(outcome="triggered").inc()

    system_msg, user_msg = get_length_adjust_prompt(
        node_title=node_title,
        content=content,
        current_word_count=current,
        target_word_count=target_word_count,
        direction=direction,
    )
    temperature, max_tokens = resolve_runtime_params(
        "writing.length_adjust",
        0.2,
        compute_section_max_tokens(
            target_word_count,
            registry_max_tokens=settings.WRITING_MAX_SECTION_TOKENS,
        ),
    )

    try:
        adjusted = await composer.llm_client.chat(
            user_message=user_msg,
            system_message=system_msg,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as err:
        logger.warning(
            "[长度闭环] LLM 调用失败 node={}: {}", node_title, err,
        )
        _record_length_degradation(
            node_id,
            f"adjust_llm_failed:{type(err).__name__}:{str(err)[:80]}",
        )
        return content

    adjusted = (adjusted or "").strip()
    if not adjusted:
        _record_length_degradation(node_id, "adjust_empty_output")
        return content

    normalized = normalize_section_html(adjusted, node_title)
    new_count = count_plain_text(normalized)
    new_ratio = new_count / float(target_word_count) if target_word_count else 0.0

    # 偏差未改善：保留原稿
    if abs(new_ratio - 1.0) >= abs(ratio - 1.0):
        logger.warning(
            "[长度闭环] 重生成未改善偏差 node={} before_ratio={:.2f} after_ratio={:.2f}，保留原稿",
            node_title, ratio, new_ratio,
        )
        _record_length_degradation(
            node_id,
            f"adjust_no_improvement:before={ratio:.2f}->after={new_ratio:.2f}",
        )
        return content

    logger.info(
        "[长度闭环] 重生成生效 node={} {}: {} -> {} 字 (ratio {:.2f}->{:.2f})",
        node_title, direction, current, new_count, ratio, new_ratio,
    )
    return normalized
