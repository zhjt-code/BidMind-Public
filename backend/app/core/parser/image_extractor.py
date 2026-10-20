"""图片提取器

使用PyMuPDF(fitz)从PDF页面中提取图片元数据和图片文件。
提取的图片信息将以文本描述形式追加到页面内容中，
使AI审查时能感知PDF中图片附件的存在。
"""

import fitz
import re as _re
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
from loguru import logger

from app.core.parser.page_image_evidence import (
    dedupe_images_for_display,
    dedupe_visual_texts,
)
from app.core.parser.page_image_paint_gate import (
    count_painted_instances,
    resolve_painted_identities,
    select_page_image_xrefs,
)

IMAGE_ANNOTATION_MARKER = "[图片"
PAGE_IMAGE_SUMMARY_MARKER = "[本页附件图片"
IMAGE_EVIDENCE_MARKER = "[投标证明材料]"


def is_annotation_chunk_content(content: str) -> bool:
    """判断文本是否为结构化图片标注 chunk。"""
    if not content:
        return False
    stripped = content.lstrip()
    return (
        IMAGE_ANNOTATION_MARKER in stripped
        or PAGE_IMAGE_SUMMARY_MARKER in stripped
    )


def is_evidence_chunk_content(content: str) -> bool:
    """判断文本是否为自然语言图片证据 chunk。"""
    if not content:
        return False
    stripped = content.lstrip()
    return IMAGE_EVIDENCE_MARKER in stripped


def is_image_related_chunk_content(content: str) -> bool:
    """统一判断是否属于图片相关 chunk。"""
    return is_annotation_chunk_content(content) or is_evidence_chunk_content(content)


@dataclass
class ExtractedImage:
    """提取的图片数据"""
    image_index: int  # 图片在页面中的索引（从0开始）
    page_num: int  # 所在页码（从1开始）
    width: int  # 图片宽度（像素）
    height: int  # 图片高度（像素）
    image_format: str  # 图片格式（jpeg, png等）
    xref: int  # PDF内部引用ID（用于去重）
    bbox: Optional[tuple[float, float, float, float]] = None  # 在页面中的位置 (x0, y0, x1, y1)
    file_path: Optional[str] = None  # 保存后的文件路径
    file_size: int = 0  # 文件大小（字节）
    spatial_context: str = ""  # 基于空间位置提取的紧邻文字（逐图独立）
    visual_content: str = ""  # VL模型识别的图片内容（由ImageAnalyzer填充）

    @property
    def size_category(self) -> str:
        """根据尺寸推断图片类别

        Returns:
            str: 图片类别描述
        """
        area = self.width * self.height
        if area >= 500 * 700:
            return "大幅扫描件"
        elif area >= 200 * 200:
            return "中等图片"
        else:
            return "小图标"

    @property
    def position_description(self) -> str:
        """根据bbox推断图片在页面中的位置

        Returns:
            str: 位置描述
        """
        if not self.bbox:
            return "位置未知"

        _, y0, _, y1 = self.bbox
        # 假设标准A4页面高度约842点
        page_height = 842.0
        center_y = (y0 + y1) / 2

        if center_y < page_height * 0.33:
            return "页面上部"
        elif center_y < page_height * 0.66:
            return "页面中部"
        else:
            return "页面下部"

    @property
    def doc_type_hint(self) -> str:
        """根据尺寸推断可能的文档类型提示

        大幅扫描件通常是证书、执照、合同等材料的复印件;
        中等图片可能是照片、图表、签章等。

        Returns:
            str: 文档类型提示
        """
        area = self.width * self.height
        if area >= 500 * 700:
            return "可能为证书、执照、资质证明、合同、报告等材料的扫描件或复印件"
        elif area >= 200 * 200:
            return "可能为照片、图表、签章或其他证明材料"
        else:
            return "小型图片"

    @property
    def search_keywords(self) -> str:
        """生成用于提升语义检索命中率的关键词

        针对大幅扫描件（通常是证书/执照复印件），注入高频搜索关键词，
        使向量化后的chunk更容易被"提供XXX复印件"类查询命中。

        Returns:
            str: 搜索友好关键词，小图返回空字符串
        """
        area = self.width * self.height
        if area >= 500 * 700:
            return "附件材料 复印件 扫描件 证明文件"
        elif area >= 200 * 200:
            return "附件图片 证明材料"
        return ""

    def to_text_annotation(self, context: str = "") -> str:
        """生成文本描述，用于追加到页面文本中

        信息来源优先级:
        1. visual_content (VL模型识别结果, 精度最高)
        2. spatial_context (基于空间位置的逐图文本映射)
        3. context (基于页面级pdfplumber文本的共享上下文)

        Args:
            context: 从附近页面提取的共享上下文文本（降级方案）

        Returns:
            str: 图片的文本描述
        """
        size_info = f"{self.width}x{self.height}"
        category = self.size_category
        position = self.position_description
        keywords = self.search_keywords

        parts = [f"{IMAGE_ANNOTATION_MARKER}{self.image_index + 1}: {category}"]

        # 优先级1: VL模型识别内容（最精确, 能读取图片中的实际文字）
        if self.visual_content:
            parts.append(f", VL识别: \"{self.visual_content}\"")
            if self.spatial_context:
                parts.append(f", 紧邻文字: \"{self.spatial_context}\"")
        # 优先级2: 空间定位的逐图上下文
        elif self.spatial_context:
            parts.append(f", 紧邻文字: \"{self.spatial_context}\"")
        # 优先级3: 共享上下文 + 通用类型提示
        else:
            doc_hint = self.doc_type_hint
            parts.append(f"({doc_hint})")
            if context:
                parts.append(f", 上下文关联: \"{context}\"")

        parts.append(f", 尺寸{size_info}, {position}")

        if keywords:
            parts.append(f", {keywords}")
        parts.append("]")

        return "".join(parts)


class ImageExtractor:
    """图片提取器

    从PDF页面中提取图片，支持过滤装饰性小图，
    保存图片文件并返回元数据信息。

    示例:
        >>> extractor = ImageExtractor()
        >>> with fitz.open("document.pdf") as doc:
        ...     page = doc[0]
        ...     images = extractor.extract_from_page(doc, page, page_num=1)
        ...     for img in images:
        ...         print(img.to_text_annotation())
    """

    # 最小图片尺寸阈值（像素），低于此值视为装饰性图片
    DEFAULT_MIN_WIDTH = 100
    DEFAULT_MIN_HEIGHT = 100
    # 最小面积阈值（像素^2）
    DEFAULT_MIN_AREA = 15000

    def __init__(
        self,
        min_width: int = DEFAULT_MIN_WIDTH,
        min_height: int = DEFAULT_MIN_HEIGHT,
        min_area: int = DEFAULT_MIN_AREA,
    ):
        """初始化图片提取器

        Args:
            min_width: 最小宽度（像素），低于此值的图片将被过滤
            min_height: 最小高度（像素），低于此值的图片将被过滤
            min_area: 最小面积（像素^2），低于此值的图片将被过滤
        """
        self.min_width = min_width
        self.min_height = min_height
        self.min_area = min_area

    def extract_from_page(
        self,
        doc: fitz.Document,
        page: fitz.Page,
        page_num: int,
        save_dir: Optional[Path] = None,
    ) -> list[ExtractedImage]:
        """从单个页面提取所有符合条件的图片

        对每张图片，基于空间位置查找其紧邻文本（如设备名称、
        章节标题），存入 spatial_context 字段，使LLM能精确
        识别每张图片的内容。

        Args:
            doc: fitz文档对象（用于提取图片字节）
            page: fitz页面对象
            page_num: 页码（从1开始）
            save_dir: 图片保存目录，为None时不保存文件

        Returns:
            list[ExtractedImage]: 提取的图片列表
        """
        images: list[ExtractedImage] = []
        seen_xrefs: set[int] = set()

        try:
            image_list = page.get_images(full=True)
            listed_xrefs = [img_info[0] for img_info in image_list]
            try:
                cheap_blocks = page.get_image_info(xrefs=False) or []
            except Exception:
                cheap_blocks = []
            painted_identities = resolve_painted_identities(image_list, cheap_blocks)
            allowed_xrefs = select_page_image_xrefs(
                listed_xrefs,
                painted_identities.keys(),
                painted_instance_count=count_painted_instances(cheap_blocks),
            )

            # 预提取页面文本块位置信息（用于逐图空间映射）
            text_blocks = self._get_page_text_blocks(page)

            for img_index, img_info in enumerate(image_list):
                xref = img_info[0]

                # 基于xref去重（同一图片可能在页面中出现多次）
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                if xref not in allowed_xrefs:
                    continue

                extracted = self._extract_single_image(
                    doc=doc,
                    page=page,
                    img_info=img_info,
                    img_index=len(images),
                    page_num=page_num,
                    save_dir=save_dir,
                )

                if extracted:
                    # 基于空间位置为每张图片匹配最近的文本标签
                    if text_blocks and extracted.bbox:
                        extracted.spatial_context = self._find_nearest_text(
                            text_blocks, extracted.bbox
                        )
                    images.append(extracted)

            if images:
                spatial_count = sum(1 for img in images if img.spatial_context)
                logger.debug(
                    f"第 {page_num} 页提取 {len(images)} 张图片 "
                    f"(过滤前 {len(image_list)} 张, "
                    f"{spatial_count} 张有空间上下文)"
                )
            elif image_list and not allowed_xrefs:
                logger.debug(
                    f"第 {page_num} 页跳过资源泄漏抽图 "
                    f"(listed={len(image_list)}, painted={len(painted_identities)})"
                )

        except Exception as e:
            logger.warning(f"第 {page_num} 页图片提取失败: {e}")

        return images

    @staticmethod
    def _get_page_text_blocks(
        page: fitz.Page,
    ) -> list[tuple[float, float, float, float, str]]:
        """提取页面文本块及其位置信息

        使用fitz的文本块提取API获取带坐标的文本块，
        用于后续的图片-文本空间映射。

        坐标系: 原点(0,0)在页面左上角, Y轴向下增长。

        Args:
            page: fitz页面对象

        Returns:
            list[tuple]: 文本块列表，每项为 (x0, y0, x1, y1, text)
        """
        try:
            blocks = page.get_text("blocks")
            # blocks: list of (x0, y0, x1, y1, text, block_no, block_type)
            # block_type: 0=text, 1=image
            return [
                (b[0], b[1], b[2], b[3], b[4].strip())
                for b in blocks
                if b[6] == 0 and b[4].strip()
            ]
        except Exception as e:
            logger.debug(f"提取页面文本块失败: {e}")
            return []

    @staticmethod
    def _find_nearest_text(
        text_blocks: list[tuple[float, float, float, float, str]],
        image_bbox: tuple[float, float, float, float],
        max_distance: float = 400.0,
    ) -> str:
        """查找图片上方（或下方）最近的文本块

        标书排版习惯: 先有章节标题/设备名称等文字标签, 后面跟图片。
        本方法根据Y坐标距离找到距离图片最近的文本块, 建立
        精确的"图片→文字标签"映射关系。

        示例:
            页面布局:
                "(4) X射线衍射仪（XRD）"  ← 文本块, y1=100
                [设备照片]                  ← 图片, y0=110
            → 返回 "(4) X射线衍射仪（XRD）"

        Args:
            text_blocks: 文本块列表 [(x0, y0, x1, y1, text), ...]
            image_bbox: 图片边界框 (x0, y0, x1, y1)
            max_distance: 最大搜索距离（PDF点数, 200≈70mm）

        Returns:
            str: 最近文本内容, 未找到返回空字符串
        """
        if not text_blocks or not image_bbox:
            return ""

        img_x0, img_y0, img_x1, img_y1 = image_bbox

        # 策略1: 查找图片上方的文本块（最常见: 标题在图片正上方）
        above_candidates: list[tuple[float, str]] = []
        for bx0, by0, bx1, by1, text in text_blocks:
            # 文本块底边在图片顶边之上（5pt容差, 处理微小重叠）
            if by1 <= img_y0 + 5:
                distance = img_y0 - by1
                if distance <= max_distance:
                    above_candidates.append((distance, text))

        if above_candidates:
            above_candidates.sort(key=lambda x: x[0])
            nearest_text = above_candidates[0][1]
            # 如果最近文本过短（<3字符, 可能是编号或符号）, 合并上一个文本块
            if len(nearest_text) < 3 and len(above_candidates) > 1:
                nearest_text = above_candidates[1][1] + " " + nearest_text
            return nearest_text[:300]

        # 策略2: 图片上方没有文本, 退回查找下方文本
        below_candidates: list[tuple[float, str]] = []
        for bx0, by0, bx1, by1, text in text_blocks:
            # 文本块顶边在图片底边之下
            if by0 >= img_y1 - 5:
                distance = by0 - img_y1
                if distance <= max_distance:
                    below_candidates.append((distance, text))

        if below_candidates:
            below_candidates.sort(key=lambda x: x[0])
            return below_candidates[0][1][:300]

        return ""

    def _extract_single_image(
        self,
        doc: fitz.Document,
        page: fitz.Page,
        img_info: tuple,
        img_index: int,
        page_num: int,
        save_dir: Optional[Path] = None,
    ) -> Optional[ExtractedImage]:
        """提取单张图片

        Args:
            doc: fitz文档对象
            page: fitz页面对象
            img_info: fitz图片信息元组
            img_index: 图片索引
            page_num: 页码
            save_dir: 保存目录

        Returns:
            ExtractedImage: 提取的图片，如果不符合条件则返回None
        """
        xref = img_info[0]

        try:
            # 提取图片基础信息
            base_image = doc.extract_image(xref)
            if not base_image:
                return None

            width = base_image["width"]
            height = base_image["height"]
            image_format = base_image.get("ext", "png")
            image_bytes = base_image["image"]
            smask_xref = int(base_image.get("smask") or 0)
            if smask_xref:
                try:
                    pixmap = fitz.Pixmap(doc, xref)
                    mask = fitz.Pixmap(doc, smask_xref)
                    pixmap = fitz.Pixmap(pixmap, mask)
                    image_bytes = pixmap.tobytes("png")
                    image_format = "png"
                    width = pixmap.width
                    height = pixmap.height
                except Exception as exc:  # noqa: BLE001 保留原图继续解析
                    logger.debug(f"图片 xref={xref} SMask合成失败, 使用原始图: {exc}")

            # 尺寸过滤：排除装饰性小图
            if not self._passes_size_filter(width, height):
                return None

            bbox = self._get_image_bbox(page, xref)

            # 保存图片文件
            file_path = None
            if save_dir and image_bytes:
                file_path = self._save_image(
                    image_bytes=image_bytes,
                    save_dir=save_dir,
                    page_num=page_num,
                    img_index=img_index,
                    image_format=image_format,
                )

            return ExtractedImage(
                image_index=img_index,
                page_num=page_num,
                width=width,
                height=height,
                image_format=image_format,
                xref=xref,
                bbox=bbox,
                file_path=str(file_path) if file_path else None,
                file_size=len(image_bytes) if image_bytes else 0,
            )

        except Exception as e:
            logger.warning(f"提取图片 xref={xref} 失败: {e}")
            return None

    def _passes_size_filter(self, width: int, height: int) -> bool:
        """检查图片是否通过尺寸过滤

        Args:
            width: 图片宽度
            height: 图片高度

        Returns:
            bool: 是否通过过滤
        """
        if width < self.min_width or height < self.min_height:
            return False
        if width * height < self.min_area:
            return False
        return True

    def _get_image_bbox(
        self, page: fitz.Page, xref: int
    ) -> Optional[tuple[float, float, float, float]]:
        """获取图片在页面中的位置

        Args:
            page: fitz页面对象
            xref: 图片xref

        Returns:
            tuple: 边界框 (x0, y0, x1, y1)，获取失败返回None
        """
        try:
            listed = page.get_images(full=True)
            cheap_blocks = page.get_image_info(xrefs=False) or []
            return resolve_painted_identities(listed, cheap_blocks).get(int(xref))
        except Exception:
            return None

    def _save_image(
        self,
        image_bytes: bytes,
        save_dir: Path,
        page_num: int,
        img_index: int,
        image_format: str,
    ) -> Optional[Path]:
        """保存图片到文件

        Args:
            image_bytes: 图片字节数据
            save_dir: 保存目录
            page_num: 页码
            img_index: 图片索引
            image_format: 图片格式

        Returns:
            Path: 保存的文件路径，失败返回None
        """
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
            filename = f"p{page_num}_img{img_index + 1}.{image_format}"
            file_path = save_dir / filename

            with open(file_path, "wb") as f:
                f.write(image_bytes)

            return file_path

        except Exception as e:
            logger.warning(f"保存图片失败: {e}")
            return None

    def extract_from_pdf(
        self,
        pdf_path: str | Path,
        save_dir: Optional[Path] = None,
    ) -> dict[int, list[ExtractedImage]]:
        """从整个PDF文件提取图片

        Args:
            pdf_path: PDF文件路径
            save_dir: 图片保存目录

        Returns:
            dict[int, list[ExtractedImage]]: 按页码分组的图片字典 {page_num: [images]}
        """
        pdf_path = Path(pdf_path)
        result: dict[int, list[ExtractedImage]] = {}
        total_count = 0

        try:
            doc = fitz.open(str(pdf_path))

            for page_idx in range(len(doc)):
                page = doc[page_idx]
                page_num = page_idx + 1

                images = self.extract_from_page(
                    doc=doc,
                    page=page,
                    page_num=page_num,
                    save_dir=save_dir,
                )

                if images:
                    result[page_num] = images
                    total_count += len(images)

            doc.close()

            logger.info(
                f"从 {pdf_path.name} 共提取 {total_count} 张图片, "
                f"分布在 {len(result)} 个页面"
            )

        except Exception as e:
            logger.error(f"从PDF提取图片失败: {e}")

        return result


# 标书中常见的"引用附件/扫描件"锚点模式
_ANCHOR_PATTERNS = _re.compile(
    r"(见扫描件|详见附件|见附件|复印件见|扫描件见|见下[页面]|"
    r"如下图|见下图|附件如下|附后|见后|证书[如详]下|资质[如详]下|"
    r"以下[为是].*?[扫复]印件|证明材料[如详]下|"
    r"参[考见]后附|详见投标文件|材料附后|资料如下|"
    r"[如详]见第\d+页|提供了.*?[复扫]印件|请[参详]阅附件|"
    r"证[照件]如下|附件材料如下)",
    _re.IGNORECASE,
)


def insert_annotation_near_anchor(
    page_text: str,
    image_annotation: str,
) -> str:
    """将图片标注插入到页面文本中引用锚点之后

    标书中常见"见扫描件"/"详见附件"等引用语。如果将图片标注
    插入到这些引用点之后(而非统一追加到末尾), 可以使图片信息
    与引用文本在同一chunk中, 避免被RecursiveCharacterTextSplitter
    切割到不同分块。

    当找不到锚点时, 保持原有行为(追加到末尾)。

    Args:
        page_text: 原始页面文本(pdfplumber提取)
        image_annotation: 由 generate_image_annotations() 生成的标注

    Returns:
        str: 插入标注后的页面文本
    """
    if not page_text or not image_annotation:
        return page_text + "\n" + image_annotation if image_annotation else page_text

    match = _ANCHOR_PATTERNS.search(page_text)
    if not match:
        return page_text + "\n" + image_annotation

    # 找到锚点所在行的末尾
    anchor_end = match.end()
    newline_pos = page_text.find("\n", anchor_end)
    if newline_pos == -1:
        insert_pos = len(page_text)
    else:
        insert_pos = newline_pos

    return (
        page_text[:insert_pos]
        + "\n"
        + image_annotation
        + "\n"
        + page_text[insert_pos:]
    )


def extract_context_for_images(
    current_page_text: str,
    prev_page_text: str = "",
    max_length: int = 400,
) -> str:
    """从当前页和前一页的文本中提取图片的共享上下文信息

    此函数提取页面级的共享上下文，作为 spatial_context（逐图空间
    映射）不可用时的降级方案。

    标书排版习惯：先有标题/说明文字，后面跟图片。
    因此从当前页文本（始终保留）和前一页末尾文本（补充）
    中提取与图片相关的上下文，作为图片标注的语义关键词。

    关键逻辑：当前页文本无论多短都保留（如"六、营业执照"仅6字
    但语义价值极高），文字不足时用前一页补充而非替换。

    Args:
        current_page_text: 当前页面的文本
        prev_page_text: 前一页的文本（当前页文字不足时补充使用）
        max_length: 上下文最大字符数（默认400，覆盖多设备页面）

    Returns:
        str: 提取的上下文文本，可能为空
    """
    current_stripped = current_page_text.strip()
    prev_stripped = prev_page_text.strip() if prev_page_text else ""

    # 策略：始终保留当前页文本，文字不足时用前一页补充（而非替换）
    if current_stripped:
        if len(current_stripped) < 20 and prev_stripped:
            # 当前页文字很短但有价值，用前一页末尾补充语义
            source_text = current_stripped + "\n" + prev_stripped
        else:
            source_text = current_stripped
    elif prev_stripped:
        # 当前页完全无文字，退回前一页
        source_text = prev_stripped
    else:
        return ""

    # 取文本的末尾部分（标题/说明通常在图片前面，即文本末尾）
    # 按行分割，取末尾非空行
    lines = [line.strip() for line in source_text.split("\n") if line.strip()]
    if not lines:
        return ""

    # 对于短文本页面（如纯标题页"六、营业执照"），始终包含第一行
    # 因为第一行往往是当前页的标题，语义价值最高
    first_line = lines[0] if len(current_stripped) < 20 and current_stripped else None

    # 从末尾向前取，拼接到 max_length 以内
    context_lines = []
    total_len = 0
    for line in reversed(lines):
        if total_len + len(line) > max_length:
            break
        context_lines.insert(0, line)
        total_len += len(line)

    # 如果一行都放不下，截取最后一行
    if not context_lines and lines:
        context_lines = [lines[-1][:max_length]]

    # 确保当前页标题不被遗漏（短文本页面的关键语义）
    if first_line and first_line not in context_lines:
        context_lines.insert(0, first_line)

    return " ".join(context_lines)


def generate_image_annotations(
    images: list[ExtractedImage],
    context: str = "",
    full_page_text: str = "",
) -> str:
    """为一组图片生成文本标注，用于追加到页面文本

 标注分三层:
 1. 页面级头部: 概述本页图片数量和涉及内容
 2. 逐图标注: 每张图片的具体信息和空间上下文
 3. 自然语言摘要: 搜索友好的证据描述(提升向量检索命中率)

 Args:
 images: 该页面的图片列表
 context: 从附近页面提取的共享上下文文本（降级方案）
 full_page_text: 整页OCR/VL识别文本(: 头部标签取其首行)

 Returns:
 str: 图片标注文本，如果没有图片则返回空字符串
 """
    if not images:
        return ""

    has_large_scan = any(img.size_category == "大幅扫描件" for img in images)
    has_visual = bool(full_page_text) or any(img.visual_content for img in images)
    has_spatial = any(img.spatial_context for img in images)

    header = f"\n{PAGE_IMAGE_SUMMARY_MARKER}: 共{len(images)}张"

    if has_visual:
        visual_labels: list[str] = []
        if full_page_text:
            visual_labels.append(full_page_text.split("\n")[0][:60])
        for img in images:
            if img.visual_content:
                label = img.visual_content.split("\n")[0][:60]
                if label not in visual_labels:
                    visual_labels.append(label)
        if visual_labels:
            labels_text = "; ".join(visual_labels[:4])
            header += f", VL识别: {labels_text}"
    elif has_spatial:
        spatial_labels: list[str] = []
        for img in images:
            if img.spatial_context:
                label = img.spatial_context.split("\n")[0][:50]
                if label not in spatial_labels:
                    spatial_labels.append(label)
        if spatial_labels:
            labels_text = "; ".join(spatial_labels[:6])
            header += f", 涉及: {labels_text}"
    elif context:
        header += f", 上下文关联: \"{context}\""

    if has_large_scan:
        header += ", 含扫描件附件材料(可能为证书、执照、资质或合同复印件)"
    header += "]"

    # 重复 visual_content 的图片不重复展示相同 VL 内容(首张保留)
    display_images = dedupe_images_for_display(images)

    lines = [header]
    for img in display_images:
        lines.append(img.to_text_annotation(context=context))

    # 自然语言摘要: 使向量化后的chunk更容易被招标要求类查询命中
    nl_summary = _build_natural_language_summary(display_images, context)
    if nl_summary:
        lines.append(nl_summary)

    return "\n".join(lines)


def _build_natural_language_summary(
    images: list[ExtractedImage],
    context: str = "",
) -> str:
    """将图片信息转换为自然语言证据摘要

    结构化标注(如 [图片1: VL识别: "xxx"]) 在bge-m3向量空间中
    与招标要求的自然语言描述距离较大, 导致检索命中率低。
    本函数生成一段自然语言摘要, 使图片证据更容易被语义检索找到。

    Args:
        images: 图片列表
        context: 共享上下文

    Returns:
        str: 自然语言摘要, 无有效信息时返回空字符串
    """
    evidence_parts: list[str] = []

    for img in images:
        desc = ""
        if img.visual_content:
            first_line = img.visual_content.split("\n")[0][:120]
            desc = first_line
        elif img.spatial_context:
            desc = img.spatial_context.split("\n")[0][:80]

        if not desc:
            continue

        category = img.size_category
        if category == "大幅扫描件":
            evidence_parts.append(
                f"{desc}的扫描件/复印件(作为投标证明材料)"
            )
        else:
            evidence_parts.append(desc)

    if not evidence_parts:
        if context:
            return (
                f"{IMAGE_EVIDENCE_MARKER} 本页附有{len(images)}张图片附件, "
                f"与以下内容相关: {context[:150]}"
            )
        return ""

    items = "; ".join(f"({i+1}){p}" for i, p in enumerate(evidence_parts[:5]))
    return f"{IMAGE_EVIDENCE_MARKER} 本页提供了以下证明文件的扫描件/复印件: {items}。"


def build_natural_evidence_chunk(
    page_num: int,
    images: list[ExtractedImage],
    context: str,
    full_page_text: str = "",
) -> str:
    """生成纯自然语言的证据描述chunk

 与 generate_image_annotations 不同, 本函数输出纯自然语言,
 不含结构化标注(如 [图片1: ...])。这使得向量化后的embedding
 与招标要求的自然语言描述距离更小, 显著提升语义检索命中率。

 生成的chunk不经过TextChunker切割, 作为独立chunk直接入库。

 Args:
 page_num: 页码
 images: 该页面的图片列表
 context: 前后页的文字上下文
 full_page_text: 整页OCR/VL识别文本(: 只拼一次并参与去重)

 Returns:
 str: 自然语言证据描述, 无有效信息时返回空字符串
 """
    if not images:
        return ""

    parts: list[str] = []
    parts.append(
        f"{IMAGE_EVIDENCE_MARKER} 投标文件第{page_num}页提供了以下证明材料的扫描件或复印件:"
    )

    has_content = False
    if full_page_text:
        parts.append(full_page_text)
        has_content = True

    # 逐图 visual_content 保序去重, 与整页文本重复的只保留页级一份;
    # pending 按首次出现顺序消费, 无重复输入时输出与旧实现逐字一致
    pending_visuals = dedupe_visual_texts(images, exclude=(full_page_text,))
    for img in images:
        if img.visual_content:
            has_content = True
            if pending_visuals and pending_visuals[0] == img.visual_content:
                parts.append(pending_visuals.pop(0))
        elif img.spatial_context:
            category = img.size_category
            if category == "大幅扫描件":
                parts.append(
                    f"一份与\"{img.spatial_context}\"相关的证明文件扫描件"
                )
            else:
                parts.append(f"一份图片材料,相关文字为\"{img.spatial_context}\"")
            has_content = True

    if not has_content:
        if context:
            parts.append(
                f"包含{len(images)}张图片附件(可能为证书、执照、"
                f"资质证明等材料的复印件),与以下内容相关: {context[:200]}"
            )
        else:
            return ""

    # 补充上下文中的章节信息
    if context:
        context_lines = [
            ln.strip() for ln in context.split("\n")
            if ln.strip() and len(ln.strip()) < 40
        ]
        if context_lines:
            section_hint = context_lines[0]
            parts.append(
                f"本页位于投标文件\"{section_hint}\"章节附近。"
            )

    return "\n".join(parts)
