""" 修复验证单测

验证 analyzer 的 `_extraction_to_dict` 读取路径：
 - 新数据：subcontractors_info 独立列 → 正确读取
 - 遗留数据：metadata_info.subcontractors 错位子键 → fallback 兼容读取
 - 混合场景：两者同时存在时，独立列优先
 - metadata_info 返回的 dict 中**不再**含 subcontractors 子键

`_save_extraction` 走 DB，纯单测不便直接验证；写入路径通过结构化 mock 模拟
CollusionExtraction 对象的字段赋值检查，确保契约一致。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# M4 读取路径：_extraction_to_dict
# ---------------------------------------------------------------------------

class _FakeCE:
    """轻量 mock CollusionExtraction，只需要 _extraction_to_dict 读取的字段"""
    def __init__(
        self,
        *,
        metadata_info=None,
        subcontractors_info=None,
        company_info=None,
        personnel_info=None,
        price_info=None,
        technical_features=None,
        qualification_info=None,
        guarantee_info=None,
    ):
        self.metadata_info = metadata_info
        self.subcontractors_info = subcontractors_info
        self.company_info = company_info
        self.personnel_info = personnel_info
        self.price_info = price_info
        self.technical_features = technical_features
        self.qualification_info = qualification_info
        self.guarantee_info = guarantee_info


class TestExtractionToDictNewColumnPath:
    """M4 验证：新列 subcontractors_info 作为首选数据源"""

    @staticmethod
    def _call(ext):
        # 不实例化 CollusionAnalyzer（避免 DB/Session 依赖），直接拿 unbound method
        from app.core.collusion.analyzer import CollusionAnalyzer
        return CollusionAnalyzer._extraction_to_dict(None, ext)  # type: ignore[arg-type]

    def test_new_column_is_primary_source(self):
        ext = _FakeCE(
            metadata_info={"creator_tool": "Adobe"},
            subcontractors_info=["新列 A", "新列 B"],
        )
        out = self._call(ext)
        assert out["subcontractors"] == ["新列 A", "新列 B"]
        assert out["metadata_info"] == {"creator_tool": "Adobe"}

    def test_legacy_metadata_fallback_when_new_column_null(self):
        """历史数据：migration 未执行时，新列为 None，应 fallback 到 metadata_info 错位子键"""
        ext = _FakeCE(
            metadata_info={
                "creator_tool": "Adobe",
                "subcontractors": ["遗留 A"],
            },
            subcontractors_info=None,
        )
        out = self._call(ext)
        assert out["subcontractors"] == ["遗留 A"]
        # 关键：返回的 metadata_info 不再含 subcontractors 子键
        assert "subcontractors" not in out["metadata_info"]
        assert out["metadata_info"] == {"creator_tool": "Adobe"}

    def test_new_column_wins_over_legacy(self):
        """新列与遗留字段同时存在时，新列优先（migration 成功后不应发生该情况，仅防御）"""
        ext = _FakeCE(
            metadata_info={
                "creator_tool": "Adobe",
                "subcontractors": ["遗留 残余"],
            },
            subcontractors_info=["新列 权威"],
        )
        out = self._call(ext)
        assert out["subcontractors"] == ["新列 权威"]
        assert "subcontractors" not in out["metadata_info"]

    def test_both_empty_returns_empty_list(self):
        ext = _FakeCE(metadata_info={"creator_tool": "Adobe"}, subcontractors_info=None)
        out = self._call(ext)
        assert out["subcontractors"] == []

    def test_metadata_info_none_passthrough(self):
        ext = _FakeCE(metadata_info=None, subcontractors_info=["A"])
        out = self._call(ext)
        assert out["metadata_info"] is None
        assert out["subcontractors"] == ["A"]

    def test_metadata_info_non_dict_tolerated(self):
        """metadata_info 若被异常落为非 dict（如 list/str），不应抛异常"""
        ext = _FakeCE(metadata_info="invalid-string", subcontractors_info=["A"])
        out = self._call(ext)
        assert out["metadata_info"] == "invalid-string"
        assert out["subcontractors"] == ["A"]

    def test_new_empty_list_is_authoritative_no_fallback(self):
        """新列为空列表 [] 时视为权威"已写入但无数据"，不应 fallback 到遗留字段。
        这避免"新写入覆盖为空被老错位字段还原"的反直觉行为。"""
        ext = _FakeCE(
            metadata_info={"subcontractors": ["遗留 A"]},
            subcontractors_info=[],
        )
        out = self._call(ext)
        # 空列表是权威状态；返回空列表，不走遗留字段
        assert out["subcontractors"] == []
        # metadata_info 里的 subcontractors 仍被剥离（契约一致性）
        assert "subcontractors" not in out["metadata_info"]


# ---------------------------------------------------------------------------
# M3 写入路径：_save_extraction field_values 构造（逻辑隔离测试）
# ---------------------------------------------------------------------------
# 完整 DB 写入走 async_session_factory 依赖，单测中不便启动。
# 这里通过源码静态检查确保"新列 subcontractors_info 出现在 field_values"，
# 并且"metadata_info 不再被污染"这条契约被保留。

class TestSaveExtractionContract:
    def test_source_code_writes_to_new_column(self):
        """静态检查：_save_extraction 里 field_values 应包含 subcontractors_info 键"""
        import inspect

        from app.core.collusion.analyzer import CollusionAnalyzer
        src = inspect.getsource(CollusionAnalyzer._save_extraction)
        assert '"subcontractors_info"' in src or "'subcontractors_info'" in src

    def test_source_code_cleans_metadata_subcontractors(self):
        """静态检查：_save_extraction 会从 metadata_info 剔除 subcontractors 错位键"""
        import inspect

        from app.core.collusion.analyzer import CollusionAnalyzer
        src = inspect.getsource(CollusionAnalyzer._save_extraction)
        # 新代码应调用 metadata_info.pop("subcontractors", None)
        assert 'metadata_info.pop("subcontractors"' in src

    def test_source_code_no_longer_mutates_metadata_with_subs(self):
        """静态检查：_save_extraction 不再把 extraction.get("subcontractors") 塞进 metadata_info"""
        import inspect

        from app.core.collusion.analyzer import CollusionAnalyzer
        src = inspect.getsource(CollusionAnalyzer._save_extraction)
        # 原错位写法 metadata_info["subcontractors"] = extraction.get("subcontractors") 不应再出现
        assert 'metadata_info["subcontractors"] = extraction.get' not in src


# ---------------------------------------------------------------------------
# 模型层：新字段存在性
# ---------------------------------------------------------------------------

class TestCollusionExtractionModel:
    def test_subcontractors_info_column_exists(self):
        from app.models.collusion_extraction import CollusionExtraction
        assert hasattr(CollusionExtraction, "subcontractors_info")
        col = CollusionExtraction.subcontractors_info
        # SQLAlchemy 列应有 type
        assert hasattr(col, "type")
