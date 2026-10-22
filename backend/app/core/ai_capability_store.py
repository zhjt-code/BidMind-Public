"""模型能力档案持久化 (P0-C)

职责: 通过 ai_settings 公共读写函数更新配置的 capabilities 字段
     (declared 覆盖与 observed 探测结果入库)
不做: 不定义能力语义(归 ai_capabilities)、不执行探测(归 health_probe)
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from app.core.ai_capabilities import (
    ModelCapabilityProfile,
    merge_probe_reports,
)


async def update_declared_capabilities(
    purpose: str, config_id: str, declared: dict[str, Any],
) -> ModelCapabilityProfile:
    """更新管理员声明的能力字段(保留既有 observed 结果)。

    declared 仅接受 ModelCapabilityProfile 已定义字段; observed/observed_at
    由探测链路专管, 显式忽略防止前端伪造实测结论。
    """
    from app.core.ai_settings import (
        _sync_to_runtime,
        load_ai_settings,
        publish_runtime_invalidate,
        save_ai_settings,
    )

    current = load_ai_settings()
    config = current.get_config_by_id(purpose, config_id)  # type: ignore[arg-type]
    if not config:
        raise ValueError(f"配置不存在: {purpose}/{config_id}")

    profile = config.capabilities or ModelCapabilityProfile()
    allowed = set(ModelCapabilityProfile.model_fields.keys()) - {
        "observed", "observed_at",
    }
    payload = {k: v for k, v in declared.items() if k in allowed}
    updated = profile.model_copy(update=payload)
    # 走一次 Pydantic 校验, 拒绝非法枚举/越界数值
    config.capabilities = ModelCapabilityProfile(**updated.model_dump())

    await save_ai_settings(current)
    _sync_to_runtime(current, purpose=purpose)  # type: ignore[arg-type]
    await publish_runtime_invalidate(purpose=purpose, reload_dispatcher=True)
    logger.info(
        f"能力档案已更新(declared): {purpose}/{config_id}, fields={sorted(payload)}"
    )
    return config.capabilities


async def record_probe_results(
    purpose: str, config_id: str, reports: dict,
) -> ModelCapabilityProfile:
    """把 health_probe 探测报告写入配置的 observed 能力(带时间戳)"""
    from app.core.ai_settings import (
        _sync_to_runtime,
        load_ai_settings,
        publish_runtime_invalidate,
        save_ai_settings,
    )

    current = load_ai_settings()
    config = current.get_config_by_id(purpose, config_id)  # type: ignore[arg-type]
    if not config:
        raise ValueError(f"配置不存在: {purpose}/{config_id}")

    config.capabilities = merge_probe_reports(config.capabilities, reports)

    await save_ai_settings(current)
    _sync_to_runtime(current, purpose=purpose)  # type: ignore[arg-type]
    await publish_runtime_invalidate(purpose=purpose, reload_dispatcher=True)
    failed = [k for k, r in reports.items() if not getattr(r, "ok", False)]
    logger.info(
        f"能力档案已更新(observed): {purpose}/{config_id}, "
        f"probes={sorted(reports)}, failed={failed or '无'}"
    )
    return config.capabilities


def get_capability_profile(
    purpose: str, config_id: str,
) -> Optional[ModelCapabilityProfile]:
    """只读获取某配置的能力档案(不存在返回 None)"""
    from app.core.ai_settings import load_ai_settings

    current = load_ai_settings()
    config = current.get_config_by_id(purpose, config_id)  # type: ignore[arg-type]
    return config.capabilities if config else None
