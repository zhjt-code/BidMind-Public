"""投标响应 Pydantic 模型

定义投标响应提取的输入输出数据结构。
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# 证明材料类型
EvidenceTypeValue = Literal["certificate", "proposal", "commitment", "reference", "other"]


class ExtractedBidResponse(BaseModel):
    """LLM 提取的单条投标响应
    
    这是 LLM 输出的原始格式，用于校验 LLM 响应。
    """
    requirement_id: int = Field(
        ...,
        description="对应的招标要求ID"
    )
    content: str = Field(
        ...,
        min_length=5,
        description="响应内容摘要"
    )
    evidence_type: EvidenceTypeValue = Field(
        ...,
        description="证明材料类型"
    )
    source_pages: list[int] = Field(
        default_factory=list,
        description="来源页码列表"
    )
    found: bool = Field(
        default=True,
        description="是否找到响应"
    )
    completeness: Optional[Literal["full", "partial", "form_only"]] = Field(
        default=None,
        description="响应完整性"
    )
    missing_elements: list[str] = Field(
        default_factory=list,
        description="缺失要素列表"
    )

    @field_validator("source_pages", "missing_elements", mode="before")
    @classmethod
    def coerce_list_fields(cls, v):
        return v if isinstance(v, list) else []

    linked_to: Optional[int] = Field(
        default=None,
        description="关联到主响应的requirement_id"
    )
    evidence_anchor: Optional[str] = Field(
        default=None,
        description="证据锚点(合同号/项目名等)"
    )
    relevance_score: Optional[int] = Field(
        default=None,
        ge=0,
        le=100,
        description="响应内容与招标要求的领域关联度(0-100)"
    )
    relevance_note: Optional[str] = Field(
        default=None,
        description="关联性说明(关联性不足时必填)"
    )


class ExtractedBidResponseNotFound(BaseModel):
    """未找到响应的结构"""
    requirement_id: int = Field(
        ...,
        description="对应的招标要求ID"
    )
    content: Optional[str] = Field(
        default=None,
        description="响应内容（未找到时为空）"
    )
    evidence_type: Optional[EvidenceTypeValue] = Field(
        default=None,
        description="证明材料类型（未找到时为空）"
    )
    found: bool = Field(
        default=False,
        description="是否找到响应"
    )


class BidResponseCreate(BaseModel):
    """创建投标响应的请求模型"""
    requirement_id: int = Field(
        ...,
        description="对应的招标要求ID"
    )
    document_id: int = Field(
        ...,
        description="投标文档ID"
    )
    content: str = Field(
        ...,
        min_length=5,
        description="响应内容"
    )
    evidence_type: Optional[EvidenceTypeValue] = Field(
        default=None,
        description="证明材料类型"
    )
    source_chunk_id: Optional[int] = Field(
        default=None,
        description="来源分块ID"
    )
    source_pages: Optional[str] = Field(
        default=None,
        description="来源页码（逗号分隔）"
    )
    completeness: Optional[str] = Field(default=None, description="响应完整性")
    missing_elements: Optional[str] = Field(default=None, description="缺失要素(JSON字符串)")
    linked_to: Optional[int] = Field(default=None, description="关联到主响应")
    evidence_anchor: Optional[str] = Field(default=None, description="证据锚点")


class BidResponseResponse(BaseModel):
    """投标响应的响应模型"""
    id: int = Field(..., description="响应ID")
    requirement_id: int = Field(..., description="招标要求ID")
    document_id: int = Field(..., description="投标文档ID")
    content: str = Field(..., description="响应内容")
    evidence_type: Optional[str] = Field(default=None, description="证明材料类型")
    source_pages: Optional[str] = Field(default=None, description="来源页码")
    source_chunk_id: Optional[int] = Field(default=None, description="来源分块ID")
    completeness: Optional[str] = Field(default=None, description="响应完整性")
    missing_elements: Optional[str] = Field(default=None, description="缺失要素")
    linked_to: Optional[int] = Field(default=None, description="关联主响应")
    evidence_anchor: Optional[str] = Field(default=None, description="证据锚点")
    relevance_score: Optional[int] = Field(default=None, description="领域关联度(0-100)")
    relevance_note: Optional[str] = Field(default=None, description="关联性说明")
    
    class Config:
        from_attributes = True


class BidResponseListResponse(BaseModel):
    """投标响应列表响应模型"""
    total: int = Field(..., description="总数")
    found_count: int = Field(..., description="已找到响应数量")
    not_found_count: int = Field(..., description="未找到响应数量")
    items: list[BidResponseResponse] = Field(
        default_factory=list,
        description="响应列表"
    )


class BidExtractionStatus(BaseModel):
    """投标响应提取状态"""
    project_id: int = Field(..., description="项目ID")
    bid_document_id: int = Field(..., description="投标文档ID")
    total_requirements: int = Field(..., description="招标要求总数")
    extracted_count: int = Field(default=0, description="已提取数量")
    status: Literal["pending", "processing", "completed", "failed"] = Field(
        ...,
        description="提取状态"
    )
    message: Optional[str] = Field(default=None, description="状态消息")
