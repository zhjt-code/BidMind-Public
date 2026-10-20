"""Task 8 串标分析器拆分 Phase 1 契约用例

锁定 `app.core.collusion.analyzer` 拆分前的对外契约：
1. 模块级符号导出契约（~20 个 _xxx 测试侧依赖符号）
2. CollusionAnalyzer 实例字段契约（12 个字段，兼容 object.__new__ 白盒测试）
3. run 编排序列契约
4. ExtractionQualityError 契约
5. 失败合同契约（pair_results=[] → overall_risk=inconclusive，防 回归）
6. pair 结果 schema 契约（_normalize_compare_result 输出键稳定）
7. 证据级风险推导契约（_derive_overall_risk_from_dims， 锚点）
8. 全局 finding 清洗契约（_recover_flattened_findings + _FLAT_FINDING_RECOVERED_FLAG， 锚点）

设计原则：
- 拆分**前**全部通过 → 锁定基线
- 拆分**中**任意 Step 跑这 8 个用例，零退化才可继续
- 拆分**后**仍全部通过 → 证明对外契约零变化
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


# =============================================================================
# 契约 1: 模块级符号导出契约
# =============================================================================

class TestModuleLevelSymbolExports:
    """所有测试侧依赖的模块级符号必须从 app.core.collusion.analyzer 顶层 import 可用"""

    def test_exception_and_class_exported(self):
        from app.core.collusion.analyzer import (
            CollusionAnalyzer,
            ExtractionQualityError,
        )
        assert isinstance(ExtractionQualityError, type)
        assert issubclass(ExtractionQualityError, Exception)
        assert isinstance(CollusionAnalyzer, type)

    def test_private_pure_function_symbols_exported(self):
        """20 个被外部测试直接 import 的 _xxx 符号必须全部可访问"""
        # 来源：基线 grep `from app.core.collusion.analyzer import` 收集
        from app.core.collusion import analyzer as mod

        required_symbols = [
            # constants 域
            "_FLAT_FINDING_RECOVERED_FLAG",
            "_is_empty_extraction",
            # extraction validation
            "_validate_extraction_payload",
            # algorithmic
            "_algorithm_compare",
            "_algorithm_d4_score",
            "_inject_d8_from_metadata",
            # findings
            "_extract_structured_findings",
            "_recover_flattened_findings",
            "_consistency_check",
            "_derive_overall_risk_from_dims",
            "_normalize_dim_evidence_levels",
            "_finalize_pair_contract",
            # synthesis helpers
            "_simple_synthesis",
            "_build_project_summary_payload",
            "_build_failed_pair_result",
            "_build_profile_quality_flags",
            "_build_global_findings_context",
            "_format_group_evidence_for_llm",
            "_apply_profile_confidence_to_candidate_scores",
        ]
        missing = [s for s in required_symbols if not hasattr(mod, s)]
        assert not missing, f"以下符号未在 analyzer 模块顶层导出: {missing}"

    def test_module_level_constants_exported(self):
        """模块级常量（被测试或子模块依赖）必须可访问"""
        from app.core.collusion import analyzer as mod

        constants = [
            "_DIM_MAP",
            "_DIM_KEYS",
            "_CERTAINTY_MIN",
            "_DECISION_ENGINE_VERSION",
            "_RISK_RANK_LOCAL",
            "_EXTRACTION_FAIL_THRESHOLD",
            "_MAX_EXTRACT_CHARS",
            "_MAX_TENDER_CHARS",
            "_IMAGE_BUDGET",
            "_DIMENSION_RETRIEVAL",
            "_FLAT_FINDING_RECOVERED_FLAG",
        ]
        missing = [c for c in constants if not hasattr(mod, c)]
        assert not missing, f"以下常量未在 analyzer 模块顶层导出: {missing}"


# =============================================================================
# 契约 2: CollusionAnalyzer 实例字段契约
# =============================================================================

class TestCollusionAnalyzerInstanceFields:
    """object.__new__(CollusionAnalyzer) + 手填字段的白盒测试模式必须可用

    test_collusion_input_guard / test_collusion_evidence_refs 等多个测试通过
    object.__new__ 跳过 __init__ 创建实例，手动填 _db/_client/_db_lock，因此
    字段名必须保持稳定。
    """

    def test_init_creates_all_required_fields(self):
        from app.core.collusion.analyzer import CollusionAnalyzer
        import inspect

        src = inspect.getsource(CollusionAnalyzer.__init__)
        required_fields = [
            "self._db",
            "self._project_id",
            "self._heartbeat_cb",
            "self._client",
            "self._semaphore",
            "self._db_lock",
            "self._dim_retry",
            "self._synthesis_retry",
            "self._group_retry",
            "self._global_findings_extra",
            "self._project_owner_id_cache",
            "self._project_owner_id_loaded",
            "self._kb_usage_loaded",
            "self._kb_usage_allowed_ids",
            "self._kb_usage_active",
        ]
        missing = [f for f in required_fields if f not in src]
        assert not missing, f"CollusionAnalyzer.__init__ 缺失以下字段赋值: {missing}"

    def test_class_method_call_pattern_preserved(self):
        """test_collusion_bug296_fix.py 通过 CollusionAnalyzer._extraction_to_dict(None, ext) 调用

        这种 classmethod 风格调用要求 _extraction_to_dict 在 CollusionAnalyzer 主类上可用
        （不下沉到子模块）。
        """
        from app.core.collusion.analyzer import CollusionAnalyzer

        assert hasattr(CollusionAnalyzer, "_extraction_to_dict")
        assert hasattr(CollusionAnalyzer, "_get_algorithm_d4_hits")
        assert hasattr(CollusionAnalyzer, "_save_extraction")
        assert hasattr(CollusionAnalyzer, "run")

    def test_save_extraction_source_contains_subcontractors_string(self):
        """ 通过 inspect.getsource(_save_extraction) 静态检查源码字符串

 这要求 _save_extraction 实现体必须留在主类（不能仅是 thin forwarder），
 否则 inspect.getsource 拿不到原始 'subcontractors_info' / 'subcontractors' 字符串。
 """
        from app.core.collusion.analyzer import CollusionAnalyzer
        import inspect

        src = inspect.getsource(CollusionAnalyzer._save_extraction)
        assert '"subcontractors_info"' in src or "'subcontractors_info'" in src
        assert 'metadata_info.pop("subcontractors"' in src


# =============================================================================
# 契约 3: run() 编排序列契约
# =============================================================================

class TestRunOrchestrationSequence:
    """run() 必须按既定 S1a→S1b/c→S1d→S2→S3→S3.5→S3.6→S4→save_summary 顺序调用"""

    def test_run_calls_stages_in_correct_order(self):
        """通过源码静态检查锁定 run() 内部调用序列

        实际跑 run() 需要完整 fixture，本用例改为读源码确认调用顺序锚点。
        """
        from app.core.collusion.analyzer import CollusionAnalyzer
        import inspect

        src = inspect.getsource(CollusionAnalyzer.run)

        # 关键调用锚点（按出现顺序）；统一加 `(` 后缀确保命中实际函数调用而非注释
        anchors = [
            "_load_documents(",                       # 文档加载
            "_extract_tender_format(",                # S1a
            "_extract_all_bid_docs(",                 # S1b
            "batch_compute_similarity(",              # S1c
            "_generate_industry_profile(",            # S1d
            "_check_extraction_quality(",             # 质量门
            "ExtractionQualityError(",                # 失败合同
            "_run_deep_comparison(",                  # S3
            "_save_skipped_pairs(",                   # S3
            "build_collusion_graph(",                 # S3.5
            "_save_groups(",                          # S3.5
            "_fuse_group_evidence(",                  # S3.6
            "_run_group_analysis(",                   # S4
            "_run_overall_synthesis(",                # S4
            "_build_project_summary_payload(",        # 汇总
            "_save_project_summary(",                 # 持久化
        ]
        positions = [src.find(a) for a in anchors]
        missing = [a for a, p in zip(anchors, positions) if p == -1]
        assert not missing, f"run() 内缺失以下调用锚点: {missing}"

        # 验证顺序（每个锚点 first occurrence 必须严格递增）
        for i in range(1, len(anchors)):
            assert positions[i] > positions[i - 1], (
                f"run() 调用顺序错乱: '{anchors[i - 1]}' 出现在 '{anchors[i]}' 之后"
            )


# =============================================================================
# 契约 4: ExtractionQualityError 契约
# =============================================================================

class TestExtractionQualityErrorContract:
    def test_is_exception_subclass(self):
        from app.core.collusion.analyzer import ExtractionQualityError
        assert issubclass(ExtractionQualityError, Exception)

    def test_can_be_raised_and_caught(self):
        from app.core.collusion.analyzer import ExtractionQualityError
        with pytest.raises(ExtractionQualityError) as exc_info:
            raise ExtractionQualityError("提取质量不达标")
        assert str(exc_info.value) == "提取质量不达标"

    def test_is_caught_by_generic_exception(self):
        """collusion_task.py 中通过 isinstance(e, ExtractionQualityError) 区分"""
        from app.core.collusion.analyzer import ExtractionQualityError
        err = ExtractionQualityError("test")
        assert isinstance(err, Exception)


# =============================================================================
# 契约 5: 失败合同契约（防御）
# =============================================================================

class TestFailureContract:
    """_build_project_summary_payload 在无有效数据时必须返回 overall_risk=inconclusive

    docs/failure_contract.md §3.2 红线：串标分析失败时禁止用 overall_risk=low 兜底。
    """

    def test_empty_pairs_and_clusters_yields_inconclusive(self):
        from app.core.collusion.analyzer import _build_project_summary_payload

        payload = _build_project_summary_payload(
            synthesis={"overall_risk": "low", "summary": "x"},
            pair_results=[],
            clusters=[],
            total_pairs=0,
            llm_pair_count=0,
            skipped_count=0,
            has_tender_doc=False,
            industry_profile=None,
        )
        assert payload["overall_risk"] == "inconclusive"
        assert payload["decision_engine_version"] >= 1

    def test_all_inconclusive_pairs_yields_inconclusive_or_no_high(self):
        """全 inconclusive pair → 项目级 overall_risk 不应被静默拉高到 high/medium"""
        from app.core.collusion.analyzer import _build_project_summary_payload

        pair_results = [
            {"overall_risk": "inconclusive", "pair_ref": {"doc_a_id": 1, "doc_b_id": 2}},
            {"overall_risk": "inconclusive", "pair_ref": {"doc_a_id": 1, "doc_b_id": 3}},
        ]
        payload = _build_project_summary_payload(
            synthesis={"overall_risk": "inconclusive", "summary": ""},
            pair_results=pair_results,
            clusters=[],
            total_pairs=2,
            llm_pair_count=0,
            skipped_count=2,
            has_tender_doc=False,
            industry_profile=None,
        )
        assert payload["overall_risk"] in {"inconclusive", "none"}


# =============================================================================
# 契约 6: pair 结果 schema 契约
# =============================================================================

class TestPairResultSchema:
    """_normalize_compare_result 输出键集合稳定（前端 collusionApi.ts 强耦合）"""

    def test_pair_ref_keys_stable(self):
        from app.core.collusion.analyzer import _normalize_compare_result

        result = _normalize_compare_result(
            compare_result={"d1_personnel": {"score": 0.5, "findings": []}},
            doc_a_id=1,
            doc_b_id=2,
            doc_a_name="文件A",
            doc_b_name="文件B",
        )
        assert "pair_id" in result
        assert "pair_ref" in result
        ref = result["pair_ref"]
        assert ref["doc_a_id"] == 1
        assert ref["doc_b_id"] == 2
        assert ref["doc_a_name"] == "文件A"
        assert ref["doc_b_name"] == "文件B"
        # pair_id 形态：build_pair_id(1,2) 应输出可比较的字符串/数字
        assert ref["pair_id"] == result["pair_id"]

    def test_findings_normalized_to_dict(self):
        """str 类型的 finding 应被规范化为 dict（防）"""
        from app.core.collusion.analyzer import _normalize_compare_result

        result = _normalize_compare_result(
            compare_result={
                "d1_personnel": {"score": 0.7, "findings": ["纯字符串的finding"]},
            },
            doc_a_id=1,
            doc_b_id=2,
            doc_a_name="A",
            doc_b_name="B",
        )
        findings = result["d1_personnel"]["findings"]
        assert len(findings) == 1
        assert isinstance(findings[0], dict)
        assert findings[0]["summary"] == "纯字符串的finding"


# =============================================================================
# 契约 7: 证据级风险推导契约（锚点）
# =============================================================================

class TestRiskDerivationContract:
    """_derive_overall_risk_from_dims 关键路径锁定"""

    def test_no_data_yields_inconclusive(self):
        """所有维度均无数据 → inconclusive"""
        from app.core.collusion.analyzer import _derive_overall_risk_from_dims

        result = _derive_overall_risk_from_dims({})
        assert result["overall_risk"] == "inconclusive"

    def test_strong_d1_first_yields_high(self):
        """D1 强一级命中（score>=0.5 + certainty>=0.7）→ high"""
        from app.core.collusion.analyzer import _derive_overall_risk_from_dims

        compare = {
            "d1_personnel": {
                "score": 0.8,
                "certainty": 0.85,
                "evidence_level": "first",
            },
        }
        result = _derive_overall_risk_from_dims(compare)
        assert result["overall_risk"] == "high"

    def test_only_d6_third_does_not_yield_high(self):
        """仅 D6 third 单维 → 不应升至 HIGH/MEDIUM（三级证据单独不能定性）"""
        from app.core.collusion.analyzer import _derive_overall_risk_from_dims

        compare = {
            "d6_format": {
                "score": 0.4,
                "certainty": 0.5,
                "evidence_level": "third",
            },
        }
        result = _derive_overall_risk_from_dims(compare)
        # 单 D6 三级不构成 LOW/MEDIUM/HIGH（依据 collusion.mdc §二 综合研判规则）
        assert result["overall_risk"] in {"none", "low"}, (
            f"单 D6 三级证据不应升至 medium/high，实际: {result['overall_risk']}"
        )


# =============================================================================
# 契约 8.5: thin forwarder keyword-only 参数转发契约（防御）
# =============================================================================

class TestThinForwarderKeywordOnlyArgs:
    """主类 thin forwarder 必须以 keyword 方式把 *,kwonly 参数转发给子模块

    历史教训：Task 8 Step 6 脚本用 ast.args.args 取参数列表，**遗漏 args.kwonlyargs**，
    导致 6 个 thin forwarder 把 keyword-only 参数当 positional 传给子模块，触发
    `TypeError: takes 1 positional argument but N were given`，串标分析全链路失败。

    本契约通过 mock 子模块函数 + keyword 调用 thin forwarder 验证转发链路：
    """

    def test_run_multi_route_extraction_forwards_kwargs(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        import asyncio
        from app.core.collusion.analyzer import CollusionAnalyzer

        analyzer = object.__new__(CollusionAnalyzer)
        sentinel_doc = MagicMock()
        sentinel_contexts = {"entity": {"context": "x"}}
        with patch(
            "app.core.collusion.analyzer_parts.extraction_stage.run_multi_route_extraction",
            new=AsyncMock(return_value={"ok": True}),
        ) as mock_fn:
            result = asyncio.run(
                analyzer._run_multi_route_extraction(
                    doc=sentinel_doc, route_contexts=sentinel_contexts,
                )
            )
            assert result == {"ok": True}
            mock_fn.assert_called_once_with(
                analyzer, doc=sentinel_doc, route_contexts=sentinel_contexts,
            )

    def test_extract_route_payload_forwards_kwargs(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        import asyncio
        from app.core.collusion.analyzer import CollusionAnalyzer

        analyzer = object.__new__(CollusionAnalyzer)
        sentinel_doc = MagicMock()
        with patch(
            "app.core.collusion.analyzer_parts.extraction_stage.extract_route_payload",
            new=AsyncMock(return_value=("entity", {})),
        ) as mock_fn:
            asyncio.run(
                analyzer._extract_route_payload(
                    doc=sentinel_doc, route_name="entity", context_meta={},
                )
            )
            mock_fn.assert_called_once_with(
                analyzer, doc=sentinel_doc, route_name="entity", context_meta={},
            )

    def test_build_collusion_metadata_info_forwards_kwargs(self):
        from unittest.mock import MagicMock, patch
        from app.core.collusion.analyzer import CollusionAnalyzer

        analyzer = object.__new__(CollusionAnalyzer)
        with patch(
            "app.core.collusion.analyzer_parts.extraction_stage.build_collusion_metadata_info",
            new=MagicMock(return_value={}),
        ) as mock_fn:
            analyzer._build_collusion_metadata_info(extraction={"k": "v"}, file_path="/x")
            mock_fn.assert_called_once_with(
                analyzer, extraction={"k": "v"}, file_path="/x",
            )

    def test_llm_compare_forwards_mode_kwarg(self):
        """_llm_compare 是 positional 7 个 + kwonly 1 个（mode）的混合签名"""
        from unittest.mock import AsyncMock, MagicMock, patch
        import asyncio
        from app.core.collusion.analyzer import CollusionAnalyzer

        analyzer = object.__new__(CollusionAnalyzer)
        with patch(
            "app.core.collusion.analyzer_parts.pair_compare.llm_compare",
            new=AsyncMock(return_value={}),
        ) as mock_fn:
            asyncio.run(
                analyzer._llm_compare(
                    MagicMock(), MagicMock(), {}, {}, 0.5, [], None, mode="full",
                )
            )
            # mode 必须以 keyword 方式传给子模块
            args, kwargs = mock_fn.call_args
            assert kwargs.get("mode") == "full", (
                f"_llm_compare 必须把 mode 作为 keyword 转发，实际 args={args} kwargs={kwargs}"
            )

    def test_retrieve_dimension_context_forwards_kwargs(self):
        from unittest.mock import AsyncMock, patch
        import asyncio
        from app.core.collusion.analyzer import CollusionAnalyzer

        analyzer = object.__new__(CollusionAnalyzer)
        with patch(
            "app.core.collusion.analyzer_parts.pair_compare.retrieve_dimension_context",
            new=AsyncMock(return_value=""),
        ) as mock_fn:
            asyncio.run(
                analyzer._retrieve_dimension_context(
                    1, "entity", min_similarity=0.3, top_k=5, max_chars=1000,
                )
            )
            args, kwargs = mock_fn.call_args
            assert kwargs == {
                "min_similarity": 0.3,
                "top_k": 5,
                "max_chars": 1000,
            }, f"_retrieve_dimension_context kwargs 异常: {kwargs}"

    def test_get_algorithm_hits_forwards_include_d4(self):
        from unittest.mock import MagicMock, patch
        from app.core.collusion.analyzer import CollusionAnalyzer

        analyzer = object.__new__(CollusionAnalyzer)
        with patch(
            "app.core.collusion.analyzer_parts.pair_persistence.get_algorithm_hits",
            new=MagicMock(return_value=set()),
        ) as mock_fn:
            analyzer._get_algorithm_hits({1: {}}, include_d4=True)
            args, kwargs = mock_fn.call_args
            assert kwargs.get("include_d4") is True

    def test_get_algorithm_hits_not_decorated_with_classmethod_in_submodule(self):
        """ 防御：子模块函数顶层不得带 @classmethod / @staticmethod 装饰器

 Step 8 ast 提取 method 时把原 @classmethod 装饰器一起带到模块级，
 Python 在模块级 wrap 的 classmethod 对象不可调用，触发
 `'classmethod' object is not callable`。本测试通过实际调用（不 mock）
 验证子模块函数仍可直接调用。
 """
        from app.core.collusion.analyzer_parts import pair_persistence

        # 子模块顶层符号必须是 callable 的普通函数，不是 classmethod / staticmethod 描述符
        assert callable(pair_persistence.get_algorithm_hits)
        assert callable(pair_persistence.get_algorithm_d1d2_hits)
        # type 必须是 function，不是 classmethod / staticmethod
        assert type(pair_persistence.get_algorithm_hits).__name__ == "function"
        assert type(pair_persistence.get_algorithm_d1d2_hits).__name__ == "function"

    def test_get_algorithm_hits_end_to_end_no_decorator_error(self):
        """ 端到端：从主类 thin forwarder 真实调用到子模块函数体，
 不 mock 中间链路，确保 @classmethod / @staticmethod 装饰器残留会被立即捕获。
 """
        from app.core.collusion.analyzer import CollusionAnalyzer

        analyzer = object.__new__(CollusionAnalyzer)
        # 空 extractions 输入；任何装饰器残留导致的 'classmethod object is not callable'
        # 都会在此调用栈中被抛出。
        result = analyzer._get_algorithm_hits({}, include_d4=False)
        assert isinstance(result, set)
        assert result == set()  # 空输入无命中

        result2 = analyzer._get_algorithm_hits({}, include_d4=True)
        assert isinstance(result2, set)


# =============================================================================
# 契约 8.6: analyzer_parts 子模块静态完整性（防御）
# =============================================================================

class TestAnalyzerPartsStaticIntegrity:
    """所有 analyzer_parts 子模块必须无 undefined name + 可成功 import

 历史教训：Task 8 Step 6-9 用 ast 提取 method body 时，**只复制方法源码不扫描
 函数体内使用的模块级符号**（json/Document/resolve_user_template/normalizers
 等），导致子模块 80+ 个 undefined name 残留。这些错误只在运行到对应代码路径时
 暴露， 修复后第三天又暴露；现有测试未覆盖 S3 真实串标
 全链路是根因。

 本测试通过：
 1. 静态 pyflakes 扫描确保零 undefined name
 2. import 时机集成（import_module 真实加载）确保无 NameError 阻塞
 """

    def test_no_undefined_names_in_submodules(self):
        """pyflakes 扫描所有 analyzer_parts 子模块，零 undefined name 残留"""
        import subprocess
        import sys
        from pathlib import Path

        analyzer_parts_dir = Path(__file__).parent.parent / "app" / "core" / "collusion" / "analyzer_parts"
        assert analyzer_parts_dir.is_dir(), f"子包目录不存在: {analyzer_parts_dir}"

        result = subprocess.run(
            [sys.executable, "-m", "pyflakes", str(analyzer_parts_dir)],
            capture_output=True, text=True, encoding="utf-8",
        )
        output = (result.stdout or "") + (result.stderr or "")
        # pyflakes 缺失时 subprocess 报 "No module named pyflakes"，
        # 输出无 "undefined name" 导致本护栏静默空转，必须显式失败
        assert "No module named" not in output, (
            f"pyflakes 不可用，护栏空转（安装: pip install -r requirements-dev.txt）:\n{output}"
        )
        undefined_lines = [
            line for line in output.splitlines()
            if "undefined name" in line
        ]
        assert not undefined_lines, (
            f"analyzer_parts 子模块存在 undefined name（共 {len(undefined_lines)} 处）:\n"
            + "\n".join(undefined_lines[:30])
            + ("\n... (truncated)" if len(undefined_lines) > 30 else "")
        )

    def test_all_submodules_importable(self):
        """所有 analyzer_parts 子模块必须能成功 import（不在 import 时抛 NameError）"""
        import importlib

        submodules = [
            "constants",
            "extraction_validation",
            "algorithmic",
            "findings",
            "synthesis_helpers",
            "extraction_stage",
            "pair_compare",
            "pair_dimensions",
            "pair_evidence",
            "pair_persistence",
            "group_stage",
            "synthesis_stage",
        ]
        failed = []
        for name in submodules:
            try:
                importlib.import_module(f"app.core.collusion.analyzer_parts.{name}")
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{name}: {type(exc).__name__}: {exc}")
        assert not failed, "以下子模块 import 失败:\n" + "\n".join(failed)


# =============================================================================
# 契约 8: 全局 finding 清洗契约（锚点）
# =============================================================================

class TestFlattenedFindingRecovery:
    """_recover_flattened_findings 必须能恢复被 LLM 拍平的字符串列表"""

    def test_flag_constant_value_stable(self):
        from app.core.collusion.analyzer import _FLAT_FINDING_RECOVERED_FLAG
        assert _FLAT_FINDING_RECOVERED_FLAG == "flat_finding_recovered"

    def test_empty_list_no_recovery(self):
        from app.core.collusion.analyzer import _recover_flattened_findings
        output, recovered = _recover_flattened_findings([])
        assert output == []
        assert recovered is False

    def test_dict_findings_pass_through(self):
        from app.core.collusion.analyzer import _recover_flattened_findings
        raw = [{"summary": "ok", "detail": "..."}]
        output, recovered = _recover_flattened_findings(raw)
        assert output == raw
        assert recovered is False

    def test_flattened_strings_get_merged(self):
        """LLM 把 finding 拍平为字符串列表时应能合并回单条 dict"""
        from app.core.collusion.analyzer import _recover_flattened_findings

        raw = [
            'summary: <PERSON_A>同时出现在A/B两份投标',
            "detail: 法定代表人字段重叠",
            'quote_a: 项目经理 <PERSON_A>',
            "page_a: 12",
        ]
        output, recovered = _recover_flattened_findings(raw)
        assert recovered is True
        assert len(output) == 1
        merged = output[0]
        assert isinstance(merged, dict)
        assert merged.get("summary") == '<PERSON_A>同时出现在A/B两份投标'
        assert merged.get("page_a") == 12  # 自动转 int
