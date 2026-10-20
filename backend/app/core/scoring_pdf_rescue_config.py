"""评分 chat-PDF 直读救援通道专用配置——独立于 app/core/config.py。

职责: 提供评分 PDF 直读救援通道的 BaseSettings（模式/页窗/预算/超时/方言参数），
      导出 scoring_pdf_rescue_settings 单例供生产使用。
不做: 不修改 app/core/config.py、不暴露敏感凭据、不调用 LLM、不访问数据库。
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScoringPdfRescueSettings(BaseSettings):
    """评分 chat-PDF 直读救援通道配置（与主配置文件隔离）。

    职责: 承载 PDF 直读救援所需全部可调参数，支持 .env 或环境变量注入。
    不做: 不包含 API Token、数据库连接串或其他主配置字段。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # 阶段3(2026-08-31): SCORING_PDF_RESCUE_MODE 与 SCORING_EXTRACTION_MODE 开关
    # 已随确定性链/VLM第二梯队删除, pdf_direct 为唯一主路径。
    SCORING_PRESENCE_GATE_ENABLED: bool = Field(
        default=True,
        description="文档级评分表存在性门禁: 无表则跳过 pdf_direct/VLM 抽取",
    )
    SCORING_PDF_RESCUE_PAGE_MARGIN: int = Field(
        default=1, ge=0, description="评分区页码范围前后扩展页数(与VLM通道同口径)"
    )
    SCORING_PDF_RESCUE_MAX_PAGES: int = Field(
        default=30, gt=0, description="子PDF最大页数, 超出截断并记诊断"
    )
    SCORING_PDF_RESCUE_MAX_PDF_MB: float = Field(
        default=15.0, gt=0, description="子PDF体积上限(MB), 超限跳过本通道转VLM"
    )
    SCORING_PDF_RESCUE_MAX_TOKENS: int = Field(
        default=16000, gt=0,
        description="单次调用最大输出 token 数(: 思考型模型的思考正文"
                    "计入输出预算, 6000 实测被吃满导致 JSONL 零行)",
    )
    SCORING_PDF_RESCUE_MAX_TOKENS_CEILING: int = Field(
        default=32000, gt=0,
        description="自适应升档封顶: 回复非空但解析零项时预算翻倍, 不超过此值",
    )
    SCORING_PDF_RESCUE_TIMEOUT_S: float = Field(
        default=300.0, gt=0, description="单次模型调用超时秒数(含文件上传解析)"
    )
    SCORING_PDF_RESCUE_MAX_ROUNDS: int = Field(
        default=3, ge=1,
        description="verdict 终点重试总轮数上限(与VLM默认对齐, 空回复轮不计入命中)",
    )
    SCORING_PDF_RESCUE_DASHSCOPE_MODEL: str = Field(
        default="qwen-long",
        description="DashScope fileid 方言使用的文档模型(qwen-long / qwen-doc-turbo)",
    )
    SCORING_PDF_RESCUE_EXTRACT_TEXT_MAX_CHARS: int = Field(
        default=120_000, gt=0,
        description="extract_text 方言回填解析文本的最大字符数(防止撑爆上下文)",
    )


scoring_pdf_rescue_settings = ScoringPdfRescueSettings()

__all__ = ["ScoringPdfRescueSettings", "scoring_pdf_rescue_settings"]
