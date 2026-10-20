"""知识库 API Schema。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.models.kb_document import (
    KnowledgeAuthority,
    KnowledgeFlow,
    KnowledgeOwnerScope,
    KnowledgeReviewStatus,
)


class KnowledgeDocumentBase(BaseModel):
    """知识资料通用字段。"""

    title: str = Field(..., min_length=1, max_length=500)
    applicable_flows: list[KnowledgeFlow] = Field(default_factory=list)
    business_type: str = Field(..., min_length=1, max_length=64)
    business_label: Optional[str] = Field(default=None, max_length=100)
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    source: Optional[str] = Field(default=None, max_length=255)
    authority: KnowledgeAuthority = KnowledgeAuthority.user_experience
    enabled: bool = True
    version: str = Field(default="1", min_length=1, max_length=64)
    owner_scope: KnowledgeOwnerScope = KnowledgeOwnerScope.personal
    industry_tags: list[str] = Field(default_factory=list)
    region_tags: list[str] = Field(default_factory=list)
    review_status: KnowledgeReviewStatus = KnowledgeReviewStatus.approved

    @field_validator("applicable_flows", mode="before")
    @classmethod
    def normalize_flows(cls, value: object) -> list[str]:
        """允许表单传逗号分隔字符串。"""
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value if isinstance(value, list) else []

    @field_validator("industry_tags", "region_tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: object) -> list[str]:
        """允许表单传逗号分隔标签。"""
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value if isinstance(value, list) else []


class KnowledgeDocumentUpdate(BaseModel):
    """更新知识资料元数据。"""

    title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    applicable_flows: Optional[list[KnowledgeFlow]] = None
    business_type: Optional[str] = Field(default=None, min_length=1, max_length=64)
    business_label: Optional[str] = Field(default=None, max_length=100)
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    source: Optional[str] = Field(default=None, max_length=255)
    authority: Optional[KnowledgeAuthority] = None
    enabled: Optional[bool] = None
    version: Optional[str] = Field(default=None, min_length=1, max_length=64)
    owner_scope: Optional[KnowledgeOwnerScope] = None
    industry_tags: Optional[list[str]] = None
    region_tags: Optional[list[str]] = None
    review_status: Optional[KnowledgeReviewStatus] = None
    superseded_by_id: Optional[int] = None


class KnowledgeDocumentResponse(BaseModel):
    """知识资料响应。"""

    id: int
    owner_id: Optional[int] = None
    title: str
    filename: str
    file_size: int
    mime_type: Optional[str] = None
    page_count: Optional[int] = None
    applicable_flows: list[str]
    business_type: str
    business_label: Optional[str] = None
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    source: Optional[str] = None
    authority: str
    enabled: bool
    version: str
    superseded_by_id: Optional[int] = None
    owner_scope: str
    industry_tags: list[str]
    region_tags: list[str]
    review_status: str
    processing_status: str
    error_message: Optional[str] = None
    processed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class KnowledgeAtomResponse(BaseModel):
    """知识库原子素材响应。"""

    id: int
    document_id: int
    title: str
    business_type: str
    business_label: Optional[str] = None
    summary: Optional[str] = None
    content: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    atom_type: str
    confidence: int
    source_chunk_ids: list[int] = Field(default_factory=list)
    source_image_ids: list[int] = Field(default_factory=list)
    processing_status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class KnowledgeChunkReference(BaseModel):
    """知识库召回来源引用。"""

    kb_doc_id: int
    kb_chunk_id: int
    page_num: Optional[int] = None
    title: str
    authority: str
    version: str
    similarity: float
    content: str


class KnowledgeSearchRequest(BaseModel):
    """知识库检索请求。"""

    query: str = Field(..., min_length=1)
    applicable_flow: Optional[KnowledgeFlow] = None
    business_types: list[str] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1, le=20)
    min_similarity: float = Field(default=0.5, ge=0, le=1)


class KnowledgeSearchResponse(BaseModel):
    """知识库检索响应。"""

    results: list[KnowledgeChunkReference] = Field(default_factory=list)


class KnowledgeProjectionRequest(BaseModel):
    """写标项目引用知识库素材请求。"""

    kb_document_ids: list[int] = Field(..., min_length=1)


class KnowledgeAtomProjectionRequest(BaseModel):
    """写标项目引用知识库原子素材请求。"""

    kb_atom_ids: list[int] = Field(..., min_length=1)


class KnowledgeProjectionResponse(BaseModel):
    """写标项目引用知识库素材响应。"""

    created_material_ids: list[int] = Field(default_factory=list)
    skipped_document_ids: list[int] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class KnowledgeUsagePolicy(str):
    """项目级知识库使用策略字面量。

    bid_review 默认仅作辅助参考，不作为审查事实来源。
    collusion_check 默认仅用于正常相似豁免/误报抑制，不作为串标定性证据。
    """

    auxiliary_only = "auxiliary_only"
    normal_similarity_exemption_only = "normal_similarity_exemption_only"


class ProjectKnowledgeUsageConfig(BaseModel):
    """项目级知识库使用配置。"""

    enabled: bool = False
    flow: Optional[KnowledgeFlow] = None
    selected_kb_document_ids: list[int] = Field(default_factory=list)
    usage_policy: Optional[str] = None
    confirmed_by_user: bool = False
    confirmed_at: Optional[datetime] = None

    @field_validator("selected_kb_document_ids", mode="before")
    @classmethod
    def normalize_ids(cls, value: object) -> list[int]:
        if value is None:
            return []
        if isinstance(value, list):
            result: list[int] = []
            for item in value:
                try:
                    result.append(int(item))
                except (TypeError, ValueError):
                    continue
            return result
        return []


class ProjectKnowledgeUsageUpdate(BaseModel):
    """项目级知识库使用配置更新请求。"""

    enabled: bool = False
    flow: Optional[KnowledgeFlow] = None
    selected_kb_document_ids: list[int] = Field(default_factory=list)
    usage_policy: Optional[str] = None


class ProjectKnowledgeUsageResponse(ProjectKnowledgeUsageConfig):
    """项目级知识库使用配置响应。"""

    pass
