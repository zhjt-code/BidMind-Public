"""PDF 可用性快速预检。

职责: 在合规门禁打分前，用 PyMuPDF 轻量判定加密/损坏/空壳。
不做: 不做完整 pdfplumber 解析、不调用 LLM/OCR、不阻断上传、不收集密码解密。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import fitz
from loguru import logger

from app.core.parser.doc_gate.contracts import DocGateResult

UsabilityKind = Literal["encrypted", "corrupt", "empty"]

_DOC_TYPE_NAMES = {"tender": "招标", "bid": "投标"}

_REASON_BY_KIND: dict[UsabilityKind, str] = {
    "encrypted": "文档已加密，系统无法读取内容。请提供未加密的 PDF 后重新上传",
    "corrupt": "文档无法正常打开，文件可能已损坏或不是有效的 PDF",
    "empty": "PDF所有页面均无文本、表格与图片，文件可能已损坏、加密或内容为空",
}


@dataclass(frozen=True)
class UsabilityAssessment:
    """可用性预检结果。kind=None 表示可继续走合规打分。"""

    kind: Optional[UsabilityKind]
    page_count: int


def assess_pdf_usability(content: bytes) -> UsabilityAssessment:
    """评估上传 PDF 是否在合规打分前就应提示可用性风险。

    判定顺序: encrypted → corrupt → empty；均未命中则 kind=None。
    扫描件(无文本但有图片)视为可用，交后续 unreadable 通道。
    仅 owner 权限加密且空密码可开时，按可读文档继续。
    """
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception as e:
        logger.debug(f"可用性预检: PDF无法打开: {e}")
        return UsabilityAssessment(kind="corrupt", page_count=0)

    try:
        if doc.needs_pass:
            auth_rc = doc.authenticate("")
            if auth_rc <= 0:
                return UsabilityAssessment(
                    kind="encrypted",
                    page_count=int(doc.page_count or 0),
                )

        page_count = int(doc.page_count or 0)
        if page_count <= 0:
            return UsabilityAssessment(kind="corrupt", page_count=0)

        if _document_has_extractable_content(doc):
            return UsabilityAssessment(kind=None, page_count=page_count)
        return UsabilityAssessment(kind="empty", page_count=page_count)
    except Exception as e:
        logger.debug(f"可用性预检: 读取页面失败: {e}")
        return UsabilityAssessment(kind="corrupt", page_count=0)
    finally:
        doc.close()


def build_usability_gate_result(
    assessment: UsabilityAssessment,
    doc_type: str,
) -> DocGateResult:
    """将可用性预检结果转为 DocGateResult(供落库与前端弹窗)。"""
    if assessment.kind is None:
        raise ValueError("usable assessment cannot build unusable gate result")

    type_name = _DOC_TYPE_NAMES.get(doc_type, "招标/投标")
    reason = _REASON_BY_KIND[assessment.kind]
    reasons = [reason]
    if assessment.kind != "empty":
        reasons.append(
            f"当前文件暂时无法确认为完整的{type_name}文件，建议更换后再继续"
        )

    return DocGateResult(
        verdict=assessment.kind,
        score=0.0,
        doc_type=doc_type,
        reasons=reasons,
        sampled_pages=0,
        page_count=max(assessment.page_count, 0),
        engine="usability",
    )


def _document_has_extractable_content(doc: fitz.Document) -> bool:
    """任一页存在文本、嵌入图或表格即视为有内容。"""
    for page in doc:
        text = (page.get_text() or "").strip()
        if text:
            return True
        if page.get_images():
            return True
        try:
            found = page.find_tables()
        except Exception:
            found = None
        if found is not None and getattr(found, "tables", None):
            if found.tables:
                return True
    return False


__all__ = [
    "UsabilityAssessment",
    "UsabilityKind",
    "assess_pdf_usability",
    "build_usability_gate_result",
]
