"""文件名安全工具

职责: 统一上传文件名清洗、导出文件名拼装与落盘路径归一校验，防止路径穿越/任意覆盖。
不做: 不负责文件内容校验、不负责存储目录的创建与权限控制、不负责 HTTP 头编码。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_WINDOWS_RESERVED = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
})

# 禁止字符: Windows 非法字符 + 控制字符 + 不可见/方向控制 Unicode
_ILLEGAL_CHARS = re.compile(
    r'[<>:"/\\|?*\x00-\x1f\u200b-\u200f\ufeff\u202a-\u202e]'
)

_MAX_LEN = 200


def sanitize_filename(name: str, fallback: str = "unnamed") -> str:
    """清理上传文件名，确保跨平台安全且 Windows 兼容。

    处理: 剥离目录成分、替换非法/不可见字符、首尾空格与点号、
    Windows 保留名、超长文件名截断。返回值保证不含路径分隔符。

    Args:
        name: 原始文件名(可能来自不可信的上传输入)
        fallback: 清理后为空时的兜底名称

    Returns:
        安全的纯文件名(不含任何路径成分)
    """
    name = os.path.basename(name or "")
    name = _ILLEGAL_CHARS.sub("_", name)
    name = name.strip(" .")
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if stem.upper() in _WINDOWS_RESERVED:
        name = f"_{name}"
    if len(name) > _MAX_LEN:
        if "." in name:
            stem_part, ext = name.rsplit(".", 1)
            name = f"{stem_part[:_MAX_LEN - len(ext) - 1]}.{ext}"
        else:
            name = name[:_MAX_LEN]
    return name or fallback


def build_export_filename(
    project_name: str | None,
    label: str,
    ext: str,
    *,
    fallback: str = "未命名项目",
) -> str:
    """按「项目名+流程类型」拼导出文件名，并做跨平台安全清洗。

    职责: 统一三大流程最终产物的下载文件名。
    不做: 不负责生成文件内容、不负责 HTTP Content-Disposition 编码。
    """
    ext = (ext or "").lstrip(".")
    stem_source = (project_name or "").strip() or fallback
    stem = sanitize_filename(stem_source, fallback=fallback)
    composed = f"{stem}{label}.{ext}" if ext else f"{stem}{label}"
    composed_fallback = f"{fallback}{label}.{ext}" if ext else f"{fallback}{label}"
    return sanitize_filename(composed, fallback=composed_fallback)


def resolve_within_dir(base_dir: Path, filename: str) -> Path:
    """将清理后的文件名安全拼接到目标目录，并校验落点未逃逸。

    即便上游已清洗，此处再做一次归一化纵深防御:
    解析为绝对路径后断言其位于 base_dir 之内。

    Args:
        base_dir: 允许写入的目标目录
        filename: 已经过 sanitize_filename 处理的文件名

    Returns:
        校验通过的绝对文件路径

    Raises:
        ValueError: 落点逃逸出 base_dir(疑似路径穿越)
    """
    base_resolved = base_dir.resolve()
    target = (base_resolved / filename).resolve()
    if base_resolved != target and base_resolved not in target.parents:
        raise ValueError(f"非法文件落点(疑似路径穿越): {filename}")
    return target
