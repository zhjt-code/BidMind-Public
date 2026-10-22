"""招标要求提取 -- 章节上下文识别子包（P0）。

职责: 为审查侧招标要求提取提供"章节归属"判定（当前仅合同/拟签合同区段识别）。
不做: 不调用 LLM、不访问数据库、不感知 FastAPI；仅做纯文本/章节路径的规则判定。
"""

from app.core.extractor.section_context.contract_classifier import (
    SectionPhase,
    classify_section_phase,
)

__all__ = ["classify_section_phase", "SectionPhase"]
