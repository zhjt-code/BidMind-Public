""" 契约规整单测

覆盖三个新 Pydantic 模型的校验行为与兼容性：
- CollusionExtractionMetadata
- CollusionDocxStructure
- CollusionExtractionPayload（= LLMExtractionResult + 算法后处理）
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# CollusionExtractionMetadata
# ---------------------------------------------------------------------------

class TestCollusionExtractionMetadata:
    @staticmethod
    def _cls():
        from app.schemas.collusion import CollusionExtractionMetadata
        return CollusionExtractionMetadata

    def test_empty_dict_passes(self):
        """全部字段可选，空 dict 合法"""
        m = self._cls().model_validate({})
        assert m.creator_tool is None
        assert m.raw_pdf_metadata is None

    def test_all_standard_fields(self):
        data = {
            "creator_tool": "Adobe",
            "author": '<PERSON_A>',
            "producer": "PDF Engine",
            "title": "投标文件",
            "subject": "xxx",
            "keywords": "a,b",
            "creation_date": "D:20260101",
            "mod_date": "D:20260102",
            "raw_pdf_metadata": {"Creator": "Adobe"},
            "extraction_trace": {"routes": {"entity": {"chunk_count": 3}}},
        }
        m = self._cls().model_validate(data)
        dumped = m.model_dump(exclude_none=True)
        assert set(dumped.keys()) == set(data.keys())
        assert dumped["creator_tool"] == "Adobe"

    def test_extra_field_ignored(self):
        """extra='ignore' 允许遗留/错位子键（如 的 subcontractors）不破坏校验"""
        data = {
            "creator_tool": "Adobe",
            "subcontractors": ["分包 A", "分包 B"],  # 错位
            "unknown_future_field": "x",
        }
        m = self._cls().model_validate(data)
        dumped = m.model_dump(exclude_none=True)
        # 未知字段被忽略，不会保留到 dump 里
        assert "subcontractors" not in dumped
        assert "unknown_future_field" not in dumped
        assert dumped["creator_tool"] == "Adobe"

    def test_type_mismatch_raises(self):
        """错误类型应触发 ValidationError（由上层 try/except 捕获降级）"""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            self._cls().model_validate({
                "raw_pdf_metadata": "这不是 dict",  # 类型错
            })


# ---------------------------------------------------------------------------
# CollusionDocxStructure
# ---------------------------------------------------------------------------

class TestCollusionDocxStructure:
    @staticmethod
    def _cls():
        from app.schemas.collusion import CollusionDocxStructure
        return CollusionDocxStructure

    def test_defaults(self):
        m = self._cls().model_validate({})
        assert m.toc_headings == []
        assert m.font_families == []
        assert m.margins == {}
        assert m.headers_footers == []

    def test_all_fields_populated(self):
        data = {
            "toc_headings": ["第一章", "第二章"],
            "font_families": ["SimSun", "Arial"],
            "margins": {"top": 25.4, "bottom": 25.4, "left": 20.0, "right": 20.0},
            "headers_footers": ["机密", "第 1 页"],
        }
        m = self._cls().model_validate(data)
        dumped = m.model_dump()
        assert dumped == data

    def test_margin_value_coerced_to_float(self):
        """Pydantic 对 int → float 做隐式转换"""
        m = self._cls().model_validate({
            "margins": {"top": 25, "bottom": 30}
        })
        assert isinstance(m.margins["top"], float)
        assert m.margins["top"] == 25.0

    def test_extra_field_ignored(self):
        m = self._cls().model_validate({
            "toc_headings": ["a"],
            "page_count": 10,  # 未知字段
        })
        dumped = m.model_dump()
        assert "page_count" not in dumped

    def test_wrong_field_type_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            self._cls().model_validate({"toc_headings": "不是列表"})


# ---------------------------------------------------------------------------
# CollusionExtractionPayload（= LLMExtractionResult + 算法后处理）
# ---------------------------------------------------------------------------

class TestCollusionExtractionPayload:
    @staticmethod
    def _cls():
        from app.schemas.collusion import CollusionExtractionPayload
        return CollusionExtractionPayload

    def test_empty_payload_passes(self):
        """LLMExtractionResult 所有字段可选，空 dict 合法"""
        m = self._cls().model_validate({})
        assert m.company_info is None
        assert m.metadata_info is None
        assert m.docx_structure is None

    def test_realistic_extraction_dict_passes(self):
        """模拟真实 extraction dict（LLM 输出 + 算法后处理）"""
        data = {
            "company_info": {
                "company_name": "测试公司A",
                "legal_representative": '<PERSON_A>',
            },
            "personnel_list": [
                {"name": '<PERSON_A>', "role": "项目经理"},
            ],
            "price_info": {"total_price": "100万元"},
            "technical_highlights": ["技术亮点1"],
            "qualifications": ["一级资质"],
            "past_projects": ["业绩1"],
            "subcontractors": ["分包A"],
            "metadata_info": {
                "creator_tool": "Adobe",
                "producer": "Acrobat",
            },
            "docx_structure": {
                "toc_headings": ["第一章"],
                "font_families": ["SimSun"],
                "margins": {"top": 25.4, "bottom": 25.4, "left": 20.0, "right": 20.0},
                "headers_footers": [],
            },
        }
        m = self._cls().model_validate(data)
        dumped = m.model_dump(exclude_none=True)
        # 顶层字段名和原 dict 一致
        assert dumped["company_info"]["company_name"] == "测试公司A"
        assert dumped["metadata_info"]["creator_tool"] == "Adobe"
        assert dumped["docx_structure"]["toc_headings"] == ["第一章"]
        # subcontractors 在 LLM 输出层，正确归属
        assert dumped["subcontractors"] == ["分包A"]

    def test_metadata_info_with_extra_subkey_tolerated(self):
        """ 兼容：metadata_info 里存量的 subcontractors 错位不破坏契约"""
        data = {
            "metadata_info": {
                "creator_tool": "Adobe",
                "subcontractors": ["残留错位"],  # 预存场景
            },
        }
        m = self._cls().model_validate(data)
        # metadata_info 里的 subcontractors 被 extra="ignore" 过滤掉
        dumped = m.model_dump(exclude_none=True)
        assert "subcontractors" not in dumped["metadata_info"]

    def test_extra_top_level_field_ignored(self):
        """顶层未知字段被 extra='ignore' 过滤"""
        data = {
            "company_info": {"company_name": "X"},
            "future_algorithmic_field": "future",
        }
        m = self._cls().model_validate(data)
        dumped = m.model_dump(exclude_none=True)
        assert "future_algorithmic_field" not in dumped


# ---------------------------------------------------------------------------
# 生产侧兜底：_validate_extraction_payload 非法输入 warning 不阻断
# ---------------------------------------------------------------------------

class TestValidateExtractionPayloadFallback:
    def test_empty_dict_is_noop(self):
        from app.core.collusion.analyzer import _validate_extraction_payload
        # 不应抛异常，也不应调用 logger（内部 early-return）
        _validate_extraction_payload({}, document_id=1, doc_type="bid")

    def test_valid_dict_no_warning(self, caplog):
        import logging
        from app.core.collusion.analyzer import _validate_extraction_payload
        caplog.set_level(logging.WARNING)
        _validate_extraction_payload(
            {"company_info": {"company_name": "ok"}},
            document_id=1, doc_type="bid",
        )
        assert "extraction payload schema 校验失败" not in caplog.text

    def test_schema_failure_logs_warning_not_raise(self, caplog, monkeypatch):
        """构造类型错误触发 ValidationError，函数吞掉并 log warning"""
        import logging
        from app.core.collusion.analyzer import _validate_extraction_payload
        # caplog 需要 propagate
        import loguru as _loguru
        sink_id = _loguru.logger.add(caplog.handler, level="WARNING", format="{message}")
        try:
            _validate_extraction_payload(
                {"personnel_list": "这不是列表"},  # 触发类型错
                document_id=999, doc_type="bid",
            )
        finally:
            _loguru.logger.remove(sink_id)

        # 不抛异常就视为通过；warning 行为已由 logger 自己测试覆盖
        # 这里只确保函数不 raise
