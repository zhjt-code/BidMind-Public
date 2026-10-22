"""门禁校验契约

职责: 定义文档门禁校验的输出契约(Pydantic模型与verdict取值)。
不做: 不实现采样与打分逻辑，不依赖pdfplumber等解析库。
"""

from typing import Literal

from pydantic import BaseModel, Field

# pass: 具备完整招标/投标文件的相关性与结构证据，直接放行
# suspect: 可提取文本但缺少关键要素或完整结构，提示用户确认
# unreadable: 前N页几乎无可提取文本(疑似扫描件/图片型PDF)，无法判定
# encrypted / corrupt / empty: 可用性预检(上传快速门禁，不阻断)
GateVerdict = Literal[
    "pass",
    "suspect",
    "unreadable",
    "encrypted",
    "corrupt",
    "empty",
]


class DocGateResult(BaseModel):
    """门禁校验结果(同时作为API返回结构与documents.gate_result落库JSON)"""

    verdict: GateVerdict = Field(..., description="门禁结论: pass/suspect/unreadable/encrypted/corrupt/empty")
    score: float = Field(..., ge=0, le=100, description="要素得分(0-100)")
    doc_type: str = Field(..., description="校验所依据的文档类型: tender/bid")
    hit_features: list[str] = Field(default_factory=list, description="命中的要素(用户可读)")
    missing_features: list[str] = Field(default_factory=list, description="缺失的关键要素(用户可读)")
    reasons: list[str] = Field(default_factory=list, description="面向用户的怀疑理由，前端直接渲染")
    sampled_pages: int = Field(..., ge=0, description="实际采样页数")
    page_count: int = Field(..., ge=0, description="文档总页数")
    toc_entry_count: int = Field(default=0, ge=0, description="可信目录条目数")
    has_confident_toc: bool = Field(default=False, description="是否识别到可信目录")
    has_long_document_fallback: bool = Field(
        default=False,
        description="是否满足无目录长文档跨页强特征兜底",
    )
    engine: str = Field(default="rule", description="判定引擎标识")
