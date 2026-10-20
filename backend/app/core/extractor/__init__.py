"""信息提取模块

本模块负责两大提取任务：
1. 招标要求提取：从招标文件中提取结构化要求
2. 投标响应提取：从投标文件中提取对应的响应内容

流程定位：
    招标文件 → 【招标要求提取】 → 人工确认要求（必须）
                                        ↓
    投标文件 → 【投标响应提取】 → 【AI对比审查】 → 审查结果
               （AI自动执行，无需人工确认）
"""

from app.core.extractor.tender_extractor import (
    TenderExtractor,
    get_tender_extractor,
)
from app.core.extractor.bid_extractor import (
    BidExtractor,
    get_bid_extractor,
)

__all__ = [
    # 招标要求提取
    "TenderExtractor",
    "get_tender_extractor",
    # 投标响应提取
    "BidExtractor",
    "get_bid_extractor",
]
