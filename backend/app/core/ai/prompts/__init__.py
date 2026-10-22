"""Prompt 模板模块

管理 LLM 调用的 Prompt 模板。
"""

from app.core.ai.prompts.base import PromptTemplate
from app.core.ai.prompts.bid_extract import (
    BID_EXTRACT_SYSTEM,
    get_bid_extract_prompt,
)
from app.core.ai.prompts.review import (
    NO_RESPONSE_PROMPT,
    REVIEW_PROMPT,
    REVIEW_SYSTEM_PROMPT,
)
from app.core.ai.prompts.tender_extract import (
    CATEGORY_DESCRIPTIONS,
    get_tender_extract_prompt,
)

__all__ = [
    "PromptTemplate",
    # Review prompts
    "REVIEW_SYSTEM_PROMPT",
    "REVIEW_PROMPT",
    "NO_RESPONSE_PROMPT",
    # Tender extraction prompts
    "get_tender_extract_prompt",
    "CATEGORY_DESCRIPTIONS",
    # Bid extraction prompts
    "get_bid_extract_prompt",
    "BID_EXTRACT_SYSTEM",
]
