"""请求温度决议(模型能力约束 vs 业务节点意图)。

职责: 按用户模型配置归一化一次 chat 请求的 temperature
不做: 不发网络请求、不读设置文件、不推导 LLM Center 节点温度
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

SIGNATURE_DEFAULT_TEMPERATURE = 0.1


@dataclass(frozen=True)
class TemperatureSettings:
    """用户在模型配置中保存的温度缺省值与能力约束。"""

    default_temperature: Optional[float] = None
    mode: str = "free"
    exact: Optional[float] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None


def temperature_settings_from_config(config: object) -> TemperatureSettings:
    """从 ModelConfigItem(或测试替身)读取用户温度字段。"""
    return TemperatureSettings(
        default_temperature=getattr(config, "temperature", None),
        mode=getattr(config, "temperature_mode", None) or "free",
        exact=getattr(config, "temperature_exact", None),
        min_value=getattr(config, "temperature_min", None),
        max_value=getattr(config, "temperature_max", None),
    )


def resolve_request_temperature(
    requested: float,
    settings: Optional[TemperatureSettings] = None,
    *,
    signature_default: float = SIGNATURE_DEFAULT_TEMPERATURE,
) -> float:
    """优先级: exact/clamp(能力约束) > 用户显式默认温度 > 业务节点温度。

 用户裁决: 前端显式设置即最高等级——default_temperature 非空时
 直接压过业务节点温度, 不再要求业务恰好传签名默认(0.1)才生效
 (旧语义使 PDF 救援节点显式传 0.0 时用户设的 1.0 永远失效)。
 signature_default 参数保留仅为 API 兼容, 不再参与判定。
 """
    if settings is None:
        return requested
    resolved = requested
    if settings.default_temperature is not None:
        resolved = settings.default_temperature
    if settings.mode == "exact" and settings.exact is not None:
        return settings.exact
    if settings.mode == "clamp":
        if settings.min_value is not None:
            resolved = max(resolved, settings.min_value)
        if settings.max_value is not None:
            resolved = min(resolved, settings.max_value)
    return resolved
