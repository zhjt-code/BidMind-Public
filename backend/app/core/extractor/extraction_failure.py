"""提取阶段基础设施失败信号 (P0-D)

职责: 定义"模型调用失败"与"业务未找到"的分离契约(信号类型/落库标记/响应构造)
不做: 不做重试、不做审查结论(审查侧按标记走人工复核)

背景(根治推广): 超时/配额耗尽/预算耗尽/schema 失败等基础设施故障,
此前与"投标文件确实没有响应"一样落库为 "[未找到响应]", 审查侧按
"未响应=可能不符合"推定, 把模型故障伪装成业务结论。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.bid_response import BidResponse

# 落库内容标记: 与 "[未找到响应]"(业务缺失)严格区分
EXTRACTION_FAILED_CONTENT = "[提取失败待复核]"


@dataclass(frozen=True)
class ExtractionFailedSignal:
    """LLM 提取因基础设施故障未产出结论的显式信号(非业务 not-found)"""

    reason: str  # timeout / retry_exhausted / quota_or_transport 等

    # 显式声明为 falsy 语义不适用——调用方必须 isinstance 判断, 不得当作 ExtractedBidResponse
    found: bool = False


def is_extraction_failed_content(content: str | None) -> bool:
    return (content or "").strip() == EXTRACTION_FAILED_CONTENT


def build_extraction_failed_response(
    requirement_id: int,
    bid_document_id: int,
    *,
    reason: str,
) -> BidResponse:
    """构造"提取失败待复核"响应记录(替代伪装的 [未找到响应])"""
    return BidResponse(
        requirement_id=requirement_id,
        document_id=bid_document_id,
        source_chunk_id=None,
        content=EXTRACTION_FAILED_CONTENT,
        evidence_type=None,
        source_pages=None,
        completeness=None,
        missing_elements=None,
        linked_to=None,
        evidence_anchor=None,
        relevance_note=f"模型调用失败({reason})，未完成提取，需人工核实响应情况",
    )
