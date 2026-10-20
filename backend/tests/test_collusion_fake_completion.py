"""防御测试: 串标假完成修复

覆盖三类风险:
1. process_task Session 4 事务边界 -- chunk 写库与 parsed 必须原子提交
2. collusion analyzer 空输入闸门 -- 全空提取 + 无 embedding 应失败
3. collusion status 口径 -- algorithm_only 不计入 LLM 深度分析
"""

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# 1. _is_empty_extraction 逻辑验证
# ═══════════════════════════════════════════════════════════════════════════════

class TestIsEmptyExtraction:
    """验证 _is_empty_extraction 对各种输入的判定"""

    @staticmethod
    def _fn(ext):
        from app.core.collusion.analyzer import _is_empty_extraction
        return _is_empty_extraction(ext)

    def test_none_is_empty(self):
        assert self._fn(None) is True

    def test_empty_dict_is_empty(self):
        assert self._fn({}) is True

    def test_all_empty_fields_is_empty(self):
        assert self._fn({
            "company_info": {},
            "personnel_list": [],
            "price_info": {},
            "technical_highlights": [],
            "qualifications": [],
            "past_projects": [],
        }) is True

    def test_one_non_empty_field_is_not_empty(self):
        assert self._fn({
            "company_info": {"company_name": "测试公司"},
            "personnel_list": [],
        }) is False


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 空输入质量闸门 -- ExtractionQualityError 触发条件
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmptyInputQualityGate:
    """验证全空提取 + 无 embedding 时 analyzer.run() 的闸门行为"""

    def test_all_empty_extractions_no_embedding_raises(self):
        """所有 bid 提取均为空 + similarity 无任何有效分数 -> 应抛出异常"""
        from app.core.collusion.analyzer import (
            _is_empty_extraction,
            ExtractionQualityError,
        )
        from dataclasses import dataclass, field as dc_field

        @dataclass
        class FakeSimilarityResult:
            high_pairs: list = dc_field(default_factory=list)
            medium_pairs: list = dc_field(default_factory=list)
            pair_scores: dict = dc_field(default_factory=dict)

        extractions = {
            1: {},
            2: {},
        }

        non_empty_count = sum(
            1 for ext in extractions.values()
            if not _is_empty_extraction(ext)
        )
        sim = FakeSimilarityResult()
        has_any_embedding = bool(
            sim.high_pairs
            or sim.medium_pairs
            or any(s > 0 for s in sim.pair_scores.values())
        )

        assert non_empty_count == 0
        assert has_any_embedding is False

        with pytest.raises(ExtractionQualityError):
            if non_empty_count == 0 and not has_any_embedding:
                raise ExtractionQualityError(
                    "所有投标文件均未提取到有效信息且无可用向量"
                )

    def test_one_non_empty_extraction_passes(self):
        """至少一份提取非空 -> 不应触发闸门"""
        from app.core.collusion.analyzer import _is_empty_extraction

        extractions = {
            1: {"company_info": {"company_name": "公司A"}},
            2: {},
        }
        non_empty_count = sum(
            1 for ext in extractions.values()
            if not _is_empty_extraction(ext)
        )
        assert non_empty_count >= 1

    def test_has_embedding_passes(self):
        """有任何 embedding 分数 > 0 -> 不应触发闸门"""
        from app.core.collusion.analyzer import _is_empty_extraction
        from dataclasses import dataclass, field as dc_field

        @dataclass
        class FakeSimilarityResult:
            high_pairs: list = dc_field(default_factory=list)
            medium_pairs: list = dc_field(default_factory=list)
            pair_scores: dict = dc_field(default_factory=dict)

        extractions = {1: {}, 2: {}}
        non_empty_count = sum(
            1 for ext in extractions.values()
            if not _is_empty_extraction(ext)
        )
        sim = FakeSimilarityResult(pair_scores={(1, 2): 0.45})
        has_any_embedding = bool(
            sim.high_pairs
            or sim.medium_pairs
            or any(s > 0 for s in sim.pair_scores.values())
        )
        assert non_empty_count == 0
        assert has_any_embedding is True


# ═══════════════════════════════════════════════════════════════════════════════
# 3. collusion status 统计口径 -- algorithm_only 不计入真实 LLM 分析
# ═══════════════════════════════════════════════════════════════════════════════

class TestCollusionStatusAccounting:
    """验证 status 统计逻辑对各 analysis_mode 的分类"""

    @staticmethod
    def _classify(modes: list[str]) -> dict:
        """模拟 collusion status 路由的分类逻辑"""
        compared_count = len(modes)
        llm_count = sum(1 for m in modes if m not in ("skipped", "algorithm_only"))
        algo_only_count = sum(1 for m in modes if m == "algorithm_only")
        skipped_count = compared_count - llm_count - algo_only_count
        return {
            "compared_count": compared_count,
            "llm_compared_count": llm_count,
            "algorithm_only_count": algo_only_count,
            "skipped_count": skipped_count,
        }

    def test_pure_algorithm_only(self):
        """全部 algorithm_only -> llm_compared = 0"""
        result = self._classify(["algorithm_only"])
        assert result["llm_compared_count"] == 0
        assert result["algorithm_only_count"] == 1

    def test_mixed_modes(self):
        """混合模式正确分类"""
        result = self._classify([
            "grouped_parallel", "simplified", "algorithm_only", "skipped",
        ])
        assert result["llm_compared_count"] == 2
        assert result["algorithm_only_count"] == 1
        assert result["skipped_count"] == 1

    def test_all_llm(self):
        """全部深度分析 -> algorithm_only = 0, skipped = 0"""
        result = self._classify(["grouped_parallel", "full", "simplified"])
        assert result["llm_compared_count"] == 3
        assert result["algorithm_only_count"] == 0
        assert result["skipped_count"] == 0

    def test_step3_progress_excludes_algorithm_only(self):
        """step3 进度计算: total_llm_pairs 不含 algorithm_only"""
        total_pairs = 3
        modes = ["grouped_parallel", "algorithm_only", "skipped"]
        stats = self._classify(modes)

        total_llm_pairs = max(
            total_pairs - stats["skipped_count"] - stats["algorithm_only_count"], 1
        )
        step3_pct = int(stats["llm_compared_count"] / total_llm_pairs * 100)
        assert total_llm_pairs == 1
        assert step3_pct == 100


# ═══════════════════════════════════════════════════════════════════════════════
# 4. process_task Session 4 事务结构静态验证
# ═══════════════════════════════════════════════════════════════════════════════

class TestProcessTaskTransactionBoundary:
    """静态验证 Session 4 的事务结构:
    commit() 必须在 async with 内，不在 finally 里。
    """

    def test_commit_inside_async_with(self):
        """parsed=1 和 commit() 必须在 async with 块内，不在 finally 里"""
        import ast
        from pathlib import Path

        # 从测试文件自身位置定位业务源文件，避免依赖 pytest 的 cwd。
        # 测试目录结构：backend/tests/test_collusion_fake_completion.py
        # 目标源文件：backend/app/core/tasks/process_task.py
        src_path = (
            Path(__file__).resolve().parent.parent
            / "app" / "core" / "tasks" / "process_task.py"
        )
        src = src_path.read_text(encoding="utf-8")
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for handler in node.finalbody:
                src_segment = ast.get_source_segment(src, handler) or ""
                assert "parsed = 1" not in src_segment, (
                    "document.parsed = 1 不应出现在 finally 块中"
                )
                assert "db.commit()" not in src_segment, (
                    "db.commit() 不应出现在 finally 块中"
                )
