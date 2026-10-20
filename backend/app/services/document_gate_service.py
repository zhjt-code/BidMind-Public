"""文档门禁服务

在上传落盘后、解析开始前，对招标/投标PDF做轻量要素校验。
校验失败时fail-open(不阻断上传)，判定原则为宁松勿严。
"""

import asyncio
from typing import Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from pathlib import Path

from app.core.config import settings
from app.core.parser.doc_gate import (
    DocGateResult,
    assess_pdf_usability,
    build_usability_gate_result,
    evaluate_doc_gate,
    sample_pdf_bytes,
)
from app.models.document import Document, DocumentType

# 门禁只校验招标/投标文档，素材与参考标书不适用
_GATED_DOC_TYPES = frozenset({DocumentType.TENDER, DocumentType.BID})


class DocumentGateService:
    """文档门禁服务

    职责: 编排上传文档的门禁校验(可用性预检→轻量采样→规则打分→结果落库)，供上传路由在解析前调用。
    不做: 不做完整解析/分块/向量化，不阻断上传流程，不修改项目状态，不调用LLM，不做计费。
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def evaluate_uploaded_document(
        self,
        document: Document,
        file_content: bytes,
    ) -> Optional[DocGateResult]:
        """对刚上传的文档执行门禁校验并落库

        Args:
            document: 已落库的文档记录
            file_content: 上传的PDF字节内容

        Returns:
            DocGateResult: 校验结果; 门禁关闭/类型不适用/校验异常时返回None(fail-open)
        """
        if not settings.DOC_GATE_ENABLED:
            return None
        if document.doc_type not in _GATED_DOC_TYPES:
            return None
        source_name = document.file_path or document.filename or ""
        if Path(source_name).suffix.lower() != ".pdf":
            return None

        try:
            # 1) 可用性预检(加密/损坏/空壳) — 同步CPU，放线程池
            usability = await asyncio.to_thread(assess_pdf_usability, file_content)
            if usability.kind is not None:
                result = build_usability_gate_result(usability, document.doc_type.value)
            else:
                # 2) 合规要素采样打分
                sample = await asyncio.to_thread(sample_pdf_bytes, file_content)
                result = evaluate_doc_gate(sample, document.doc_type.value)
        except Exception as e:
            # fail-open: 门禁自身异常不影响上传主流程，完整解析阶段自会暴露文件问题
            logger.warning(
                f"文档门禁校验失败(fail-open放行): document_id={document.id}, error={e}"
            )
            return None

        document.gate_verdict = result.verdict
        document.gate_result = result.model_dump()
        document.gate_ack = False
        await self.db.flush()

        return result

    async def acknowledge_gate(self, document: Document) -> None:
        """记录用户"接受警告继续试用"的确认(留痕，不改变任何流程行为)"""
        if document.gate_ack:
            return
        document.gate_ack = True
        await self.db.flush()
        logger.info(
            f"门禁警告已确认: document_id={document.id}, verdict={document.gate_verdict}"
        )