"""标书素材匹配子包。

职责: 承载素材—章节匹配的绑定持久化、语义画像、类型门禁、句子召回与
      LLM 蕴含裁决等能力，供 material_matcher 薄接入。
不做: 不承担 HTTP 请求处理、不直接渲染文档、不管理任务调度。
"""

from app.core.writer.material_matching.contracts import (
    MaterialBindingProposal,
    MaterialMatchingRequest,
)
from app.core.writer.material_matching.pipeline import match_materials_to_chapters

__all__ = [
    "MaterialMatchingRequest",
    "MaterialBindingProposal",
    "match_materials_to_chapters",
]
