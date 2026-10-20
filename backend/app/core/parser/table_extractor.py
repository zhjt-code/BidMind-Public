"""表格提取器

从PDF页面中提取表格数据，保持结构完整。
"""

import pdfplumber
from dataclasses import dataclass
from typing import Optional, Any
from loguru import logger

from app.core.config import settings
from app.core.parser.script_char_lifter import chars_in_bbox, lift_page_script_chars, text_from_chars
from app.core.parser.table_linearizer import join_cell_lines_cjk_aware


@dataclass
class ExtractedTable:
    """提取的表格数据"""
    table_index: int  # 表格在页面中的索引（从0开始）
    page_num: int  # 所在页码（从1开始）
    data: list[list[str]]  # 表格数据（二维数组）
    row_count: int  # 行数
    col_count: int  # 列数
    bbox: Optional[tuple[float, float, float, float]] = None  # 边界框 (x0, y0, x1, y1)
    # 单元格bbox槽网格(结构真源): 与data同形, 槽值为anchor单元格bbox
    # (x0,top,x1,bottom); None=该槽被合并单元格覆盖(rowspan/colspan语义的物理证据)。
    # 旧序列化数据无此字段时为 None, 消费方必须回退文本启发式。
    cell_grid: Optional[list[list[Optional[tuple[float, float, float, float]]]]] = None
    
    @property
    def headers(self) -> list[str]:
        """获取表头（第一行）
        
        Returns:
            list[str]: 表头列表
        """
        if self.data and len(self.data) > 0:
            return self.data[0]
        return []
    
    @property
    def body(self) -> list[list[str]]:
        """获取表体（除第一行外的所有行）
        
        Returns:
            list[list[str]]: 表体数据
        """
        if self.data and len(self.data) > 1:
            return self.data[1:]
        return []
    
    def to_markdown(self) -> str:
        """转换为Markdown表格格式
        
        Returns:
            str: Markdown格式的表格
        """
        if not self.data:
            return ""
        
        lines = []
        
        # 表头
        headers = self.headers
        lines.append("| " + " | ".join(str(h) if h else "" for h in headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        
        # 表体
        for row in self.body:
            # 确保行的列数与表头一致
            padded_row = row + [""] * (len(headers) - len(row))
            lines.append("| " + " | ".join(str(cell) if cell else "" for cell in padded_row[:len(headers)]) + " |")
        
        return "\n".join(lines)
    
    def to_text(self, separator: str = "\t") -> str:
        """转换为纯文本格式
        
        Args:
            separator: 列分隔符，默认制表符
            
        Returns:
            str: 纯文本格式的表格
        """
        if not self.data:
            return ""
        
        lines = []
        for row in self.data:
            line = separator.join(str(cell) if cell else "" for cell in row)
            lines.append(line)
        
        return "\n".join(lines)
    
    def get_column(self, col_index: int) -> list[str]:
        """获取指定列的数据
        
        Args:
            col_index: 列索引（从0开始）
            
        Returns:
            list[str]: 该列的所有数据
        """
        if col_index < 0 or col_index >= self.col_count:
            return []
        return [row[col_index] if col_index < len(row) else "" for row in self.data]
    
    def get_column_by_header(self, header: str) -> list[str]:
        """通过表头名称获取列数据
        
        Args:
            header: 表头名称
            
        Returns:
            list[str]: 该列的数据（不包含表头）
        """
        if not self.headers:
            return []
        
        try:
            col_index = self.headers.index(header)
            return [row[col_index] if col_index < len(row) else "" for row in self.body]
        except ValueError:
            return []


class TableExtractor:
    """表格提取器
    
    从PDF页面中识别和提取表格，支持复杂表格结构。
    
    示例:
        >>> extractor = TableExtractor()
        >>> tables = extractor.extract_from_page(page, page_num=1)
        >>> for table in tables:
        ...     print(table.to_markdown())
    """
    
    def __init__(
        self,
        min_rows: int = 2,
        min_cols: int = 2,
        vertical_strategy: str = "lines",
        horizontal_strategy: str = "lines"
    ):
        """初始化表格提取器
        
        Args:
            min_rows: 最小行数，少于此值的不视为表格
            min_cols: 最小列数，少于此值的不视为表格
            vertical_strategy: 垂直线检测策略 ("lines", "lines_strict", "text")
            horizontal_strategy: 水平线检测策略 ("lines", "lines_strict", "text")
        """
        self.min_rows = min_rows
        self.min_cols = min_cols
        self.table_settings = {
            "vertical_strategy": vertical_strategy,
            "horizontal_strategy": horizontal_strategy,
            "snap_tolerance": 3,
            "join_tolerance": 3,
            "edge_min_length": 3,
            "min_words_vertical": 3,
            "min_words_horizontal": 1,
        }
    
    def extract_from_page(
        self,
        page: pdfplumber.page.Page,
        page_num: int
    ) -> list[ExtractedTable]:
        """从页面提取所有表格
        
        Args:
            page: pdfplumber页面对象
            page_num: 页码（从1开始）
            
        Returns:
            list[ExtractedTable]: 提取的表格列表
        """
        tables: list[ExtractedTable] = []
        
        try:
            # 查找表格
            found_tables = page.find_tables(table_settings=self.table_settings)
            
            for idx, table in enumerate(found_tables):
                extracted = self._extract_table(table, idx, page_num)
                if extracted:
                    tables.append(extracted)
            
            if tables:
                logger.debug(f"第 {page_num} 页发现 {len(tables)} 个表格")
                
        except Exception as e:
            logger.warning(f"第 {page_num} 页表格提取失败: {e}")
        
        return tables
    
    def _extract_table(
        self,
        table: pdfplumber.table.Table,
        table_index: int,
        page_num: int
    ) -> Optional[ExtractedTable]:
        """提取单个表格
        
        Args:
            table: pdfplumber表格对象
            table_index: 表格索引
            page_num: 页码
            
        Returns:
            ExtractedTable: 提取的表格，如果不符合条件则返回None
        """
        try:
            # 提取表格数据
            data = table.extract()
            
            if not data:
                return None
            
            # 单元格bbox槽网格: 与data行槽对齐, 承载合并单元格跨度物理证据
            raw_cell_grid = self._extract_cell_grid(table, expected_rows=len(data))
            data = self._repair_script_cells(table, data, raw_cell_grid)
            
            # 清理数据(与cell_grid并行, 保持行对齐)
            cleaned_data, cleaned_grid = self._clean_table_data_with_cells(
                data, raw_cell_grid
            )
            
            if not cleaned_data:
                return None
            
            row_count = len(cleaned_data)
            col_count = max(len(row) for row in cleaned_data) if cleaned_data else 0
            
            # 检查是否满足最小行列要求
            if row_count < self.min_rows or col_count < self.min_cols:
                return None
            
            return ExtractedTable(
                table_index=table_index,
                page_num=page_num,
                data=cleaned_data,
                row_count=row_count,
                col_count=col_count,
                bbox=table.bbox,
                cell_grid=cleaned_grid,
            )
            
        except Exception as e:
            logger.warning(f"提取表格 {table_index} 失败: {e}")
            return None

    @staticmethod
    def _repair_script_cells(
        table: pdfplumber.table.Table,
        data: list[list[Any]],
        cell_grid: Optional[list[list[Optional[tuple[float, float, float, float]]]]],
    ) -> list[list[Any]]:
        """用修正 chars 重建有 bbox 的单元格文本，失败时保留原值。"""
        if not settings.PARSER_SCRIPT_LIFT_ENABLED or not data or not cell_grid:
            return data
        try:
            page = getattr(table, "page", None)
            page_chars = list(getattr(page, "chars", None) or [])
            if not page_chars:
                return data
            lifted_chars = lift_page_script_chars(page_chars)
            repaired: list[list[Any]] = []
            for row_idx, row in enumerate(data):
                repaired_row: list[Any] = []
                for col_idx, cell in enumerate(row):
                    bbox = None
                    if row_idx < len(cell_grid) and col_idx < len(cell_grid[row_idx]):
                        bbox = cell_grid[row_idx][col_idx]
                    if bbox is None:
                        repaired_row.append(cell)
                        continue
                    text = text_from_chars(chars_in_bbox(lifted_chars, bbox))
                    repaired_row.append(text if text else cell)
                repaired.append(repaired_row)
            return repaired
        except Exception as exc:
            logger.debug("表格单元格上下标修复回退原始文本: {}", exc)
            return data
    
    @staticmethod
    def _extract_cell_grid(
        table: pdfplumber.table.Table,
        expected_rows: int,
    ) -> Optional[list[list[Optional[tuple[float, float, float, float]]]]]:
        """从 pdfplumber Table.rows 读取单元格bbox槽网格; 行数不齐时返回 None(回退文本启发)。"""
        try:
            rows = getattr(table, "rows", None) or []
            if len(rows) != expected_rows:
                return None
            grid: list[list[Optional[tuple[float, float, float, float]]]] = []
            for row in rows:
                slots: list[Optional[tuple[float, float, float, float]]] = []
                for cell in row.cells:
                    if cell is None:
                        slots.append(None)
                    else:
                        slots.append(tuple(round(float(v), 1) for v in cell))
                grid.append(slots)
            return grid
        except Exception:
            return None
    
    def _clean_table_data_with_cells(
        self,
        data: list[list[Any]],
        cell_grid: Optional[list[list[Optional[tuple[float, float, float, float]]]]],
    ) -> tuple[
        list[list[str]],
        Optional[list[list[Optional[tuple[float, float, float, float]]]]],
    ]:
        """清理表格数据并同步剔除cell_grid对应行, 保证两者行对齐。"""
        cleaned: list[list[str]] = []
        cleaned_cells: list[list[Optional[tuple[float, float, float, float]]]] = []
        has_grid = cell_grid is not None and len(cell_grid) == len(data)
        for row_idx, row in enumerate(data):
            cleaned_row = []
            for cell in row:
                if cell is None:
                    cleaned_row.append("")
                else:
                    cell_str = join_cell_lines_cjk_aware(str(cell).strip())
                    cleaned_row.append(cell_str)
            # 检查是否为全空行(与既有 _clean_table_data 同判据)
            if any(cell for cell in cleaned_row):
                cleaned.append(cleaned_row)
                if has_grid:
                    cleaned_cells.append(list(cell_grid[row_idx]))
        return cleaned, (cleaned_cells if has_grid else None)
    
    def _clean_table_data(self, data: list[list[Any]]) -> list[list[str]]:
        """清理表格数据
        
        - 将None转换为空字符串
        - 去除单元格前后空白
        - 移除全空行
        
        Args:
            data: 原始表格数据
            
        Returns:
            list[list[str]]: 清理后的数据
        """
        cleaned = []
        
        for row in data:
            cleaned_row = []
            for cell in row:
                if cell is None:
                    cleaned_row.append("")
                else:
                    # 转换为字符串并清理
                    cell_str = join_cell_lines_cjk_aware(str(cell).strip())
                    cleaned_row.append(cell_str)
            
            # 检查是否为全空行
            if any(cell for cell in cleaned_row):
                cleaned.append(cleaned_row)
        
        return cleaned
    
    def extract_from_pdf(
        self,
        pdf_path: str,
        page_numbers: Optional[list[int]] = None
    ) -> list[ExtractedTable]:
        """从整个PDF文件提取表格
        
        Args:
            pdf_path: PDF文件路径
            page_numbers: 指定页码列表（从1开始），None表示所有页
            
        Returns:
            list[ExtractedTable]: 所有提取的表格
        """
        all_tables: list[ExtractedTable] = []
        
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for idx, page in enumerate(pdf.pages):
                    page_num = idx + 1
                    
                    # 如果指定了页码，检查是否在范围内
                    if page_numbers and page_num not in page_numbers:
                        continue
                    
                    tables = self.extract_from_page(page, page_num)
                    all_tables.extend(tables)
            
            logger.info(f"从 {pdf_path} 共提取 {len(all_tables)} 个表格")
            
        except Exception as e:
            logger.error(f"从PDF提取表格失败: {e}")
        
        return all_tables
