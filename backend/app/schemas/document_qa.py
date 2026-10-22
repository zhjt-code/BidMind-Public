"""文档质检 API 数据契约

请求体校验模型; 响应经 ApiResponse.data 透传 read_service 构建的 dict。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# 人工处置合法值(与 models.document_qa 列约定一致)
DispositionType = Literal["pending", "confirmed", "false_positive", "ignored"]


class FindingDispositionRequest(BaseModel):
    """发现项处置请求(乐观锁: expected_version 不匹配返回 409)"""

    disposition: DispositionType = Field(..., description="处置结论")
    note: Optional[str] = Field(default=None, max_length=500, description="处置备注")
    expected_version: int = Field(
        ..., ge=0, description="客户端读取到的 disposition_version"
    )
