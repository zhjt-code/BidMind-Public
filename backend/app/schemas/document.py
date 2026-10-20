"""文档 Schema 定义

定义文档上传和响应的数据模型。
"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

from app.core.parser.doc_gate.contracts import DocGateResult
from app.models.document import DocumentType


class DocumentUploadResponse(BaseModel):
    """文档上传响应"""
    id: int = Field(..., description="文档ID")
    project_id: int = Field(..., description="项目ID")
    doc_type: DocumentType = Field(..., description="文档类型")
    bid_part: Optional[str] = Field(
        default=None, description="投标信封卷角色(None=整本/commercial/technical)"
    )
    filename: str = Field(..., description="文件名")
    file_size: int = Field(..., description="文件大小(字节)")
    message: str = Field(default="上传成功", description="状态消息")
    gate_result: Optional[DocGateResult] = Field(
        default=None, description="文档门禁校验结果(未启用或不适用时为None)"
    )
    
    class Config:
        from_attributes = True


class DocumentResponse(BaseModel):
    """文档详情响应"""
    id: int = Field(..., description="文档ID")
    project_id: int = Field(..., description="项目ID")
    doc_type: DocumentType = Field(..., description="文档类型")
    bid_part: Optional[str] = Field(
        default=None, description="投标信封卷角色(None=整本/commercial/technical)"
    )
    filename: str = Field(..., description="文件名")
    file_path: str = Field(..., description="存储路径")
    file_size: int = Field(..., description="文件大小(字节)")
    page_count: Optional[int] = Field(default=None, description="页数")
    parsed: int = Field(..., description="是否已解析(0/1)")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")
    
    class Config:
        from_attributes = True


class DocumentListResponse(BaseModel):
    """文档列表响应"""
    tender: Optional[DocumentResponse] = Field(default=None, description="招标文件")
    bid: Optional[DocumentResponse] = Field(default=None, description="投标文件（向后兼容，取第一份 BID）")
    bids: List[DocumentResponse] = Field(default_factory=list, description="全部投标文件列表（串标项目使用）")


class ProcessingStatus(BaseModel):
    """处理状态响应"""
    document_id: int = Field(..., description="文档ID")
    status: str = Field(..., description="处理状态")
    progress: int = Field(default=0, ge=0, le=100, description="处理进度(0-100)")
    message: Optional[str] = Field(default=None, description="状态消息")
    chunk_count: Optional[int] = Field(default=None, description="分块数量")


class ProcessingResult(BaseModel):
    """离线处理结果"""
    document_id: int = Field(..., description="文档ID")
    success: bool = Field(..., description="是否成功")
    page_count: int = Field(default=0, description="页数")
    chunk_count: int = Field(default=0, description="分块数量")
    message: str = Field(default="处理完成", description="结果消息")
    error: Optional[str] = Field(default=None, description="错误信息")
