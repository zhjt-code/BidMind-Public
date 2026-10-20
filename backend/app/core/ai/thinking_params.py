"""思考/seed 请求参数整形 (P0-C)

职责: 按 provider 与能力档案生成思考(thinking/reasoning)扩展参数,
     并判定是否允许下发 seed
不做: 不判断业务是否应该思考(那是节点/能力档案的声明)、不发网络请求

厂商映射(2026-08 官方文档核验):
- qwen(dashscope):    extra_body.enable_thinking = true/false
- volcengine(方舟):    extra_body.thinking = {"type": "enabled"/"disabled"}
- deepseek:           extra_body.thinking = {"type": ...} + reasoning_effort(low/high/max)
- tencent(TokenHub):  reasoning_effort(low/medium/high), 经 extra_body 注入请求体顶层
- aihubmix(含备用域): reasoning_effort(low/medium/high)
- baidu/unknown/local: 不下发任何思考参数(auto 行为)

策略为 auto 且强度为 default 时不发送任何参数, 保持服务端默认(现状)。
"""

from __future__ import annotations

from typing import Any, Optional

from app.core.ai_capabilities import (
    ModelCapabilityProfile,
    effective_capability,
)

_EFFORT_CLAMP = {
    "deepseek": {"low": "low", "medium": "high", "high": "high", "max": "max"},
    "tencent": {"low": "low", "medium": "medium", "high": "high", "max": "high"},
    "aihubmix": {"low": "low", "medium": "medium", "high": "high", "max": "high"},
}


def build_thinking_extra_body(
    provider: str,
    profile: Optional[ModelCapabilityProfile],
) -> dict[str, Any]:
    """生成思考参数(合并进 extra_body); 无需干预时返回 {}"""
    if profile is None:
        return {}
    policy = profile.thinking_policy
    effort = profile.reasoning_effort
    body: dict[str, Any] = {}

    if provider == "qwen":
        if policy == "disabled":
            body["enable_thinking"] = False
        elif policy == "enabled":
            body["enable_thinking"] = True
    elif provider == "volcengine":
        if policy in ("disabled", "enabled"):
            body["thinking"] = {"type": policy}
    elif provider == "deepseek":
        if policy in ("disabled", "enabled"):
            body["thinking"] = {"type": policy}
        if effort != "default":
            body["reasoning_effort"] = _EFFORT_CLAMP["deepseek"].get(effort, "high")
    elif provider in ("tencent", "aihubmix"):
        if effort != "default":
            body["reasoning_effort"] = _EFFORT_CLAMP[provider].get(effort, "high")

    return body


def build_client_extra_kwargs(client: Any) -> dict[str, Any]:
    """给 completions.create 生成 extra_body 扩展(CloudClient 三个构造点调用)"""
    profile = getattr(client, "capability_profile", None)
    if profile is None:
        return {}
    try:
        from app.core.ai.dispatcher import detect_provider
        provider = detect_provider(getattr(client, "base_url", "") or "")
    except Exception:
        return {}
    body = build_thinking_extra_body(provider, profile)
    return {"extra_body": body} if body else {}


def seed_allowed(client: Any) -> bool:
    """能力档案显式声明不支持 seed 时省略该参数(避免 400)"""
    profile = getattr(client, "capability_profile", None)
    if profile is None:
        return True
    return effective_capability(profile, "supports_seed") != "unsupported"
