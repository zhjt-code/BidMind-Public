"""L3 完整候选提取专用配置——独立于 app/core/config.py。

职责: 提供 L3 专用 BaseSettings，通过环境变量控制完整候选提取的模式、预算与超时；
      导出 scoring_full_candidate_settings 单例供生产使用。
不做: 不修改 app/core/config.py、不暴露敏感凭据、不调用 LLM、不访问数据库。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScoringFullCandidateSettings(BaseSettings):
    """L3 完整候选提取专用配置（与主配置文件隔离）。

    职责: 承载 L3 完整候选提取所需的全部可调参数，支持通过 .env 或环境变量注入。
    不做: 不包含 API Token、数据库连接串或其他主配置字段。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    SCORING_FULL_CANDIDATE_MODE: Literal["off", "shadow", "enforce"] = "shadow"
    SCORING_FULL_CANDIDATE_CONTEXT_CHAR_BUDGET: int = Field(
        default=24000, gt=0, description="L3 上下文字符预算，必须 >0"
    )
    SCORING_FULL_CANDIDATE_MAX_TOKENS: int = Field(
        default=5000, gt=0, description="L3 最大输出 token 数，必须 >0"
    )
    SCORING_FULL_CANDIDATE_NEIGHBOR_CHUNKS: int = Field(
        default=2, ge=0, description="每侧邻近 chunk 限制（≥0，0 表示不包含邻居）"
    )
    SCORING_FULL_CANDIDATE_MAX_SCOPES: int = Field(
        default=3, gt=0, description="单次请求最大 scope 数量，必须 >0"
    )
    SCORING_FULL_CANDIDATE_TIMEOUT_S: float = Field(
        default=180.0, gt=0, description="L3 调用超时秒数，必须 >0"
    )

scoring_full_candidate_settings = ScoringFullCandidateSettings()

__all__ = ["ScoringFullCandidateSettings", "scoring_full_candidate_settings"]
