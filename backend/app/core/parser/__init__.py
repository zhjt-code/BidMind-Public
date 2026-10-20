"""文档解析模块

提供PDF文档解析、表格提取、图片提取、文本分块、本地OCR功能。
"""

from app.core.parser.pdf_parser import PdfParser, ParsedDocument, ParsedPage
from app.core.parser.table_extractor import TableExtractor, ExtractedTable
from app.core.parser.image_extractor import (
    ImageExtractor,
    ExtractedImage,
    generate_image_annotations,
    extract_context_for_images,
)
from app.core.parser.text_chunker import TextChunker, TextChunk
from app.core.parser.chunk_planner import ChunkPlanner, ChunkShadowSummary
from app.core.parser.chunk_context import build_embedding_text
from app.core.parser.chunk_quality import (
    assess_chunk,
    filter_chunks,
    QualityStats,
)
from app.core.parser.text_cleaner import (
    denoise_pages,
    detect_repeated_lines,
    merge_soft_linebreaks,
)
from app.core.parser.ocr_engine import OcrEngine, OcrResult

__all__ = [
    "PdfParser",
    "ParsedDocument",
    "ParsedPage",
    "TableExtractor",
    "ExtractedTable",
    "ImageExtractor",
    "ExtractedImage",
    "generate_image_annotations",
    "extract_context_for_images",
    "TextChunker",
    "TextChunk",
    "ChunkPlanner",
    "ChunkShadowSummary",
    "build_embedding_text",
    "assess_chunk",
    "filter_chunks",
    "QualityStats",
    "denoise_pages",
    "detect_repeated_lines",
    "merge_soft_linebreaks",
    "OcrEngine",
    "OcrResult",
]
