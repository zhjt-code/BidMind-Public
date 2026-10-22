"""门禁轻量采样器

职责: 从上传的PDF字节流中读取前N页文本与总页数，供门禁打分使用。
不做: 不做完整解析(表格/图片/OCR/向量化)，不接入水印与页眉页脚降噪——
      门禁只做要素词计数，水印词与标书要素词表基本不重叠，且判定原则为
      宁松勿严，少量噪音文本不影响结论方向。
"""

import io
from dataclasses import dataclass

import pdfplumber
from loguru import logger

# 采样页数: 覆盖封面/公告/目录/须知前言，兼顾速度(逐页惰性加载)
DEFAULT_SAMPLE_PAGES = 12


@dataclass(frozen=True)
class GateSample:
    """门禁采样结果"""

    page_count: int
    sampled_pages: int
    page_texts: list[str]

    @property
    def total_chars(self) -> int:
        """采样页可提取文本总字符数(去空白)"""
        return sum(len(t.strip()) for t in self.page_texts)


def sample_pdf_bytes(content: bytes, max_pages: int = DEFAULT_SAMPLE_PAGES) -> GateSample:
    """从PDF字节流采样前N页文本

    Args:
        content: PDF文件字节内容
        max_pages: 最多采样页数

    Returns:
        GateSample: 采样结果

    Raises:
        Exception: PDF无法打开时向上抛出，由调用方决定fail-open策略
    """
    page_texts: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        page_count = len(pdf.pages)
        for page in pdf.pages[:max_pages]:
            try:
                page_texts.append(page.extract_text() or "")
            except Exception as e:
                # 单页提取失败不中断采样，按空页处理
                logger.debug(f"门禁采样: 第{page.page_number}页文本提取失败: {e}")
                page_texts.append("")

    return GateSample(
        page_count=page_count,
        sampled_pages=len(page_texts),
        page_texts=page_texts,
    )
