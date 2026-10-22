"""解析产物完整性评估与内容指纹。

职责: 判定 ParsedDocument 是否为"损坏/空壳"文档(fail-visible 闸门依据),
      并计算归一化全文内容指纹(逻辑副本检测)。
不做: 不解析 PDF、不访问数据库、不修改解析产物、不做字节级校验(上传层职责)。

定位(综合提升方案 阶段0.3, 缺口D): 损坏文件此前会以"0个chunk/0个评分项"假成功
流过全链路, 用户只看到空结果; 本模块把"不可用输入"在解析阶段显式拦下。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class DocumentIntegrityReport:
    """解析产物完整性评估结果。"""

    usable: bool
    reason: str
    page_count: int
    char_count: int
    table_count: int
    image_count: int


def assess_parsed_document_integrity(parsed_doc: Any) -> DocumentIntegrityReport:
    """评估解析产物是否可用。

    判定为不可用(usable=False)的条件(任一):
    - 可渲染页数为 0;
    - 全部页面无文本、无表格且无图片(空壳: 加密/损坏/全白页)。

    扫描件(无文本但有图片)判定为可用, 交 OCR/VL 通道处理。
    """
    pages = list(getattr(parsed_doc, "pages", None) or [])
    page_count = int(getattr(parsed_doc, "page_count", 0) or 0) or len(pages)
    char_count = sum(len(getattr(p, "text", "") or "") for p in pages)
    table_count = sum(len(getattr(p, "tables", None) or []) for p in pages)
    image_count = sum(len(getattr(p, "images", None) or []) for p in pages)

    if page_count <= 0 or not pages:
        return DocumentIntegrityReport(
            usable=False,
            reason="PDF无可渲染页面，文件可能已损坏或为空文件",
            page_count=page_count,
            char_count=char_count,
            table_count=table_count,
            image_count=image_count,
        )
    if char_count == 0 and table_count == 0 and image_count == 0:
        return DocumentIntegrityReport(
            usable=False,
            reason="PDF所有页面均无文本、表格与图片，文件可能已损坏、加密或内容为空",
            page_count=page_count,
            char_count=char_count,
            table_count=table_count,
            image_count=image_count,
        )
    return DocumentIntegrityReport(
        usable=True,
        reason="",
        page_count=page_count,
        char_count=char_count,
        table_count=table_count,
        image_count=image_count,
    )


def normalized_content_hash(parsed_doc: Any) -> str | None:
    """计算归一化全文 sha256(剔除全部空白), 无文本时返回 None。

    同一文档经不同 PDF 生成器导出(字节不同)仍产生相同指纹(样本19≈21实证),
    用于逻辑副本检测; 不参与完整性判定。
    """
    pages = getattr(parsed_doc, "pages", None) or []
    joined = "".join(getattr(p, "text", "") or "" for p in pages)
    normalized = _WHITESPACE_RE.sub("", joined)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


__all__ = [
    "DocumentIntegrityReport",
    "assess_parsed_document_integrity",
    "normalized_content_hash",
]
