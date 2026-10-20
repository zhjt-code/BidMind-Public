""" OCR 页型多标化：防御测试。

问题形态: 页型标签 winner-take-all, 一页含多种材料或字段串用时,
真实材料名被另一类型垄断(发票页因含'纳税人识别号'被单标成税务登记证),
检索词面随之偏移, 库内明词对特定条款不可见。

机制: infer_document_types 返回全部命中类型(按命中数降序), 首行多标并列。
本案例(发票)与同形态第二例(社保含身份证号)两实体夹具。
"""

from __future__ import annotations
from tests.synthetic_data import synthetic_text

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.parser.ocr_engine import OcrResult
from app.core.parser.ocr_postprocessor import (
    infer_document_type,
    infer_document_types,
    postprocess_ocr,
)


class TestMultiLabelInference:
    """多标推断: 真实材料名不被强势的另一类型垄断。"""

    def test_invoice_with_tax_field_not_monopolized(self):
        """发票页含'纳税人识别号', 但发票专有标识更多 -> 多标含'发票'且居首。"""
        text = (
            "北京增值税专用发票 发票号码 No 02482091 价税合计 60000.00 "
            "纳税人识别号 1100234130 结算发票"
        )
        types = infer_document_types(text)
        assert "发票" in types, f"发票未被识别: {types}"
        assert types[0] == "发票", f"发票命中最多却未居首: {types}"

    def test_social_insurance_with_id_number_second_entity(self):
        """同形态第二例: 社保缴费证明附身份证号, 不被'身份证'垄断。"""
        text = (
            '社会保险缴费证明 参保人 <PERSON_A> 缴费基数 8000 养老保险 医疗保险 '
            + synthetic_text('公民身份号码 ', '110101', '199001', '011234')
        )
        types = infer_document_types(text)
        assert "社会保险" in types, f"社保未被识别: {types}"

    def test_single_type_still_single_label(self):
        """回归: 纯营业执照仍单标, 不被多标机制误伤。"""
        text = synthetic_text('营业执照 ', '统一社会信用代码 ', '91110000X', 'XXXXXXXXX', ' 注册资本 5000万元 经营范围 建筑')
        types = infer_document_types(text)
        assert types == ["营业执照"], types

    def test_unknown_returns_empty(self):
        assert infer_document_types("普通段落文字没有任何证照特征") == []
        assert infer_document_types("") == []
        assert infer_document_types(None) == []


class TestBackwardCompatSingle:
    """infer_document_type 单标向后兼容: 取多标首位。"""

    def test_single_label_preserved(self):
        assert infer_document_type("营业执照 统一社会信用代码 注册资本") == "营业执照"
        assert infer_document_type("") is None
        assert infer_document_type(None) is None


class TestPostprocessMultiLabelFirstLine:
    """postprocess_ocr 首行多标并列, 使每种材料名进入 chunk 文本。"""

    def test_invoice_first_line_contains_invoice(self):
        ocr = OcrResult(
            raw_text=(
                "北京增值税专用发票 发票号码 No 02482091 价税合计 60000.00 "
                "纳税人识别号 1100234130 结算发票"
            ),
            avg_confidence=0.9,
            is_confident=True,
        )
        out = postprocess_ocr(ocr)
        assert "发票" in out
        assert out.startswith("这是一份包含"), out.split("\n")[0]

    def test_single_type_first_line_unchanged(self):
        """回归: 单类型首行保持'这是一份XX的扫描件'。"""
        ocr = OcrResult(
            raw_text="营业执照 统一社会信用代码 91110000 注册资本 5000万 经营范围 建筑施工",
            avg_confidence=0.9,
            is_confident=True,
        )
        out = postprocess_ocr(ocr)
        assert out.startswith("这是一份营业执照的扫描件")
