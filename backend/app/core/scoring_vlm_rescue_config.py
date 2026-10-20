"""VLM 视觉救援通道专用配置——独立于 app/core/config.py。

职责: 提供评分 VLM 救援通道的 BaseSettings（模式/渲染/预算/超时），
      导出 scoring_vlm_rescue_settings 单例供生产使用。
不做: 不修改 app/core/config.py、不暴露敏感凭据、不调用 LLM、不访问数据库。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScoringVlmRescueSettings(BaseSettings):
    """评分 VLM 视觉救援通道配置（与主配置文件隔离）。

    职责: 承载 VLM 救援所需全部可调参数，支持 .env 或环境变量注入。
    不做: 不包含 API Token、数据库连接串或其他主配置字段。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # 直接投产裁决(2026-08-12): 默认 enforce；off 为单变量回滚，无 DB 迁移
    SCORING_VLM_RESCUE_MODE: Literal["off", "enforce"] = "enforce"
    SCORING_VLM_RESCUE_DPI: int = Field(
        default=200, gt=0, description="评分区页面渲染分辨率"
    )
    SCORING_VLM_RESCUE_PAGE_MARGIN: int = Field(
        default=1, ge=0, description="评分区页码范围前后扩展页数( 续表/前置声明证据)"
    )
    SCORING_VLM_RESCUE_MAX_PAGES: int = Field(
        default=30, gt=0, description="单文档最大渲染页数, 超出截断并记诊断"
    )
    SCORING_VLM_RESCUE_MAX_PAGES_PER_CALL: int = Field(
        default=8, gt=0, description="单次 VLM 调用最大图片页数"
    )
    SCORING_VLM_RESCUE_MAX_TOKENS: int = Field(
        default=6000, gt=0, description="单次 VLM 调用最大输出 token 数"
    )
    SCORING_VLM_RESCUE_TIMEOUT_S: float = Field(
        default=300.0, gt=0, description="单次 VLM 调用超时秒数"
    )
    SCORING_VLM_RESCUE_REPAIR_ATTEMPTS: int = Field(
        default=1, ge=0,
        description="(已废弃, 由 MAX_ROUNDS 取代)门禁失败后携反馈重询次数上限",
    )
    SCORING_VLM_RESCUE_MAX_ROUNDS: int = Field(
        default=3, ge=1,
        description="verdict 终点重试总轮数上限(阶段1: 未完整闭合即携缺口反馈重询, 跨轮取优)",
    )


scoring_vlm_rescue_settings = ScoringVlmRescueSettings()

__all__ = ["ScoringVlmRescueSettings", "scoring_vlm_rescue_settings"]
