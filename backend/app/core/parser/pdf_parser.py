"""PDF解析器

使用pdfplumber精准提取PDF内容，保留布局和表格结构。
使用PyMuPDF(fitz)提取PDF中的图片信息。
"""

import fitz
import pdfplumber
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from loguru import logger

from app.core.config import settings
from app.core.parser.table_extractor import TableExtractor, ExtractedTable
from app.core.parser.image_extractor import ImageExtractor, ExtractedImage
from app.core.parser.page_chrome_policy import (
    PageChromePolicy,
    crop_pdfplumber_page,
    filter_page_images,
    learn_page_chrome,
)
from app.core.parser.watermark_policy import WatermarkPolicy, learn_watermark_policy


@dataclass
class ParsedPage:
    """解析后的单页数据"""
    page_num: int  # 页码（从1开始）
    text: str  # 页面文本内容
    tables: list[ExtractedTable] = field(default_factory=list)  # 页面表格
    images: list[ExtractedImage] = field(default_factory=list)  # 页面图片
    width: float = 0.0  # 页面宽度
    height: float = 0.0  # 页面高度
    # 整页OCR/VL识别文本的瞬态字段(仅解析期使用, 不落库), 拼装侧只消费一份
    full_page_visual_content: str = ""
    
    @property
    def has_tables(self) -> bool:
        """是否包含表格"""
        return len(self.tables) > 0
    
    @property
    def has_images(self) -> bool:
        """是否包含图片"""
        return len(self.images) > 0
    
    @property
    def char_count(self) -> int:
        """字符数"""
        return len(self.text) if self.text else 0


@dataclass
class ParsedDocument:
    """解析后的文档数据"""
    file_path: str  # 文件路径
    filename: str  # 文件名
    page_count: int  # 总页数
    pages: list[ParsedPage] = field(default_factory=list)  # 所有页面
    metadata: dict = field(default_factory=dict)  # PDF元数据
    
    @property
    def full_text(self) -> str:
        """获取完整文本（所有页面合并）
        
        Returns:
            str: 完整文档文本，页面之间用换行分隔
        """
        return "\n\n".join(
            page.text for page in self.pages if page.text
        )
    
    @property
    def all_tables(self) -> list[ExtractedTable]:
        """获取所有表格
        
        Returns:
            list[ExtractedTable]: 文档中的所有表格
        """
        tables = []
        for page in self.pages:
            tables.extend(page.tables)
        return tables
    
    @property
    def all_images(self) -> list[ExtractedImage]:
        """获取所有图片
        
        Returns:
            list[ExtractedImage]: 文档中的所有图片
        """
        images = []
        for page in self.pages:
            images.extend(page.images)
        return images
    
    @property
    def total_char_count(self) -> int:
        """总字符数"""
        return sum(page.char_count for page in self.pages)
    
    def get_text_by_page_range(self, start: int, end: int) -> str:
        """获取指定页码范围的文本
        
        Args:
            start: 起始页码（从1开始，包含）
            end: 结束页码（包含）
            
        Returns:
            str: 指定范围的文本
        """
        texts = []
        for page in self.pages:
            if start <= page.page_num <= end:
                if page.text:
                    texts.append(page.text)
        return "\n\n".join(texts)


class PdfParser:
    """PDF解析器
    
    使用pdfplumber解析PDF文档，提取文本和表格内容。
    使用PyMuPDF(fitz)提取PDF中的图片信息。
    
    示例:
        >>> parser = PdfParser()
        >>> doc = parser.parse("招标文件.pdf")
        >>> print(doc.full_text)
        >>> print(len(doc.all_tables))
        >>> print(len(doc.all_images))
    """
    
    def __init__(
        self,
        extract_tables: bool = True,
        extract_images: bool = True,
    ):
        """初始化解析器
        
        Args:
            extract_tables: 是否提取表格，默认True
            extract_images: 是否提取图片，默认True
        """
        self.extract_tables = extract_tables
        self.extract_images = extract_images
        self.table_extractor = TableExtractor() if extract_tables else None
        self.image_extractor = ImageExtractor() if extract_images else None
    
    def parse(
        self,
        file_path: str | Path,
        image_save_dir: Optional[Path] = None,
    ) -> ParsedDocument:
        """解析PDF文件
        
        Args:
            file_path: PDF文件路径
            image_save_dir: 图片保存目录，为None时不保存图片文件
            
        Returns:
            ParsedDocument: 解析后的文档对象
            
        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 文件不是有效的PDF
        """
        file_path = Path(file_path)
        
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        
        if file_path.suffix.lower() != ".pdf":
            raise ValueError(f"不是PDF文件: {file_path}")
        
        logger.info(f"开始解析PDF: {file_path.name}")
        
        pages: list[ParsedPage] = []
        metadata: dict = {}
        
        # 使用fitz预提取所有页面的图片信息
        page_images: dict[int, list[ExtractedImage]] = {}
        if self.extract_images and self.image_extractor:
            page_images = self._extract_all_images(file_path, image_save_dir)
        
        try:
            with pdfplumber.open(file_path) as pdf:
                # 提取元数据
                metadata = self._extract_metadata(pdf)
                page_count = len(pdf.pages)
                chrome_policy = self._learn_page_chrome_policy(pdf.pages, page_images)
                if chrome_policy.summary.pages:
                    metadata["page_chrome"] = chrome_policy.summary.to_dict()
                    metadata["page_chrome_body_bboxes"] = {
                        str(page_num): list(decision.body_bbox)
                        for page_num, decision in chrome_policy.decisions.items()
                    }
                watermark_policy = self._learn_watermark_policy(pdf.pages, page_images)
                
                logger.info(f"PDF共 {page_count} 页")
                
                # 逐页解析
                for idx, page in enumerate(pdf.pages):
                    page_num = idx + 1
                    images = filter_page_images(
                        page_images.get(page_num, []),
                        chrome_policy,
                        page_num,
                    )
                    images = watermark_policy.filter_images(
                        images, float(page.width), float(page.height)
                    )
                    parse_page = crop_pdfplumber_page(page, chrome_policy, page_num)
                    parse_page = watermark_policy.filter_page(parse_page)
                    parsed_page = self._parse_page(parse_page, page_num, images)
                    parsed_page.text = chrome_policy.clean_text(
                        page_num, parsed_page.text
                    )
                    parsed_page.width = float(page.width)
                    parsed_page.height = float(page.height)
                    pages.append(parsed_page)
                    
                    if page_num % 10 == 0:
                        logger.debug(f"已解析 {page_num}/{page_count} 页")

                if watermark_policy.has_rules:
                    metadata["watermark_filter"] = watermark_policy.summary.to_dict()
                    logger.info(
                        "水印预过滤: 文本组{} 图片签名{} 过滤字符{} 过滤图片{}",
                        watermark_policy.summary.text_groups,
                        watermark_policy.summary.image_signatures,
                        watermark_policy.summary.filtered_chars,
                        watermark_policy.summary.filtered_images,
                    )
            
            doc = ParsedDocument(
                file_path=str(file_path),
                filename=file_path.name,
                page_count=page_count,
                pages=pages,
                metadata=metadata
            )
            
            image_count = len(doc.all_images)
            logger.success(
                f"解析完成: {file_path.name}, "
                f"共 {page_count} 页, "
                f"{doc.total_char_count} 字符, "
                f"{len(doc.all_tables)} 个表格, "
                f"{image_count} 张图片"
            )
            
            return doc
            
        except Exception as e:
            logger.error(f"解析PDF失败: {file_path.name}, 错误: {e}")
            raise
    
    def _extract_metadata(self, pdf: pdfplumber.PDF) -> dict:
        """提取PDF元数据
        
        Args:
            pdf: pdfplumber PDF对象
            
        Returns:
            dict: 元数据字典
        """
        metadata = {}
        
        if pdf.metadata:
            # 常见元数据字段
            fields = [
                "Title", "Author", "Subject", "Keywords",
                "Creator", "Producer", "CreationDate", "ModDate"
            ]
            for field in fields:
                value = pdf.metadata.get(field)
                if value:
                    metadata[field.lower()] = str(value)
        
        return metadata
    
    def _parse_page(
        self,
        page: pdfplumber.page.Page,
        page_num: int,
        images: Optional[list[ExtractedImage]] = None,
    ) -> ParsedPage:
        """解析单页
        
        Args:
            page: pdfplumber页面对象
            page_num: 页码（从1开始）
            images: 该页面已提取的图片列表（由fitz预提取）
            
        Returns:
            ParsedPage: 解析后的页面对象
        """
        # 提取文本
        text = self._extract_text(page)
        
        # 提取表格
        tables: list[ExtractedTable] = []
        if self.extract_tables and self.table_extractor:
            tables = self.table_extractor.extract_from_page(page, page_num)
        
        return ParsedPage(
            page_num=page_num,
            text=text,
            tables=tables,
            images=images or [],
            width=float(page.width),
            height=float(page.height)
        )
    
    def _extract_text(self, page: pdfplumber.page.Page) -> str:
        """提取页面文本
        
        Args:
            page: pdfplumber页面对象
            
        Returns:
            str: 页面文本内容
        """
        try:
            text = page.extract_text()
            if settings.PARSER_SCRIPT_LIFT_ENABLED:
                try:
                    from pdfplumber.utils import extract_text
                    from app.core.parser.script_char_lifter import lift_page_script_chars

                    lifted_chars = lift_page_script_chars(list(page.chars or []))
                    lifted_text = extract_text(lifted_chars) if lifted_chars else ""
                    if lifted_text:
                        text = lifted_text
                except Exception as exc:
                    logger.debug(f"上下标文本修复回退原始提取: {exc}")
            if text:
                from app.core.parser.text_normalizer import normalize_cjk
                from app.core.xml_compat import sanitize_xml_compatible_text

                text = sanitize_xml_compatible_text(text)
                text = normalize_cjk(text)
                lines = text.split("\n")
                cleaned_lines = []
                for line in lines:
                    line = line.strip()
                    if line:
                        cleaned_lines.append(line)
                return "\n".join(cleaned_lines)
            return ""
        except Exception as e:
            logger.warning(f"提取文本失败: {e}")
            return ""
    
    def _extract_all_images(
        self,
        file_path: Path,
        save_dir: Optional[Path] = None,
    ) -> dict[int, list[ExtractedImage]]:
        """使用fitz预提取所有页面的图片信息
        
        Args:
            file_path: PDF文件路径
            save_dir: 图片保存目录
            
        Returns:
            dict[int, list[ExtractedImage]]: 按页码分组的图片 {page_num: [images]}
        """
        try:
            return self.image_extractor.extract_from_pdf(
                pdf_path=file_path,
                save_dir=save_dir,
            )
        except Exception as e:
            logger.warning(f"图片提取失败（不影响文本解析）: {e}")
            return {}

    def _learn_page_chrome_policy(
        self,
        pages: list[pdfplumber.page.Page],
        page_images: dict[int, list[ExtractedImage]],
    ) -> PageChromePolicy:
        """学习当前 PDF 的跨页重复页眉页脚策略。"""

        try:
            from app.core.config import settings

            if not bool(getattr(settings, "PAGE_CHROME_FILTER_ENABLED", True)):
                return PageChromePolicy()
            return learn_page_chrome(
                pages,
                page_images=page_images,
                min_repeat_pages=int(
                    getattr(settings, "PAGE_CHROME_MIN_REPEAT_PAGES", 3)
                ),
                repeat_ratio=float(getattr(settings, "PAGE_CHROME_REPEAT_RATIO", 0.6)),
                candidate_band_ratio=float(
                    getattr(settings, "PAGE_CHROME_CANDIDATE_BAND_RATIO", 0.25)
                ),
            )
        except Exception as e:
            logger.warning(f"Page Chrome 策略学习失败(降级为原解析): {e}")
            return PageChromePolicy()

    def _learn_watermark_policy(
        self,
        pages: list[pdfplumber.page.Page],
        page_images: dict[int, list[ExtractedImage]],
    ) -> WatermarkPolicy:
        """学习当前 PDF 的跨页重复水印策略(根治层)。"""

        try:
            from app.core.config import settings

            if not bool(getattr(settings, "WATERMARK_FILTER_ENABLED", True)):
                return WatermarkPolicy()
            return learn_watermark_policy(
                pages,
                page_images=page_images,
                min_repeat_pages=int(
                    getattr(settings, "WATERMARK_MIN_REPEAT_PAGES", 3)
                ),
                repeat_ratio=float(getattr(settings, "WATERMARK_REPEAT_RATIO", 0.6)),
            )
        except Exception as e:
            logger.warning(f"水印策略学习失败(降级为原解析): {e}")
            return WatermarkPolicy()

    def parse_bytes(
        self,
        pdf_bytes: bytes,
        filename: str = "unknown.pdf",
        image_save_dir: Optional[Path] = None,
    ) -> ParsedDocument:
        """从字节流解析PDF
        
        Args:
            pdf_bytes: PDF文件的字节内容
            filename: 文件名（用于日志）
            image_save_dir: 图片保存目录，为None时不保存图片文件
            
        Returns:
            ParsedDocument: 解析后的文档对象
        """
        import io
        
        logger.info(f"从字节流解析PDF: {filename}")
        
        pages: list[ParsedPage] = []
        metadata: dict = {}
        
        # 使用fitz预提取图片
        page_images: dict[int, list[ExtractedImage]] = {}
        if self.extract_images and self.image_extractor:
            try:
                fitz_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                for page_idx in range(len(fitz_doc)):
                    fitz_page = fitz_doc[page_idx]
                    page_num = page_idx + 1
                    images = self.image_extractor.extract_from_page(
                        doc=fitz_doc,
                        page=fitz_page,
                        page_num=page_num,
                        save_dir=image_save_dir,
                    )
                    if images:
                        page_images[page_num] = images
                fitz_doc.close()
            except Exception as e:
                logger.warning(f"字节流图片提取失败（不影响文本解析）: {e}")
        
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                metadata = self._extract_metadata(pdf)
                page_count = len(pdf.pages)
                chrome_policy = self._learn_page_chrome_policy(pdf.pages, page_images)
                if chrome_policy.summary.pages:
                    metadata["page_chrome"] = chrome_policy.summary.to_dict()
                    metadata["page_chrome_body_bboxes"] = {
                        str(page_num): list(decision.body_bbox)
                        for page_num, decision in chrome_policy.decisions.items()
                    }
                watermark_policy = self._learn_watermark_policy(pdf.pages, page_images)
                
                for idx, page in enumerate(pdf.pages):
                    page_num = idx + 1
                    images = filter_page_images(
                        page_images.get(page_num, []),
                        chrome_policy,
                        page_num,
                    )
                    images = watermark_policy.filter_images(
                        images, float(page.width), float(page.height)
                    )
                    parse_page = crop_pdfplumber_page(page, chrome_policy, page_num)
                    parse_page = watermark_policy.filter_page(parse_page)
                    parsed_page = self._parse_page(parse_page, page_num, images)
                    parsed_page.text = chrome_policy.clean_text(
                        page_num, parsed_page.text
                    )
                    parsed_page.width = float(page.width)
                    parsed_page.height = float(page.height)
                    pages.append(parsed_page)

                if watermark_policy.has_rules:
                    metadata["watermark_filter"] = watermark_policy.summary.to_dict()
            
            doc = ParsedDocument(
                file_path="",
                filename=filename,
                page_count=page_count,
                pages=pages,
                metadata=metadata
            )
            
            image_count = len(doc.all_images)
            logger.success(
                f"解析完成: {filename}, "
                f"共 {page_count} 页, "
                f"{doc.total_char_count} 字符, "
                f"{image_count} 张图片"
            )
            
            return doc
            
        except Exception as e:
            logger.error(f"解析PDF字节流失败: {filename}, 错误: {e}")
            raise
