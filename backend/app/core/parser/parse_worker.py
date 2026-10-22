"""子进程 PDF 解析入口

此模块故意独立于 app.core.tasks 包，使 ProcessPoolExecutor(spawn)
的子进程 unpickle 函数引用时，import 路径为:

    app -> app.core -> app.core.parser -> parse_worker

整条路径上没有 torch / sentence-transformers 等重量级依赖，
从而避免 Python 3.13 + torch 在 spawn 子进程中的兼容性错误。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.parser.pdf_parser import ParsedDocument


def parse_pdf_in_process(file_path: str, image_save_dir_str: str) -> ParsedDocument:
    """子进程入口：执行 PDF 解析

    必须为模块顶层函数，便于 pickle 序列化。
    所有依赖均在函数体内懒导入，保证模块加载零开销。
    """
    from pathlib import Path

    from app.core.parser.pdf_parser import PdfParser

    parser = PdfParser()
    return parser.parse(file_path, image_save_dir=Path(image_save_dir_str))
