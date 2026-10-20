"""PDF解析模块测试"""

import pytest
from pathlib import Path
import sys

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.parser import PdfParser, TableExtractor, ParsedDocument, ExtractedTable


class TestPdfParser:
    """PdfParser 测试类"""
    
    @pytest.fixture
    def parser(self):
        """创建解析器实例"""
        return PdfParser()
    
    @pytest.fixture
    def sample_pdf_path(self):
        """示例PDF路径"""
        # 使用项目中的测试数据
        return Path(__file__).parent.parent.parent / "trial data" / "招标.pdf"
    
    def test_parser_init(self, parser):
        """测试解析器初始化"""
        assert parser is not None
        assert parser.extract_tables is True
        assert parser.table_extractor is not None
    
    def test_parser_init_no_tables(self):
        """测试不提取表格的初始化"""
        parser = PdfParser(extract_tables=False)
        assert parser.extract_tables is False
        assert parser.table_extractor is None
    
    def test_parse_nonexistent_file(self, parser):
        """测试解析不存在的文件"""
        with pytest.raises(FileNotFoundError):
            parser.parse("nonexistent.pdf")
    
    def test_parse_non_pdf_file(self, parser, tmp_path):
        """测试解析非PDF文件"""
        # 创建一个非PDF文件
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("This is not a PDF")
        
        with pytest.raises(ValueError):
            parser.parse(txt_file)
    
    @pytest.mark.skipif(
        not (Path(__file__).parent.parent.parent / "trial data" / "招标.pdf").exists(),
        reason="测试PDF文件不存在"
    )
    def test_parse_real_pdf(self, parser, sample_pdf_path):
        """测试解析真实PDF文件"""
        doc = parser.parse(sample_pdf_path)
        
        assert isinstance(doc, ParsedDocument)
        assert doc.filename == "招标.pdf"
        assert doc.page_count > 0
        assert len(doc.pages) == doc.page_count
        assert doc.total_char_count > 0
        assert doc.full_text  # 有内容
    
    @pytest.mark.skipif(
        not (Path(__file__).parent.parent.parent / "trial data" / "招标.pdf").exists(),
        reason="测试PDF文件不存在"
    )
    def test_parsed_document_properties(self, parser, sample_pdf_path):
        """测试ParsedDocument属性"""
        doc = parser.parse(sample_pdf_path)
        
        # 测试 full_text
        full_text = doc.full_text
        assert isinstance(full_text, str)
        
        # 测试 all_tables
        all_tables = doc.all_tables
        assert isinstance(all_tables, list)
        
        # 测试页码范围文本
        if doc.page_count >= 2:
            text = doc.get_text_by_page_range(1, 2)
            assert isinstance(text, str)


class TestTableExtractor:
    """TableExtractor 测试类"""
    
    @pytest.fixture
    def extractor(self):
        """创建表格提取器实例"""
        return TableExtractor()
    
    def test_extractor_init(self, extractor):
        """测试提取器初始化"""
        assert extractor is not None
        assert extractor.min_rows == 2
        assert extractor.min_cols == 2
    
    def test_extractor_custom_init(self):
        """测试自定义参数初始化"""
        extractor = TableExtractor(min_rows=3, min_cols=3)
        assert extractor.min_rows == 3
        assert extractor.min_cols == 3


class TestExtractedTable:
    """ExtractedTable 测试类"""
    
    @pytest.fixture
    def sample_table(self):
        """创建示例表格"""
        return ExtractedTable(
            table_index=0,
            page_num=1,
            data=[
                ["序号", "项目名称", "要求"],
                ["1", "资质要求", "具有相关资质"],
                ["2", "技术要求", "满足技术规范"],
            ],
            row_count=3,
            col_count=3
        )
    
    def test_table_headers(self, sample_table):
        """测试表头获取"""
        headers = sample_table.headers
        assert headers == ["序号", "项目名称", "要求"]
    
    def test_table_body(self, sample_table):
        """测试表体获取"""
        body = sample_table.body
        assert len(body) == 2
        assert body[0] == ["1", "资质要求", "具有相关资质"]
    
    def test_table_to_markdown(self, sample_table):
        """测试转换为Markdown"""
        md = sample_table.to_markdown()
        assert "| 序号 | 项目名称 | 要求 |" in md
        assert "| --- | --- | --- |" in md
        assert "| 1 | 资质要求 | 具有相关资质 |" in md
    
    def test_table_to_text(self, sample_table):
        """测试转换为纯文本"""
        text = sample_table.to_text()
        assert "序号\t项目名称\t要求" in text
        assert "1\t资质要求\t具有相关资质" in text
    
    def test_get_column(self, sample_table):
        """测试获取列数据"""
        col = sample_table.get_column(1)
        assert col == ["项目名称", "资质要求", "技术要求"]
    
    def test_get_column_by_header(self, sample_table):
        """测试通过表头获取列"""
        col = sample_table.get_column_by_header("项目名称")
        assert col == ["资质要求", "技术要求"]
    
    def test_empty_table(self):
        """测试空表格"""
        table = ExtractedTable(
            table_index=0,
            page_num=1,
            data=[],
            row_count=0,
            col_count=0
        )
        assert table.headers == []
        assert table.body == []
        assert table.to_markdown() == ""


class TestParsedPage:
    """ParsedPage 测试类"""
    
    def test_page_properties(self):
        """测试页面属性"""
        from app.core.parser.pdf_parser import ParsedPage
        
        page = ParsedPage(
            page_num=1,
            text="这是测试内容",
            tables=[],
            width=595.0,
            height=842.0
        )
        
        assert page.page_num == 1
        assert page.has_tables is False
        assert page.char_count == 6


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
