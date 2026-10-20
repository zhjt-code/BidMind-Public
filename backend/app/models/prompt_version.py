"""Prompt 版本管理模型

支持 Prompt 模板的版本化管理：每个 purpose 可有多个版本，仅一个 is_active。
"""

from sqlalchemy import Boolean, Column, Integer, String, Text, UniqueConstraint

from app.models.base import BaseModel


class PromptVersion(BaseModel):
    """Prompt 版本

    Attributes:
        purpose: 用途标识 (tender_extract / bid_extract / review / collusion_extract / collusion_compare / collusion_synthesis)
        version: 版本号（同 purpose 下递增）
        name: 版本名称（便于管理员识别）
        content: Prompt 模板文本
        is_active: 是否为当前激活版本（同 purpose 下最多一个）
    """

    __tablename__ = "prompt_versions"

    purpose = Column(String(50), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    name = Column(String(200), nullable=True)
    content = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=False, index=True)

    __table_args__ = (
        UniqueConstraint("purpose", "version", name="uq_prompt_purpose_version"),
    )
