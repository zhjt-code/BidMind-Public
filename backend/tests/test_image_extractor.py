"""图片提取模块测试"""

import sys
from pathlib import Path

import pytest

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.parser.image_extractor import (
    ExtractedImage,
    ImageExtractor,
    extract_context_for_images,
    generate_image_annotations,
)
from app.core.parser.pdf_parser import ParsedDocument, ParsedPage, PdfParser


class TestExtractedImage:
    """ExtractedImage 测试类"""

    @pytest.fixture
    def large_image(self):
        """创建大幅扫描件示例"""
        return ExtractedImage(
            image_index=0,
            page_num=5,
            width=595,
            height=842,
            image_format="jpeg",
            xref=10,
            bbox=(50.0, 100.0, 545.0, 780.0),
            file_path="/uploads/1/images/p5_img1.jpeg",
            file_size=102400,
        )

    @pytest.fixture
    def medium_image(self):
        """创建中等图片示例"""
        return ExtractedImage(
            image_index=1,
            page_num=3,
            width=300,
            height=250,
            image_format="png",
            xref=15,
            bbox=(100.0, 500.0, 400.0, 750.0),
            file_path="/uploads/1/images/p3_img2.png",
            file_size=51200,
        )

    @pytest.fixture
    def small_icon(self):
        """创建小图标示例（应被过滤）"""
        return ExtractedImage(
            image_index=0,
            page_num=1,
            width=50,
            height=50,
            image_format="png",
            xref=5,
            bbox=(10.0, 10.0, 60.0, 60.0),
        )

    def test_size_category_large(self, large_image):
        """测试大幅扫描件分类"""
        assert large_image.size_category == "大幅扫描件"

    def test_size_category_medium(self, medium_image):
        """测试中等图片分类"""
        assert medium_image.size_category == "中等图片"

    def test_size_category_small(self, small_icon):
        """测试小图标分类"""
        assert small_icon.size_category == "小图标"

    def test_position_description_upper(self):
        """测试页面上部位置判断"""
        img = ExtractedImage(
            image_index=0, page_num=1, width=200, height=200,
            image_format="png", xref=1,
            bbox=(100.0, 50.0, 300.0, 200.0),
        )
        assert img.position_description == "页面上部"

    def test_position_description_middle(self):
        """测试页面中部位置判断"""
        img = ExtractedImage(
            image_index=0, page_num=1, width=200, height=200,
            image_format="png", xref=1,
            bbox=(100.0, 300.0, 300.0, 500.0),
        )
        assert img.position_description == "页面中部"

    def test_position_description_lower(self):
        """测试页面下部位置判断"""
        img = ExtractedImage(
            image_index=0, page_num=1, width=200, height=200,
            image_format="png", xref=1,
            bbox=(100.0, 600.0, 300.0, 800.0),
        )
        assert img.position_description == "页面下部"

    def test_position_description_no_bbox(self):
        """测试无bbox时的位置描述"""
        img = ExtractedImage(
            image_index=0, page_num=1, width=200, height=200,
            image_format="png", xref=1,
        )
        assert img.position_description == "位置未知"

    def test_doc_type_hint_large(self, large_image):
        """测试大幅扫描件的文档类型提示"""
        assert "证书" in large_image.doc_type_hint
        assert "执照" in large_image.doc_type_hint

    def test_doc_type_hint_medium(self, medium_image):
        """测试中等图片的文档类型提示"""
        assert "照片" in medium_image.doc_type_hint or "图表" in medium_image.doc_type_hint

    def test_to_text_annotation_without_context(self, large_image):
        """测试无上下文时的文本标注（降级: 仅展示类型提示）"""
        annotation = large_image.to_text_annotation()
        assert "[图片1:" in annotation
        assert "大幅扫描件" in annotation
        assert "595x842" in annotation
        assert "页面中部" in annotation
        assert "上下文关联" not in annotation
        assert "紧邻文字" not in annotation

    def test_to_text_annotation_with_shared_context(self, large_image):
        """测试有共享上下文时的文本标注（降级方案）"""
        annotation = large_image.to_text_annotation(context="营业执照复印件")
        assert "[图片1:" in annotation
        assert "大幅扫描件" in annotation
        assert '上下文关联: "营业执照复印件"' in annotation
        assert "紧邻文字" not in annotation

    def test_to_text_annotation_with_spatial_context(self, large_image):
        """核心测试: 有空间上下文时优先使用紧邻文字"""
        large_image.spatial_context = "(4) X射线衍射仪（XRD）"
        annotation = large_image.to_text_annotation(context="共享上下文不应出现")
        assert "[图片1:" in annotation
        assert "大幅扫描件" in annotation
        assert '紧邻文字: "(4) X射线衍射仪（XRD）"' in annotation
        # 空间上下文可用时，不应出现共享上下文和通用类型提示
        assert "上下文关联" not in annotation
        assert "可能为证书" not in annotation

    def test_to_text_annotation_spatial_overrides_shared(self, medium_image):
        """测试空间上下文优先级高于共享上下文"""
        medium_image.spatial_context = "扫描电子显微镜(SEM)"
        annotation = medium_image.to_text_annotation(context="其他上下文")
        assert '紧邻文字: "扫描电子显微镜(SEM)"' in annotation
        assert "其他上下文" not in annotation

    def test_to_text_annotation_doc_hint(self, large_image):
        """测试无空间上下文时文本标注中包含文档类型提示"""
        annotation = large_image.to_text_annotation()
        assert "证书" in annotation or "执照" in annotation

    def test_search_keywords_large(self, large_image):
        """测试大幅扫描件的搜索关键词"""
        keywords = large_image.search_keywords
        assert "复印件" in keywords
        assert "扫描件" in keywords
        assert "附件材料" in keywords

    def test_search_keywords_medium(self, medium_image):
        """测试中等图片的搜索关键词"""
        keywords = medium_image.search_keywords
        assert "附件图片" in keywords

    def test_search_keywords_small(self, small_icon):
        """测试小图标无搜索关键词"""
        assert small_icon.search_keywords == ""

    def test_to_text_annotation_includes_keywords(self, large_image):
        """测试文本标注包含搜索关键词"""
        annotation = large_image.to_text_annotation()
        assert "复印件" in annotation
        assert "扫描件" in annotation


class TestImageExtractor:
    """ImageExtractor 测试类"""

    @pytest.fixture
    def extractor(self):
        """创建图片提取器实例"""
        return ImageExtractor()

    def test_extractor_init_defaults(self, extractor):
        """测试默认初始化"""
        assert extractor.min_width == 100
        assert extractor.min_height == 100
        assert extractor.min_area == 15000

    def test_extractor_init_custom(self):
        """测试自定义参数初始化"""
        extractor = ImageExtractor(
            min_width=200, min_height=200, min_area=50000
        )
        assert extractor.min_width == 200
        assert extractor.min_height == 200
        assert extractor.min_area == 50000

    def test_passes_size_filter_large(self, extractor):
        """测试大图通过过滤"""
        assert extractor._passes_size_filter(595, 842) is True

    def test_passes_size_filter_small(self, extractor):
        """测试小图被过滤"""
        assert extractor._passes_size_filter(50, 50) is False

    def test_passes_size_filter_narrow(self, extractor):
        """测试窄图被过滤（宽度不足）"""
        assert extractor._passes_size_filter(50, 500) is False

    def test_passes_size_filter_low_area(self, extractor):
        """测试面积不足被过滤"""
        assert extractor._passes_size_filter(100, 100) is False

    def test_passes_size_filter_borderline(self, extractor):
        """测试边界值通过过滤"""
        assert extractor._passes_size_filter(150, 150) is True


class TestFindNearestText:
    """_find_nearest_text 空间映射测试"""

    def test_text_above_image(self):
        """测试图片上方有文本时的映射"""
        text_blocks = [
            (50.0, 80.0, 400.0, 100.0, "(4) X射线衍射仪（XRD）"),
        ]
        image_bbox = (50.0, 110.0, 400.0, 500.0)
        result = ImageExtractor._find_nearest_text(text_blocks, image_bbox)
        assert "X射线衍射仪" in result

    def test_closest_text_wins(self):
        """测试最近的文本块被优先选择"""
        text_blocks = [
            (50.0, 10.0, 400.0, 30.0, "八、主要设备清单"),
            (50.0, 80.0, 400.0, 100.0, "(4) X射线衍射仪（XRD）"),
        ]
        image_bbox = (50.0, 110.0, 400.0, 500.0)
        result = ImageExtractor._find_nearest_text(text_blocks, image_bbox)
        assert "X射线衍射仪" in result
        assert "设备清单" not in result

    def test_multiple_images_different_text(self):
        """测试同一页面不同图片映射到不同文本"""
        text_blocks = [
            (50.0, 80.0, 400.0, 100.0, "(1) 电感耦合等离子体发射光谱仪(ICP)"),
            (50.0, 380.0, 400.0, 400.0, "(2) 扫描电子显微镜(SEM)"),
        ]
        img1_bbox = (50.0, 110.0, 400.0, 370.0)
        img2_bbox = (50.0, 410.0, 400.0, 700.0)

        result1 = ImageExtractor._find_nearest_text(text_blocks, img1_bbox)
        result2 = ImageExtractor._find_nearest_text(text_blocks, img2_bbox)

        assert "ICP" in result1
        assert "SEM" in result2

    def test_no_text_blocks(self):
        """测试无文本块时返回空"""
        result = ImageExtractor._find_nearest_text([], (50.0, 110.0, 400.0, 500.0))
        assert result == ""

    def test_no_image_bbox(self):
        """测试无图片bbox时返回空"""
        text_blocks = [
            (50.0, 80.0, 400.0, 100.0, "测试文本"),
        ]
        result = ImageExtractor._find_nearest_text(text_blocks, None)
        assert result == ""

    def test_text_too_far_above(self):
        """测试文本距离图片太远时不匹配"""
        text_blocks = [
            (50.0, 10.0, 400.0, 30.0, "远处的文本"),
        ]
        # 图片在y=700处，距离文本670点，远超默认200点阈值
        image_bbox = (50.0, 700.0, 400.0, 800.0)
        result = ImageExtractor._find_nearest_text(text_blocks, image_bbox)
        assert result == ""

    def test_fallback_to_below_text(self):
        """测试图片上方无文本时退回下方文本"""
        text_blocks = [
            (50.0, 600.0, 400.0, 620.0, "图片下方说明"),
        ]
        # 图片在y=400-590，上方无文本
        image_bbox = (50.0, 400.0, 400.0, 590.0)
        result = ImageExtractor._find_nearest_text(text_blocks, image_bbox)
        assert "下方说明" in result

    def test_short_text_merges_with_upper(self):
        """测试最近文本过短（<3字符）时合并上一个文本块"""
        text_blocks = [
            (50.0, 50.0, 400.0, 70.0, "(4) X射线衍射仪（XRD）"),
            (50.0, 80.0, 400.0, 95.0, "图"),  # 仅1字符
        ]
        image_bbox = (50.0, 100.0, 400.0, 500.0)
        result = ImageExtractor._find_nearest_text(text_blocks, image_bbox)
        # 应合并为 "X射线衍射仪（XRD） 图" 或类似
        assert "X射线衍射仪" in result

    def test_text_truncated_at_300(self):
        """测试长文本被截断到300字符"""
        long_text = "这是一段非常长的文本" * 50  # 500字符
        text_blocks = [
            (50.0, 80.0, 400.0, 100.0, long_text),
        ]
        image_bbox = (50.0, 110.0, 400.0, 500.0)
        result = ImageExtractor._find_nearest_text(text_blocks, image_bbox)
        assert len(result) <= 300


class TestExtractContextForImages:
    """extract_context_for_images 测试"""

    def test_context_from_current_page(self):
        """测试从当前页提取上下文"""
        current = "一、企业基本情况\n营业执照复印件\n（附后）"
        result = extract_context_for_images(current)
        assert "营业执照" in result

    def test_context_from_prev_page_when_current_empty(self):
        """测试当前页无文字时从前一页提取"""
        current = ""
        prev = "以下为资质证书扫描件"
        result = extract_context_for_images(current, prev)
        assert "资质证书" in result

    def test_context_short_current_supplements_with_prev(self):
        """核心测试: 当前页文字很短时保留当前页并用前一页补充"""
        current = "六、营业执照"
        prev = "五、法定代表人授权委托书\n委托代理人信息如下"
        result = extract_context_for_images(current, prev)
        # 关键: 当前页的"营业执照"必须保留在结果中
        assert "营业执照" in result
        # 同时前一页的内容也应被补充
        assert "委托" in result or "授权" in result

    def test_context_short_current_without_prev(self):
        """测试当前页文字很短但无前一页时,仍保留当前页文字"""
        current = "六、营业执照"
        result = extract_context_for_images(current, "")
        assert "营业执照" in result

    def test_context_short_current_title_always_included(self):
        """测试短文本页标题不被末尾截取逻辑遗漏"""
        current = "资质证书"
        prev = "很长的前一页第一行\n很长的前一页第二行\n很长的前一页第三行\n很长的前一页末尾"
        result = extract_context_for_images(current, prev)
        # "资质证书"是当前页标题,必须出现在结果中
        assert "资质证书" in result

    def test_context_empty_both_pages(self):
        """测试两页都为空时返回空字符串"""
        result = extract_context_for_images("", "")
        assert result == ""

    def test_context_default_max_length_400(self):
        """测试默认max_length=400，覆盖多设备页面"""
        # 模拟含多个设备名称的页面（每个约30字符）
        lines = [f"({i}) 设备名称{i}号比较长的完整名字" for i in range(1, 15)]
        text = "\n".join(lines)
        result = extract_context_for_images(text)
        # 默认400字符应能容纳更多设备名称
        assert len(result) > 150  # 比旧默认值150大

    def test_context_max_length_truncation(self):
        """测试上下文长度限制"""
        long_text = "这是一段很长的文本\n" * 50
        result = extract_context_for_images(long_text, max_length=50)
        assert len(result) <= 60  # 允许少量超出(行边界)

    def test_context_prioritizes_last_lines(self):
        """测试优先提取末尾行（图片通常在文字后面）"""
        text = "第一行无关信息\n第二行无关信息\n营业执照复印件（见下页）"
        result = extract_context_for_images(text)
        assert "营业执照" in result

    def test_context_normal_page_no_supplement(self):
        """测试正常页面(文字>=20字符)不会补充前一页"""
        current = "这是一段足够长的当前页文本,包含了丰富的内容描述"
        prev = "前一页的内容不应该出现"
        result = extract_context_for_images(current, prev)
        assert "前一页" not in result


class TestGenerateImageAnnotations:
    """generate_image_annotations 测试"""

    def test_empty_list(self):
        """测试空图片列表"""
        result = generate_image_annotations([])
        assert result == ""

    def test_single_image_without_context(self):
        """测试单张图片无上下文标注"""
        images = [
            ExtractedImage(
                image_index=0, page_num=5, width=595, height=842,
                image_format="jpeg", xref=10,
                bbox=(50.0, 100.0, 545.0, 780.0),
                file_path="/uploads/1/images/p5_img1.jpeg",
            )
        ]
        result = generate_image_annotations(images)
        assert "[本页附件图片: 共1张" in result
        assert "含扫描件附件材料" in result  # 大幅扫描件头部语义增强
        assert "[图片1:" in result
        assert "大幅扫描件" in result
        assert "复印件" in result  # 搜索关键词
        assert "上下文关联" not in result

    def test_single_image_with_context(self):
        """测试单张图片有上下文标注"""
        images = [
            ExtractedImage(
                image_index=0, page_num=5, width=595, height=842,
                image_format="jpeg", xref=10,
                bbox=(50.0, 100.0, 545.0, 780.0),
            )
        ]
        result = generate_image_annotations(images, context="营业执照复印件")
        assert "[本页附件图片: 共1张" in result
        assert "上下文关联" in result
        assert "营业执照" in result

    def test_multiple_images_shared_context(self):
        """测试多张图片共享上下文标注"""
        images = [
            ExtractedImage(
                image_index=0, page_num=3, width=595, height=842,
                image_format="jpeg", xref=10,
                bbox=(50.0, 50.0, 545.0, 400.0),
                file_path="/uploads/1/images/p3_img1.jpeg",
            ),
            ExtractedImage(
                image_index=1, page_num=3, width=400, height=600,
                image_format="png", xref=15,
                bbox=(50.0, 450.0, 450.0, 800.0),
                file_path="/uploads/1/images/p3_img2.png",
            ),
        ]
        result = generate_image_annotations(images, context="资质证书")
        assert "[本页附件图片: 共2张" in result
        assert "[图片1:" in result
        assert "[图片2:" in result
        assert "资质证书" in result

    def test_images_with_spatial_context(self):
        """核心测试: 有空间上下文时头部展示标签摘要"""
        img1 = ExtractedImage(
            image_index=0, page_num=5, width=595, height=842,
            image_format="jpeg", xref=10,
            bbox=(50.0, 110.0, 545.0, 400.0),
        )
        img1.spatial_context = "(1) 电感耦合等离子体发射光谱仪(ICP)"

        img2 = ExtractedImage(
            image_index=1, page_num=5, width=595, height=842,
            image_format="jpeg", xref=15,
            bbox=(50.0, 450.0, 545.0, 780.0),
        )
        img2.spatial_context = "(2) 扫描电子显微镜(SEM)"

        result = generate_image_annotations([img1, img2])
        assert "[本页附件图片: 共2张" in result
        assert "涉及:" in result
        assert "ICP" in result
        assert "SEM" in result
        # 逐图标注使用紧邻文字
        assert "紧邻文字" in result
        # 不应出现"上下文关联"
        assert "上下文关联" not in result

    def test_images_mixed_spatial_and_shared(self):
        """测试部分图片有空间上下文的混合场景"""
        img1 = ExtractedImage(
            image_index=0, page_num=5, width=595, height=842,
            image_format="jpeg", xref=10,
            bbox=(50.0, 110.0, 545.0, 400.0),
        )
        img1.spatial_context = "(1) 电感耦合等离子体发射光谱仪(ICP)"

        img2 = ExtractedImage(
            image_index=1, page_num=5, width=300, height=250,
            image_format="jpeg", xref=15,
            bbox=(50.0, 450.0, 350.0, 700.0),
            # 无spatial_context
        )

        result = generate_image_annotations([img1, img2], context="设备照片")
        # 头部应使用空间标签摘要（因为至少有一个图片有spatial）
        assert "涉及:" in result
        assert "ICP" in result
        # img1使用紧邻文字
        assert "紧邻文字" in result
        # img2降级使用共享上下文
        assert "上下文关联" in result


class TestPdfParserWithImages:
    """PdfParser 图片功能集成测试"""

    def test_parser_init_with_images(self):
        """测试解析器含图片提取的初始化"""
        parser = PdfParser()
        assert parser.extract_images is True
        assert parser.image_extractor is not None

    def test_parser_init_no_images(self):
        """测试不提取图片的初始化"""
        parser = PdfParser(extract_images=False)
        assert parser.extract_images is False
        assert parser.image_extractor is None

    def test_parsed_page_has_images_field(self):
        """测试ParsedPage包含images字段"""
        page = ParsedPage(
            page_num=1,
            text="测试内容",
            tables=[],
            images=[],
            width=595.0,
            height=842.0,
        )
        assert page.has_images is False
        assert page.images == []

    def test_parsed_page_with_images(self):
        """测试含图片的ParsedPage"""
        img = ExtractedImage(
            image_index=0, page_num=1, width=595, height=842,
            image_format="jpeg", xref=10,
        )
        page = ParsedPage(
            page_num=1,
            text="资质证明材料",
            tables=[],
            images=[img],
            width=595.0,
            height=842.0,
        )
        assert page.has_images is True
        assert len(page.images) == 1

    def test_parsed_document_all_images(self):
        """测试ParsedDocument的all_images属性"""
        img1 = ExtractedImage(
            image_index=0, page_num=1, width=595, height=842,
            image_format="jpeg", xref=10,
        )
        img2 = ExtractedImage(
            image_index=0, page_num=3, width=400, height=600,
            image_format="png", xref=15,
        )
        doc = ParsedDocument(
            file_path="/test.pdf",
            filename="test.pdf",
            page_count=3,
            pages=[
                ParsedPage(page_num=1, text="page1", images=[img1]),
                ParsedPage(page_num=2, text="page2", images=[]),
                ParsedPage(page_num=3, text="page3", images=[img2]),
            ],
        )
        assert len(doc.all_images) == 2

    @pytest.mark.skipif(
        not (Path(__file__).parent.parent.parent / "trial data" / "投标.pdf").exists(),
        reason="测试PDF文件不存在"
    )
    def test_parse_real_pdf_with_images(self):
        """测试解析真实PDF（含图片提取）"""
        pdf_path = Path(__file__).parent.parent.parent / "trial data" / "投标.pdf"
        parser = PdfParser()
        doc = parser.parse(pdf_path)

        assert isinstance(doc, ParsedDocument)
        assert doc.page_count > 0

        # 统计图片
        image_count = len(doc.all_images)
        pages_with_images = sum(1 for p in doc.pages if p.has_images)

        print(f"\n解析结果: {doc.page_count} 页, {image_count} 张图片, "
              f"{pages_with_images} 个页面含图片")

        for p in doc.pages:
            if p.has_images:
                print(f"  第 {p.page_num} 页: {len(p.images)} 张图片")
                for img in p.images:
                    print(f"    - {img.to_text_annotation()}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
