from tests.synthetic_data import synthetic_text
"""OCR 后处理器单元测试

覆盖: 证照字段抽取、文档类型推断、输出长度控制、空输入处理。
"""

import os
import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.parser.ocr_engine import OcrResult
from app.core.parser.ocr_postprocessor import (
    infer_document_type,
    is_document_type_image,
    postprocess_ocr,
)


class TestDocumentTypeInference:
    def test_business_license(self):
        text = synthetic_text('营业执照\n', '统一社会信用代码：', '91110000X', 'XXXXXXXXX', '\n注册资本：5000万元')
        assert infer_document_type(text) == "营业执照"

    def test_qualification_cert(self):
        text = "建筑业企业资质证书\n资质等级：施工总承包一级"
        assert infer_document_type(text) == "资质证书"

    def test_id_card(self):
        text = synthetic_text('姓名 <PERSON_A>\n性别 男\n民族 汉\n公民身份号码 ', '110101', '199001', '011234')
        assert infer_document_type(text) == "身份证"

    def test_safety_permit(self):
        text = "安全生产许可证\n编号 (京)JZ安许证字[2024]000123"
        assert infer_document_type(text) == "安全生产许可证"

    def test_contract(self):
        text = "建设工程施工合同\n甲方：某建设公司\n乙方：某施工公司\n合同金额：1200万元"
        assert infer_document_type(text) == "合同"

    def test_unknown_type(self):
        text = "一些普通的文字内容没有任何证照特征"
        assert infer_document_type(text) is None

    def test_empty_text(self):
        assert infer_document_type("") is None
        assert infer_document_type(None) is None


class TestIsDocumentTypeImage:
    def test_is_cert(self):
        assert is_document_type_image("营业执照 统一社会信用代码") is True

    def test_not_cert(self):
        assert is_document_type_image("工地照片 施工现场") is False


class TestPostprocessOcr:
    def test_business_license(self):
        ocr = OcrResult(
            raw_text=(
                "营业执照\n"
                + synthetic_text('统一社会信用代码：', '91110000X', 'XXXXXXXXX', '\n')
                + "名    称：<ORG_9>\n"
                "法定代表人：<PERSON_4>\n"
                "注册资本：5000万元\n"
                "经营范围：建筑工程施工"
            ),
            avg_confidence=0.92,
            is_confident=True,
        )
        result = postprocess_ocr(ocr)
        assert "营业执照" in result
        assert "<ORG_9>" in result
        assert synthetic_text('911100', '00XXXX', 'XXXXXX') in result
        assert "<PERSON_4>" in result

    def test_qualification_cert_with_fields(self):
        ocr = OcrResult(
            raw_text=(
                "建筑业企业资质证书\n"
                "证书编号：D123456789\n"
                "企业名称：<ORG_18>\n"
                "资质等级：施工总承包特级\n"
                "有效期至：2027年12月31日"
            ),
            avg_confidence=0.88,
            is_confident=True,
        )
        result = postprocess_ocr(ocr)
        assert "资质证书" in result
        assert "D123456789" in result
        assert "施工总承包特级" in result
        assert "2027年12月31日" in result

    def test_empty_input(self):
        ocr = OcrResult(raw_text="", avg_confidence=0.0, is_confident=False)
        assert postprocess_ocr(ocr) == ""

    def test_whitespace_only(self):
        ocr = OcrResult(raw_text="   \n  \n  ", avg_confidence=0.1, is_confident=False)
        assert postprocess_ocr(ocr) == ""

    def test_unknown_doc_type(self):
        ocr = OcrResult(
            raw_text="一些普通文字内容但字数足够长度超过阈值可以触发处理",
            avg_confidence=0.8,
            is_confident=True,
        )
        result = postprocess_ocr(ocr)
        assert "文档" in result

    def test_output_length_limit(self):
        long_text = "某某公司资质证书\n" + "这是一行很长的文字内容。\n" * 100
        ocr = OcrResult(raw_text=long_text, avg_confidence=0.9, is_confident=True)
        result = postprocess_ocr(ocr)
        assert len(result) <= 500

    def test_id_card_fields(self):
        ocr = OcrResult(
            raw_text=(
                "中华人民共和国居民身份证\n"
                + '姓  名：<PERSON_A>\n'
                + "性别：男\n"
                "民族：汉\n"
                + synthetic_text('公民身份号码：', '110101', '199001', '011234')
            ),
            avg_confidence=0.85,
            is_confident=True,
        )
        result = postprocess_ocr(ocr)
        assert "身份证" in result
        assert '<PERSON_A>' in result
