"""文本分块模块测试"""

import sys
from pathlib import Path

import pytest

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.parser import PdfParser, TextChunk, TextChunker


class TestTextChunker:
    """TextChunker 测试类"""
    
    @pytest.fixture
    def chunker(self):
        """创建分块器实例"""
        return TextChunker()
    
    @pytest.fixture
    def sample_text(self):
        """示例长文本"""
        return """
        第一章 总则
        
        第一条 为了规范招标投标活动，保护国家利益、社会公共利益和招标投标活动当事人的合法权益，提高经济效益，保证项目质量，制定本法。
        
        第二条 在中华人民共和国境内进行招标投标活动，适用本法。
        
        第三条 在中华人民共和国境内进行下列工程建设项目包括项目的勘察、设计、施工、监理以及与工程建设有关的重要设备、材料等的采购，必须进行招标。
        
        第二章 招标
        
        第四条 任何单位和个人不得将依法必须进行招标的项目化整为零或者以其他任何方式规避招标。
        
        第五条 招标投标活动应当遵循公开、公平、公正和诚实信用的原则。
        
        第六条 依法必须进行招标的项目，其招标投标活动不受地区或者部门的限制。任何单位和个人不得违法限制或者排斥本地区、本系统以外的法人或者其他组织参加投标。
        """ * 5  # 重复5次以创建足够长的文本
    
    def test_chunker_init(self, chunker):
        """测试分块器初始化"""
        assert chunker is not None
        assert chunker.chunk_size == 1500
        assert chunker.chunk_overlap == 200
    
    def test_chunker_custom_init(self):
        """测试自定义参数初始化"""
        chunker = TextChunker(chunk_size=1000, chunk_overlap=100)
        assert chunker.chunk_size == 1000
        assert chunker.chunk_overlap == 100
    
    def test_split_empty_text(self, chunker):
        """测试空文本分割"""
        chunks = chunker.split("")
        assert chunks == []
        
        chunks = chunker.split("   ")
        assert chunks == []
    
    def test_split_short_text(self, chunker):
        """测试短文本分割（不需要分块）"""
        short_text = "这是一段短文本。"
        chunks = chunker.split(short_text)
        
        assert len(chunks) == 1
        assert chunks[0].content == short_text
        assert chunks[0].chunk_index == 0
    
    def test_split_long_text(self, chunker, sample_text):
        """测试长文本分割"""
        chunks = chunker.split(sample_text)
        
        assert len(chunks) > 1
        
        # 验证每个分块
        for idx, chunk in enumerate(chunks):
            assert isinstance(chunk, TextChunk)
            assert chunk.chunk_index == idx
            assert chunk.char_count > 0
            assert chunk.content  # 非空
            assert len(chunk.content) <= chunker.chunk_size + 100  # 允许一点误差
    
    def test_split_with_metadata(self, chunker, sample_text):
        """测试带元数据的分割"""
        metadata = {"doc_type": "tender", "project_id": 123}
        chunks = chunker.split(sample_text, metadata=metadata)
        
        assert len(chunks) > 0
        for chunk in chunks:
            assert chunk.metadata["doc_type"] == "tender"
            assert chunk.metadata["project_id"] == 123
    
    def test_chunk_preview(self, chunker, sample_text):
        """测试分块预览"""
        chunks = chunker.split(sample_text)
        
        for chunk in chunks:
            preview = chunk.preview
            assert len(preview) <= 103  # 100 + "..."
            if len(chunk.content) <= 100:
                assert preview == chunk.content
            else:
                assert preview.endswith("...")
    
    def test_estimate_chunk_count(self, chunker, sample_text):
        """测试分块数量估算"""
        estimated = chunker.estimate_chunk_count(sample_text)
        actual_chunks = chunker.split(sample_text)
        
        # 估算值应该接近实际值（允许±50%误差）
        assert estimated > 0
        assert abs(estimated - len(actual_chunks)) <= max(estimated, len(actual_chunks)) * 0.5


class TestTextChunkerWithPageTracking:
    """页码追踪测试类"""
    
    @pytest.fixture
    def chunker(self):
        """创建分块器实例"""
        return TextChunker(chunk_size=500, chunk_overlap=50)
    
    def test_split_with_page_tracking(self, chunker):
        """测试多页分割"""
        pages = [
            (1, "第一页的内容。" * 50),
            (2, "第二页的内容。" * 50),
            (3, "第三页的内容。" * 50),
        ]
        
        chunks = chunker.split_with_page_tracking(pages)
        
        assert len(chunks) > 0
        
        # 验证页码信息
        page_nums = set()
        for chunk in chunks:
            assert "page_num" in chunk.metadata
            page_nums.add(chunk.metadata["page_num"])
        
        # 应该包含所有页码
        assert page_nums == {1, 2, 3}
    
    def test_split_with_page_tracking_empty_pages(self, chunker):
        """测试包含空页的情况"""
        pages = [
            (1, "第一页的内容。" * 50),
            (2, ""),  # 空页
            (3, "   "),  # 只有空白
            (4, "第四页的内容。" * 50),
        ]
        
        chunks = chunker.split_with_page_tracking(pages)
        
        # 空页应该被跳过
        page_nums = {chunk.metadata["page_num"] for chunk in chunks}
        assert 2 not in page_nums
        assert 3 not in page_nums
        assert 1 in page_nums
        assert 4 in page_nums


class TestTextChunkerFactory:
    """工厂方法测试类"""
    
    def test_create_for_embedding(self):
        """测试创建embedding优化的分块器"""
        chunker = TextChunker.create_for_embedding(model_max_tokens=512)
        
        assert chunker is not None
        # chunk_size应该约为768（512 * 1.5）
        assert 700 <= chunker.chunk_size <= 800
        # overlap应该约为15%
        assert chunker.chunk_overlap < chunker.chunk_size * 0.2


class TestTextChunkerIntegration:
    """集成测试类"""
    
    @pytest.fixture
    def sample_pdf_path(self):
        """示例PDF路径"""
        return Path(__file__).parent.parent.parent / "trial data" / "招标.pdf"
    
    @pytest.mark.skipif(
        not (Path(__file__).parent.parent.parent / "trial data" / "招标.pdf").exists(),
        reason="测试PDF文件不存在"
    )
    def test_integration_with_pdf_parser(self, sample_pdf_path):
        """测试与PDF解析器的集成"""
        # 解析PDF
        parser = PdfParser()
        doc = parser.parse(sample_pdf_path)
        
        # 获取完整文本并分块
        chunker = TextChunker()
        chunks = chunker.split(doc.full_text)
        
        assert len(chunks) > 0
        
        # 验证分块内容
        total_chunk_chars = sum(c.char_count for c in chunks)
        # 由于重叠，总字符可能略多于原文
        assert total_chunk_chars >= len(doc.full_text) * 0.8
    
    @pytest.mark.skipif(
        not (Path(__file__).parent.parent.parent / "trial data" / "招标.pdf").exists(),
        reason="测试PDF文件不存在"
    )
    def test_integration_with_page_tracking(self, sample_pdf_path):
        """测试按页分块"""
        # 解析PDF
        parser = PdfParser()
        doc = parser.parse(sample_pdf_path)
        
        # 准备页面数据
        pages = [(p.page_num, p.text) for p in doc.pages]
        
        # 分块
        chunker = TextChunker()
        chunks = chunker.split_with_page_tracking(pages)
        
        assert len(chunks) > 0
        
        # 验证每个分块都有页码
        for chunk in chunks:
            assert "page_num" in chunk.metadata
            assert 1 <= chunk.metadata["page_num"] <= doc.page_count


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
