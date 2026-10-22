"""能力路由过滤 (P0-C)

职责: 按调用需求(视觉/结构化/输出预算)以纯函数方式排除不满足能力的通道
不做: 不选优、不熔断、不感知项目绑定、不修改通道状态

fail-open 原则: 若过滤会清空全部候选, 记 WARNING 并放弃过滤,
避免能力档案误配置导致业务断流(fail-closed 由 P0-D 的调用方语义承担)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from loguru import logger

from app.core.ai_capabilities import (
    effective_capability,
    effective_max_output,
)


@dataclass(frozen=True)
class CallRequirements:
    """一次调度调用对模型能力的要求"""

    needs_vision: bool = False
    structured_mode: Optional[str] = None  # None / "json_object" / "json_schema"
    min_output_tokens: Optional[int] = None


def build_requirements(method: str, dispatch_kwargs: dict) -> CallRequirements:
    """从调度方法与参数推导能力要求"""
    needs_vision = method == "chat_with_images"
    structured_mode: Optional[str] = None
    if method in ("chat_json", "chat_with_validation"):
        structured_mode = (
            "json_schema" if dispatch_kwargs.get("json_schema") is not None
            else "json_object"
        )
    raw_tokens = dispatch_kwargs.get("max_tokens")
    min_output = int(raw_tokens) if isinstance(raw_tokens, (int, float)) and raw_tokens else None
    return CallRequirements(
        needs_vision=needs_vision,
        structured_mode=structured_mode,
        min_output_tokens=min_output,
    )


def channel_meets(channel: Any, req: CallRequirements) -> bool:
    """单通道能力判定(仅排除 effective=unsupported 或输出上限不足的通道;
    auto 一律放行, 交由运行时探测/降级学习)"""
    profile = getattr(channel.client, "capability_profile", None)
    if profile is None:
        return True

    if req.needs_vision:
        if effective_capability(profile, "supports_vision") == "unsupported":
            return False

    if req.structured_mode == "json_object":
        if effective_capability(profile, "json_object") == "unsupported":
            return False
    elif req.structured_mode == "json_schema":
        # strict schema 需求: strict 与 json_schema 任一显式不支持即排除
        if effective_capability(profile, "strict_schema") == "unsupported":
            return False

    if req.min_output_tokens:
        cap = effective_max_output(profile)
        if cap is not None and cap < req.min_output_tokens:
            return False

    return True


def capability_excluded_ids(
    channels: Iterable[Any],
    req: CallRequirements,
    *,
    purpose: str = "",
) -> set[int]:
    """返回应排除的通道 id(channel) 集合(供 dispatcher exclude 合并)。

    总闸 LLM_CAPABILITY_ROUTING_ENABLED 关闭或过滤会清空候选时返回空集。
    """
    from app.core.config import settings
    if not getattr(settings, "LLM_CAPABILITY_ROUTING_ENABLED", True):
        return set()

    channel_list = list(channels)
    if not channel_list:
        return set()

    excluded = {id(ch) for ch in channel_list if not channel_meets(ch, req)}
    if not excluded:
        return set()
    if len(excluded) >= len(channel_list):
        logger.warning(
            f"Dispatcher[{purpose}] 能力过滤将清空全部候选"
            f"(needs_vision={req.needs_vision}, structured={req.structured_mode}, "
            f"min_output={req.min_output_tokens}), fail-open 放弃过滤"
        )
        return set()
    logger.debug(
        f"Dispatcher[{purpose}] 能力过滤排除 {len(excluded)}/{len(channel_list)} 个通道"
    )
    return excluded
