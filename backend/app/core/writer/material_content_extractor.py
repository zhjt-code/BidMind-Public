"""素材内容提取器

两阶段批处理架构:
  阶段1 parse_only():   纯解析(无LLM),返回文本 + 图片对象列表
  阶段2 batch_vl_recognize(): 统一图片池走VL管线(AdaptiveConcurrency)
  阶段3 assemble_with_vl():  VL结果回填 + 标注组装 + 类型推断

复用投标审查流程中已验证的成熟解析模块:
- PdfParser + ImageAnalyzer + ImageExtractor
- XlsxParser

Office(.doc/.docx) 由调用方先经 ``normalize_office_path_for_parse`` 转为 PDF，
本模块不再维护 DOCX 弱抽取旁路。

单文件兼容入口 extract() 保留,内部调用三阶段流程。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger

from app.core.parser.image_extractor import (
    ExtractedImage,
    build_natural_evidence_chunk,
    extract_context_for_images,
    generate_image_annotations,
    insert_annotation_near_anchor,
)
from app.core.parser.pdf_parser import ParsedDocument, PdfParser
from app.core.parser.scan_page_policy import (
    is_scan_dominated_page,
    render_scan_page_image,
)
from app.core.parser.xlsx_parser import XlsxParser


@dataclass
class MaterialContent:
    """素材内容提取结果

    Attributes:
        text: 提取的文本内容(含VL识别结果融入后的完整文本)
        material_type: 推断的素材类型(text/image/table)
        images: 提取的图片列表(PDF 等容器内图片)
        tables: 提取的表格列表(PDF中的表格)
        page_count: 页数(PDF有效,其他为估算值)
    """
    text: str
    material_type: str
    images: list = field(default_factory=list)
    tables: list = field(default_factory=list)
    page_count: int = 1
    page_texts: list[tuple[int, str]] = field(default_factory=list)
    parsed_pages: list = field(default_factory=list)


@dataclass
class ParseResult:
    """纯解析结果(不含VL,用于批处理阶段1)

    Attributes:
        raw_text: 纯解析得到的文本(不含VL标注)
        images: 可送入VL管线的图片对象列表(ExtractedImage)
        tables: 表格列表
        page_count: 页数
        parsed_pages: PDF解析的页面对象(用于后续标注组装)
        parsed_document: PDF解析文档对象(用于结构分析/页段裁切)
        file_type: 文件类型(pdf/xlsx/image)
    """
    raw_text: str
    images: list[ExtractedImage] = field(default_factory=list)
    tables: list = field(default_factory=list)
    page_count: int = 1
    parsed_pages: list = field(default_factory=list)
    parsed_document: Optional[ParsedDocument] = None
    file_type: str = "text"


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff"}


class MaterialContentExtractor:
    """素材内容提取器(两阶段批处理架构)

    批处理模式(推荐):
        extractor = MaterialContentExtractor()

        # 阶段1: 纯解析,收集图片池
        parse_results = {}
        all_images = []
        for mat in materials:
            pr = extractor.parse_only(mat.file_path, ext, image_save_dir)
            parse_results[mat.id] = pr
            all_images.extend(pr.images)

        # 阶段2: 统一VL(复用投标审查管线)
        await MaterialContentExtractor.batch_vl_recognize(all_images)

        # 阶段3: 组装最终文本
        for mat_id, pr in parse_results.items():
            content = extractor.assemble_with_vl(pr)

    单文件兼容模式:
        result = await extractor.extract(file_path, ext, project_id, image_save_dir)
    """

    def __init__(self, enable_vl: bool = True):
        self.enable_vl = enable_vl
        self._pdf_parser = PdfParser(extract_tables=True, extract_images=True)
        self._xlsx_parser = XlsxParser()

    # ── 阶段1: 纯解析(无LLM调用) ───────────────────────────

    def parse_only(
        self,
        file_path: str,
        ext: str,
        image_save_dir: Optional[Path] = None,
    ) -> ParseResult:
        """纯解析,不调用任何LLM

        解析文档结构,提取文本和图片对象列表。
        图片对象统一为 ExtractedImage 格式,可直接送入 VL 管线。

        Args:
            file_path: 文件路径
            ext: 文件扩展名(含点号)
            image_save_dir: 图片保存目录

        Returns:
            ParseResult: 纯解析结果
        """
        ext_lower = ext.lower()
        if ext_lower in {".doc", ".docx"}:
            raise ValueError(
                f"Office 文档须先经 normalize_office_path_for_parse 转为 PDF: {ext_lower}"
            )

        path_obj = Path(file_path)
        if not path_obj.exists():
            raise FileNotFoundError(f"素材文件不存在: {file_path}")

        logger.info(f"[素材解析] 开始: {path_obj.name}, 类型={ext_lower}")

        if ext_lower == ".pdf":
            return self._parse_pdf(file_path, image_save_dir)
        elif ext_lower == ".xlsx":
            return self._parse_xlsx(file_path)
        elif ext_lower in IMAGE_EXTENSIONS:
            return self._parse_image(file_path, ext_lower)
        else:
            raise ValueError(f"不支持的素材文件类型: {ext_lower}")

    def _parse_pdf(
        self, file_path: str, image_save_dir: Optional[Path],
    ) -> ParseResult:
        """PDF纯解析: pdfplumber + fitz 提取文本/表格/图片,不调用VL"""
        parsed = self._pdf_parser.parse(file_path, image_save_dir=image_save_dir)
        self._replace_scan_page_images(file_path, parsed, image_save_dir)

        text_parts = []
        all_images: list[ExtractedImage] = []

        for page in parsed.pages:
            text_parts.append(page.text or "")
            if page.images:
                all_images.extend(page.images)

        raw_text = "\n\n".join(text_parts)

        logger.info(
            f"[素材解析] PDF完成: pages={parsed.page_count}, "
            f"chars={len(raw_text)}, images={len(all_images)}, "
            f"tables={len(parsed.all_tables)}"
        )

        return ParseResult(
            raw_text=raw_text,
            images=all_images,
            tables=parsed.all_tables,
            page_count=parsed.page_count,
            parsed_pages=parsed.pages,
            parsed_document=parsed,
            file_type="pdf",
        )

    @staticmethod
    def _replace_scan_page_images(
        file_path: str,
        parsed: ParsedDocument,
        image_save_dir: Optional[Path],
    ) -> None:
        """扫描主导页使用整页合成图替代逐XObject提取图。"""
        if image_save_dir is None:
            return

        chrome_bboxes = (parsed.metadata or {}).get("page_chrome_body_bboxes") or {}

        def _clip_for_page(page_num: int) -> tuple[float, float, float, float] | None:
            raw = chrome_bboxes.get(str(page_num)) or chrome_bboxes.get(page_num)
            if not raw or len(raw) != 4:
                return None
            try:
                return tuple(float(v) for v in raw)  # type: ignore[return-value]
            except Exception:
                return None

        replaced = 0
        for page in parsed.pages:
            if not page.images or not is_scan_dominated_page(page.text or "", page.images):
                continue
            try:
                page.images = [
                    render_scan_page_image(
                        file_path,
                        page.page_num,
                        image_save_dir,
                        image_index=0,
                        clip=_clip_for_page(page.page_num),
                    )
                ]
                replaced += 1
            except Exception as exc:  # noqa: BLE001 解析阶段应保留原逐图产物继续推进
                logger.warning(
                    "[素材解析] 扫描页整页渲染失败, 保留逐图产物: file={}, page={}, err={}",
                    Path(file_path).name,
                    page.page_num,
                    exc,
                )
        if replaced:
            logger.info(
                "[素材解析] 扫描页改用整页渲染: file={}, pages={}",
                Path(file_path).name,
                replaced,
            )

    def _parse_xlsx(self, file_path: str) -> ParseResult:
        """XLSX纯解析: 无图片,直接返回表格文本"""
        parsed = self._xlsx_parser.parse(file_path)

        logger.info(
            f"[素材解析] XLSX完成: sheets={len(parsed.sheets)}, "
            f"chars={len(parsed.full_text)}"
        )

        return ParseResult(
            raw_text=parsed.full_text,
            images=[],
            tables=parsed.sheets,
            page_count=max(1, len(parsed.sheets)),
            file_type="xlsx",
        )

    def _parse_image(self, file_path: str, ext_lower: str) -> ParseResult:
        """纯图片: 构造 ExtractedImage 对象,等待批量VL识别"""
        img = ExtractedImage(
            image_index=0,
            page_num=1,
            width=0,
            height=0,
            image_format=ext_lower.lstrip("."),
            xref=0,
            file_path=str(file_path),
        )

        logger.info(f"[素材解析] 图片: {Path(file_path).name}")

        return ParseResult(
            raw_text="",
            images=[img],
            tables=[],
            page_count=1,
            file_type="image",
        )

    # ── 阶段2: 统一VL批量识别 ──────────────────────────────

    @staticmethod
    async def batch_vl_recognize(
        images: list[ExtractedImage],
        progress_callback=None,
        project_id: int | None = None,
    ) -> None:
        """统一VL批量识别,复用共享图片识别服务(含OCR-first)

        所有素材的图片汇聚到同一个池中,一次性并发处理。
        结果原地写入每个 ExtractedImage.visual_content 字段。

        Args:
            images: 统一图片池(来自所有素材文件)
            progress_callback: 可选的进度回调
        """
        from app.core.parser.image_recognition_service import recognize_images

        await recognize_images(
            images,
            progress_callback=progress_callback,
            purpose="素材批处理",
            project_id=project_id,
            offload_llm=project_id is not None,
        )

    # ── 阶段3: VL结果组装 + 类型推断 ──────────────────────

    def assemble_with_vl(self, pr: ParseResult) -> MaterialContent:
        """VL识别完成后,将结果回填到文本并推断素材类型

        Args:
            pr: parse_only() 的返回值(图片已经过 batch_vl_recognize 填充)

        Returns:
            MaterialContent: 最终提取结果
        """
        if pr.file_type == "pdf":
            return self._assemble_pdf(pr)
        elif pr.file_type == "xlsx":
            return self._assemble_xlsx(pr)
        elif pr.file_type == "image":
            return self._assemble_image(pr)
        else:
            return MaterialContent(
                text=pr.raw_text,
                material_type="text",
                images=pr.images,
                tables=pr.tables,
                page_count=pr.page_count,
                parsed_pages=pr.parsed_pages,
            )

    def _assemble_pdf(self, pr: ParseResult) -> MaterialContent:
        """PDF: 按 page_num 将VL标注回填到对应页面文本"""
        images_by_page: dict[int, list[ExtractedImage]] = {}
        for img in pr.images:
            images_by_page.setdefault(img.page_num, []).append(img)

        text_parts: list[str] = []
        page_texts: list[tuple[int, str]] = []
        for page in pr.parsed_pages:
            page_text = page.text or ""
            page_imgs = images_by_page.get(page.page_num, [])

            if page_imgs:
                prev_text = ""
                if page.page_num > 1 and len(pr.parsed_pages) >= page.page_num:
                    prev_page = pr.parsed_pages[page.page_num - 2]
                    prev_text = prev_page.text or ""

                context = extract_context_for_images(
                    current_page_text=page_text,
                    prev_page_text=prev_text,
                )
                annotation = generate_image_annotations(page_imgs, context)
                if annotation:
                    page_text = insert_annotation_near_anchor(page_text, annotation)

                evidence_text = build_natural_evidence_chunk(
                    page_num=page.page_num,
                    images=page_imgs,
                    context=context,
                )
                if evidence_text and len(evidence_text) >= 20:
                    page_text = page_text + "\n" + evidence_text

            page.text = page_text
            text_parts.append(page_text)
            page_texts.append((page.page_num, page_text))

        full_text = "\n\n".join(text_parts)
        material_type = self._infer_material_type(
            full_text,
            has_images=bool(pr.images),
            has_tables=bool(pr.tables),
        )

        return MaterialContent(
            text=full_text,
            material_type=material_type,
            images=pr.images,
            tables=pr.tables,
            page_count=pr.page_count,
            page_texts=page_texts,
            parsed_pages=pr.parsed_pages,
        )

    @staticmethod
    def _assemble_xlsx(pr: ParseResult) -> MaterialContent:
        """XLSX: 无图片,直接返回"""
        return MaterialContent(
            text=pr.raw_text,
            material_type="table",
            images=[],
            tables=pr.tables,
            page_count=pr.page_count,
            page_texts=[(1, pr.raw_text)] if pr.raw_text else [],
            parsed_pages=pr.parsed_pages,
        )

    @staticmethod
    def _assemble_image(pr: ParseResult) -> MaterialContent:
        """纯图片: VL识别结果作为文本内容"""
        vl_result = ""
        if pr.images and pr.images[0].visual_content:
            vl_result = pr.images[0].visual_content

        if vl_result:
            text = f"[图片内容VL识别结果]\n{vl_result}"
        else:
            text = "[图片内容无法识别]"

        image_evidence = MaterialContentExtractor._build_material_image_evidence(pr.images)
        if image_evidence:
            text = text + "\n\n" + image_evidence

        return MaterialContent(
            text=text,
            material_type="image",
            images=pr.images,
            tables=[],
            page_count=1,
            page_texts=[(1, text)] if text else [],
            parsed_pages=pr.parsed_pages,
        )

    # ── 单文件兼容入口 ─────────────────────────────────────

    async def extract(
        self,
        file_path: str,
        ext: str,
        project_id: int,
        image_save_dir: Optional[Path] = None,
    ) -> MaterialContent:
        """单文件提取(向后兼容)

        内部调用三阶段流程: parse_only -> batch_vl_recognize -> assemble_with_vl
        """
        ext_lower = ext.lower()
        path_obj = Path(file_path)
        if not path_obj.exists():
            raise FileNotFoundError(f"素材文件不存在: {file_path}")

        logger.info(f"开始提取素材内容: {path_obj.name}, 类型={ext_lower}")

        pr = self.parse_only(file_path, ext, image_save_dir)

        if pr.images and self.enable_vl:
            await self.batch_vl_recognize(pr.images)

        result = self.assemble_with_vl(pr)

        logger.info(
            f"素材内容提取完成: file={path_obj.name}, "
            f"content_length={len(result.text)}字, material_type={result.material_type}"
        )

        return result

    # ── 工具方法 ───────────────────────────────────────────

    @staticmethod
    def _infer_material_type(
        text: str,
        has_images: bool,
        has_tables: bool,
    ) -> str:
        """根据内容推断素材类型

        推断规则:
        - 有表格且表格占比高 -> table
        - 有图片且图片占比高 -> image
        - 其他 -> text
        """
        text_len = len(text)

        if has_tables and not has_images:
            return "table"

        if has_images and not has_tables:
            if text_len < 200:
                return "image"

        if has_tables and has_images:
            return "table"

        return "text"

    @staticmethod
    def _build_material_image_evidence(images: list[ExtractedImage]) -> str:
        """为素材图片生成简短的自然语言证据摘要。"""
        if not images:
            return ""

        evidence_lines: list[str] = []
        for img in images:
            if img.visual_content:
                evidence_lines.append(img.visual_content.split("\n")[0][:180])
            elif img.spatial_context:
                evidence_lines.append(f"与“{img.spatial_context[:120]}”相关的图片材料")

        if not evidence_lines:
            return ""

        deduped: list[str] = []
        for line in evidence_lines:
            if line not in deduped:
                deduped.append(line)

        return "[素材图片证据摘要]\n" + "\n".join(f"- {line}" for line in deduped[:8])

    @staticmethod
    def slice_parse_result(
        pr: ParseResult,
        page_start: int,
        page_end: int,
    ) -> ParseResult:
        """按页范围裁切 ParseResult，仅支持 PDF 素材。"""
        if pr.file_type != "pdf":
            return pr

        selected_pages = [
            page for page in pr.parsed_pages
            if page_start <= page.page_num <= page_end
        ]
        if not selected_pages:
            return ParseResult(
                raw_text="",
                images=[],
                tables=[],
                page_count=0,
                parsed_pages=[],
                parsed_document=None,
                file_type="pdf",
            )

        selected_images = [
            img for img in pr.images
            if page_start <= getattr(img, "page_num", 0) <= page_end
        ]
        selected_tables = [
            table for table in pr.tables
            if page_start <= int(getattr(table, "page_num", 0) or 0) <= page_end
        ]
        raw_text = "\n\n".join(page.text or "" for page in selected_pages)

        parent_doc = pr.parsed_document
        sliced_doc = ParsedDocument(
            file_path=parent_doc.file_path if parent_doc else "",
            filename=parent_doc.filename if parent_doc else "",
            page_count=len(selected_pages),
            pages=selected_pages,
            metadata=dict(parent_doc.metadata) if parent_doc else {},
        )

        return ParseResult(
            raw_text=raw_text,
            images=selected_images,
            tables=selected_tables,
            page_count=len(selected_pages),
            parsed_pages=selected_pages,
            parsed_document=sliced_doc,
            file_type="pdf",
        )
