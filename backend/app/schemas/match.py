"""招标-投标匹配 Pydantic 模型

定义匹配结果的数据结构。
"""

from typing import Optional

from pydantic import BaseModel, Field


class MatchedPairResponse(BaseModel):
    """匹配对响应模型"""
    requirement_id: int = Field(..., description="招标要求ID")
    requirement_category: str = Field(..., description="要求分类")
    requirement_description: str = Field(..., description="要求描述")
    is_mandatory: bool = Field(..., description="是否为否决项")
    
    response_id: Optional[int] = Field(default=None, description="投标响应ID")
    response_content: Optional[str] = Field(default=None, description="响应内容")
    response_evidence_type: Optional[str] = Field(default=None, description="证明材料类型")
    
    has_response: bool = Field(..., description="是否有响应")


class MatchSummaryResponse(BaseModel):
    """匹配汇总响应模型"""
    total: int = Field(..., description="总匹配数")
    with_response: int = Field(..., description="有响应数")
    without_response: int = Field(..., description="无响应数")
    match_rate: float = Field(..., description="匹配率(0-1)")
    
    mandatory_total: int = Field(..., description="否决项总数")
    mandatory_matched: int = Field(..., description="否决项有响应数")
    mandatory_unmatched: int = Field(..., description="否决项无响应数")


class MatchResultResponse(BaseModel):
    """匹配结果完整响应模型"""
    project_id: int = Field(..., description="项目ID")
    summary: MatchSummaryResponse = Field(..., description="匹配汇总")
    pairs: list[MatchedPairResponse] = Field(
        default_factory=list,
        description="匹配对列表"
    )
    
    # 高风险项
    unmatched_mandatory: list[MatchedPairResponse] = Field(
        default_factory=list,
        description="无响应的否决项（高风险）"
    )
