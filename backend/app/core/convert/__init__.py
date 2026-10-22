"""
职责: 导出 Office→PDF 转换与上传格式契约
不做: 解析、门禁、任务编排
"""

from app.core.convert.office_to_pdf import (
    ALLOWED_UPLOAD_MIME_TYPES,
    ALLOWED_UPLOAD_SUFFIXES,
    OFFICE_SUFFIXES,
    UPLOAD_FORMAT_ERROR,
    convert_office_to_pdf,
    converted_pdf_path,
    normalize_office_path_for_parse,
    office_convert_siblings,
    sniff_upload_magic,
    suffix_of,
)

__all__ = [
    "ALLOWED_UPLOAD_MIME_TYPES",
    "ALLOWED_UPLOAD_SUFFIXES",
    "OFFICE_SUFFIXES",
    "UPLOAD_FORMAT_ERROR",
    "convert_office_to_pdf",
    "converted_pdf_path",
    "normalize_office_path_for_parse",
    "office_convert_siblings",
    "sniff_upload_magic",
    "suffix_of",
]
