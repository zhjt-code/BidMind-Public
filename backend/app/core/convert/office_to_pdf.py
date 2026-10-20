"""
职责: 识别上传文件格式并将 Office 文档经 Gotenberg 转为 PDF；提供解析前路径归一入口
不做: 文档解析、门禁打分、处理任务状态机、上传路由编排
"""

from __future__ import annotations

from pathlib import Path

import httpx
from loguru import logger

from app.core.config import settings

OFFICE_SUFFIXES = frozenset({".doc", ".docx"})
ALLOWED_UPLOAD_SUFFIXES = frozenset({".pdf", ".doc", ".docx"})
ALLOWED_UPLOAD_MIME_TYPES = frozenset({
    "application/pdf",
    "application/x-pdf",
    "application/octet-stream",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
})

_MAGIC_PREFIXES: dict[str, bytes] = {
    ".pdf": b"%PDF",
    ".docx": b"PK\x03\x04",
    ".doc": bytes.fromhex("D0CF11E0A1B11AE1"),
}

UPLOAD_FORMAT_ERROR = "仅支持 PDF / Word(.doc/.docx)"
# 对外说 3 分钟，与 Gotenberg API_TIMEOUT=180s 对齐；客户端 OFFICE_CONVERT_TIMEOUT_SEC 略长以免抢先断开。
OFFICE_CONVERT_TIMEOUT_USER_MESSAGE = (
    "Word 文档转换超时（超过 3 分钟）。请稍后重试，或先另存为 PDF 再上传。"
)


def suffix_of(filename: str) -> str:
    """返回小写后缀（含点）。无后缀时返回空串。"""
    return Path(filename or "").suffix.lower()


def sniff_upload_magic(content: bytes, suffix: str) -> bool:
    """按扩展名校验文件头魔数。未知后缀返回 False。"""
    prefix = _MAGIC_PREFIXES.get(suffix)
    if prefix is None:
        return False
    return content.startswith(prefix)


def converted_pdf_path(src_path: str | Path) -> Path:
    """Office 原件对应的转换产物路径: <原件路径>.pdf。"""
    return Path(f"{src_path}.pdf")


def office_convert_siblings(file_path: str | Path) -> list[Path]:
    """返回应与主文件一并清理的转换配对路径。

    原件 x.doc / x.docx ↔ 产物 x.doc.pdf / x.docx.pdf。
    原生 PDF 无配对文件。
    """
    path = Path(file_path)
    name_lower = path.name.lower()
    if name_lower.endswith(".docx.pdf") or name_lower.endswith(".doc.pdf"):
        return [path.with_name(path.name[:-4])]
    if path.suffix.lower() in OFFICE_SUFFIXES:
        return [converted_pdf_path(path)]
    return []


async def normalize_office_path_for_parse(
    file_path: str | Path,
    *,
    timeout_sec: int | None = None,
) -> tuple[Path, str]:
    """
    职责: 将 Office 磁盘源归一为可解析 PDF 路径并返回解析后缀。
    不做: 不解析正文、不调用 LLM、不改业务表字段。

    Returns:
        (resolved_path, ext)：非 Office 时 resolved 为原路径；Office 为转换产物，ext 恒为 ``.pdf``。
    """
    src = Path(file_path)
    suffix = src.suffix.lower()
    if suffix in OFFICE_SUFFIXES:
        dest = await convert_office_to_pdf(src, timeout_sec=timeout_sec)
        return dest, ".pdf"
    return src, suffix


async def convert_office_to_pdf(
    src_path: str | Path,
    timeout_sec: int | None = None,
) -> Path:
    """将 .doc/.docx 转为 PDF。产物已存在则直接返回（幂等）。"""
    src = Path(src_path)
    dest = converted_pdf_path(src)
    if dest.exists() and dest.stat().st_size > 4:
        with dest.open("rb") as existing:
            if existing.read(4).startswith(b"%PDF"):
                return dest

    suffix = src.suffix.lower()
    if suffix not in OFFICE_SUFFIXES:
        raise ValueError(f"不支持转换为 PDF 的文件类型: {suffix or '无后缀'}")

    base_url = (settings.OFFICE_CONVERT_URL or "").rstrip("/")
    if not base_url:
        raise ValueError("Word 转换服务未配置，无法处理 Word 文档")

    if not src.exists():
        raise ValueError(f"待转换文件不存在: {src}")

    timeout = timeout_sec if timeout_sec is not None else int(
        settings.OFFICE_CONVERT_TIMEOUT_SEC
    )
    convert_url = f"{base_url}/forms/libreoffice/convert"
    logger.info("Office 转 PDF 开始: src={} dest={}", src.name, dest.name)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            with src.open("rb") as handle:
                response = await client.post(
                    convert_url,
                    files={"files": (src.name, handle, "application/octet-stream")},
                )
    except httpx.TimeoutException as exc:
        raise ValueError(OFFICE_CONVERT_TIMEOUT_USER_MESSAGE) from exc
    except httpx.HTTPError as exc:
        raise ValueError(f"Word 转换服务不可用: {exc}") from exc

    if response.status_code == 503:
        raise ValueError(OFFICE_CONVERT_TIMEOUT_USER_MESSAGE)
    if response.status_code != 200:
        raise ValueError(f"Word 文档转换失败: HTTP {response.status_code}")

    payload = response.content
    if len(payload) < 4 or not payload.startswith(b"%PDF"):
        raise ValueError("Word 文档转换失败: 转换结果不是有效 PDF")

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(f"{dest}.tmp")
    tmp_path.write_bytes(payload)
    tmp_path.replace(dest)
    logger.info("Office 转 PDF 完成: dest={} size={}", dest.name, dest.stat().st_size)
    return dest
