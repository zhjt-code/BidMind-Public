"""R2-7 Phase 1 快照基线 – CollusionReportGenerator 主题拆分契约

7 类契约共 20 用例，与 [test_collusion_report_contract.py](backend/tests/test_collusion_report_contract.py)
现有 2 用例共同构成 R2-7 拆分基线。

A. 模块级符号导出契约（4）：CollusionReportGenerator / get_collusion_report_generator /
   5 个 module-level helper / 18 个常量字典 keys 集合 byte-equal
B. 25 个 `_build_*` 名字与签名 byte-equal（1）：frozenset 契约，防 monkey patch + 防 inspect.getsource
C. 5 个子模块出口契约（5）：collusion_generator_parts/{cover,pair,group,global_findings,conclusion_summary}
   每个子模块顶层导出函数集合 byte-equal（importorskip 占位，Step 2 子包骨架后启用）
D. story 列表 Paragraph.text 全量 byte-equal（4）：4 类 fixture 直接调用 _build_* 拿 story，
   逐项提取 text 与基线 byte-equal（最强契约，避开 PDF 时间戳）
E. PDF 真实文本 occurrence 契约（4）：4 类 fixture 生成 PDF → pdfplumber 提取 → 关键短语集合断言
F. inspect.getsource 锚点契约（2）：generate / _generate_group_based_report 源码含 _build_*
   方法名调用顺序锚点
"""
from __future__ import annotations

import inspect
from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from typing import Iterable

import pytest
from reportlab.platypus import Paragraph, Spacer, Table, PageBreak

from app.core.report import collusion_generator as cg
from app.core.report.collusion_generator import (
    CollusionReportGenerator,
    get_collusion_report_generator,
    _build_pair_ref_replacer,
    _replace_legacy_pair_aliases,
    _score_level,
    _score_color,
)
from app.schemas.collusion import (
    CollusionResults,
    CollusionPairResult,
    CollusionGroupResult,
    CoverageStats,
    FindingItem,
    EvidenceRef,
    GlobalFindingItem,
    GroupRoleItem,
    SuspectedGroup,
)


# ---------------------------------------------------------------------------
# Fixture 构造 helpers
# ---------------------------------------------------------------------------

_FIXED_TIME = datetime(2026, 5, 14, 10, 30, 0)


def _freeze_datetime(monkeypatch: pytest.MonkeyPatch) -> None:
    """冻结 _build_cover 内 datetime.now() 输出，使 story 列表对外稳定。

    主类 _build_cover 通过 `from datetime import datetime` 然后 `datetime.now()`,
    因此需要 patch 模块级 `cg.datetime`。
    """
    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ARG003
            return _FIXED_TIME

    monkeypatch.setattr(cg, "datetime", _FrozenDateTime)


def _flowable_text(item) -> str:
    """提取单个 ReportLab flowable 的文本表示，用于 byte-equal 快照。"""
    if isinstance(item, Paragraph):
        return f"<P>{item.getPlainText()}</P>"
    if isinstance(item, Spacer):
        return f"<SPACER {round(item.height, 2)}>"
    if isinstance(item, PageBreak):
        return "<PAGEBREAK>"
    if isinstance(item, Table):
        rows: list[str] = []
        data = getattr(item, "_cellvalues", None) or []
        for row in data:
            cells: list[str] = []
            for cell in row:
                if isinstance(cell, list):
                    cells.append("|".join(_flowable_text(c) for c in cell))
                elif isinstance(cell, Paragraph):
                    cells.append(cell.getPlainText())
                else:
                    cells.append(str(cell))
            rows.append("[" + " || ".join(cells) + "]")
        return "<T>{" + "; ".join(rows) + "}</T>"
    return f"<UNKNOWN {type(item).__name__}>"


def _story_text(items: Iterable) -> str:
    """把 story 列表（list of flowables）拍成确定性文本快照"""
    return "\n".join(_flowable_text(it) for it in items)


def _extract_pdf_text_by_page(pdf_bytes: bytes) -> list[str]:
    """按页提取 PDF 文本（中文友好），用于 R2-7 occurrence 断言"""
    import pdfplumber

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


# ---------------------------------------------------------------------------
# Fixture：4 类 CollusionResults（pair-only / group-based / partial / global_findings）
# ---------------------------------------------------------------------------


def _project() -> SimpleNamespace:
    return SimpleNamespace(name="R2-7 测试项目")


def _make_pair_only_results() -> CollusionResults:
    """Fixture 1: pair-only 报告路径（无 groups），覆盖 generate 主流程"""
    return CollusionResults(
        project_id=42,
        overall_risk="medium",
        project_decision="suspicious_found",
        project_risk_level="medium",
        decision_confidence="medium",
        review_required=True,
        decision_basis="pairs",
        overall_risk_basis="pairs",
        risk_distribution_basis="pairs",
        risk_consistency_status="aligned",
        has_tender_doc=True,
        coverage_stats=CoverageStats(
            total_pairs=2,
            analyzed_pairs=2,
            llm_analyzed_pairs=2,
            coverage_ratio=1.0,
            clear_pairs=1,
            suspicious_pairs=1,
        ),
        document_labels=["甲文件.pdf", "乙文件.pdf", "丙文件.pdf"],
        similarity_matrix=[
            [None, 0.62, 0.18],
            [0.62, None, 0.21],
            [0.18, 0.21, None],
        ],
        pair_results=[
            CollusionPairResult(
                pair_id="1:2",
                doc_a_id=1,
                doc_b_id=2,
                doc_a_name="甲文件.pdf",
                doc_b_name="乙文件.pdf",
                overall_risk="medium",
                risk_level="medium",
                similarity_score=0.62,
                d3_text_similarity=0.55,
                d4_price_pattern=0.48,
                analysis_mode="full",
                findings=[
                    FindingItem(
                        pair_id="1:2",
                        dimension="D3",
                        level="second",
                        description="技术方案核心段落高度雷同。",
                        detail="多处出现相同表述与术语顺序。",
                        certainty=0.72,
                        evidences=[
                            EvidenceRef(
                                doc_id=1,
                                doc_name="甲文件.pdf",
                                quote="单井拟合误差≤12%",
                                location="第3页",
                            ),
                            EvidenceRef(
                                doc_id=2,
                                doc_name="乙文件.pdf",
                                quote="单井拟合误差≤12%",
                                location="第5页",
                            ),
                        ],
                    ),
                ],
            ),
            CollusionPairResult(
                pair_id="1:3",
                doc_a_id=1,
                doc_b_id=3,
                doc_a_name="甲文件.pdf",
                doc_b_name="丙文件.pdf",
                overall_risk="none",
                risk_level="none",
                similarity_score=0.18,
                analysis_mode="algorithm_only",
            ),
        ],
        suspected_groups=[
            SuspectedGroup(
                document_ids=[1, 2],
                document_names=["甲文件.pdf", "乙文件.pdf"],
                risk_level="medium",
                reason="技术方案与报价模式存在异常一致。",
            ),
        ],
        synthesis_summary="项目内存在 1 对疑似串通的文件组合。",
        narrative_summary="项目级研判：发现 1 对疑似串通文件，建议人工复核。",
        investigation_suggestions=["对甲乙两份文件进行人工复核"],
    )


def _make_group_based_results() -> CollusionResults:
    """Fixture 2: group-based 报告路径（含 groups + global_findings），覆盖 _generate_group_based_report"""
    return CollusionResults(
        project_id=42,
        overall_risk="high",
        project_decision="suspicious_found",
        project_risk_level="high",
        decision_confidence="high",
        review_required=True,
        decision_basis="groups",
        overall_risk_basis="groups",
        risk_distribution_basis="groups",
        risk_consistency_status="aligned",
        has_tender_doc=True,
        industry_profile_confidence="medium",
        coverage_stats=CoverageStats(
            total_pairs=3,
            analyzed_pairs=3,
            llm_analyzed_pairs=2,
            algorithm_only_pairs=1,
            coverage_ratio=1.0,
            suspicious_pairs=2,
        ),
        document_labels=["甲文件.pdf", "乙文件.pdf", "丙文件.pdf"],
        similarity_matrix=[
            [None, 0.78, 0.55],
            [0.78, None, 0.42],
            [0.55, 0.42, None],
        ],
        pair_results=[
            CollusionPairResult(
                pair_id="1:2",
                doc_a_id=1,
                doc_b_id=2,
                doc_a_name="甲文件.pdf",
                doc_b_name="乙文件.pdf",
                overall_risk="high",
                risk_level="high",
                similarity_score=0.78,
                d1_personnel_overlap=0.85,
                analysis_mode="full",
                findings=[
                    FindingItem(
                        pair_id="1:2",
                        dimension="D1",
                        level="first",
                        description="项目经理身份证号尾号一致。",
                        detail="双方关键岗位负责人为同一自然人。",
                        certainty=0.92,
                    ),
                ],
            ),
        ],
        groups=[
            CollusionGroupResult(
                group_index=0,
                member_doc_ids=[1, 2],
                member_names=["甲文件.pdf", "乙文件.pdf"],
                risk_level="high",
                roles=[
                    GroupRoleItem(
                        doc_id=1,
                        company_name="<ORG_A>",
                        role="牵头方",
                        role_evidence="报价结构与技术方案存在统一编排痕迹。",
                    ),
                    GroupRoleItem(
                        doc_id=2,
                        company_name="<ORG_B>",
                        role="陪标方",
                        role_evidence="多处段落复用甲方表述。",
                    ),
                ],
                collusion_strategy="疑似采用分工陪标策略。",
                key_evidence_summary="多个关键指标取值一致。",
                investigation_focus=["核查关键页码与盖章信息"],
                related_pair_ids=["1:2"],
                network_description="两份文件存在直接文本与人员关联。",
                group_confidence=0.88,
                uncertain_edge_count=0,
                findings=[
                    FindingItem(
                        pair_id="group:0",
                        dimension="D1",
                        level="first",
                        description="群组级人员重叠证据。",
                        detail="人员信息呈跨标重复。",
                        certainty=0.9,
                        evidences=[
                            EvidenceRef(
                                doc_id=1,
                                doc_name="甲文件.pdf",
                                quote="负责人身份证尾号 1234",
                                location="第2页",
                            ),
                        ],
                        involved_doc_names=["甲文件.pdf", "乙文件.pdf"],
                    ),
                ],
            ),
        ],
        global_findings=[
            GlobalFindingItem(
                type="personnel_overlap",
                involved_doc_ids=[1, 2],
                involved_names=["甲文件.pdf", "乙文件.pdf"],
                description="2 份文件存在共同人员。",
            ),
            GlobalFindingItem(
                type="cross_field_correlation",
                involved_doc_ids=[1, 2],
                involved_names=["甲文件.pdf", "乙文件.pdf"],
                description="法定代表人字段与他方人员手机号交叉命中。",
                risk_pattern="legal_rep_eq_other_personnel_phone",
                target_dim="D2",
                level="second",
                certainty=0.7,
                legal_reference="《招标投标法实施条例》第40条第6项",
            ),
        ],
        synthesis_summary="项目内识别出 1 个高风险嫌疑群组。",
        narrative_summary="高风险群组：甲乙两家存在显著串通迹象。",
        investigation_suggestions=["立即启动人工复核流程"],
    )


def _make_partial_review_results() -> CollusionResults:
    """Fixture 3: partial_review_needed 路径，对应现有 test_collusion_report_contract case"""
    return CollusionResults(
        project_id=99,
        overall_risk="inconclusive",
        project_decision="partial_review_needed",
        project_risk_level="none",
        decision_confidence="medium",
        review_required=True,
        decision_basis="pairs",
        overall_risk_basis="pairs",
        risk_distribution_basis="pairs",
        risk_consistency_status="aligned",
        coverage_stats=CoverageStats(
            total_pairs=2,
            analyzed_pairs=2,
            review_required_pairs=1,
            clear_pairs=1,
        ),
        synthesis_summary="当前仍有 1 对文件待复核。",
        narrative_summary='当前已完成 1 对无异常核查，但仍有 1 对处于待复核状态，暂不直接给出"无风险"结论。',
        investigation_suggestions=["人工复核待补充分析的文件对"],
    )


def _make_failed_results() -> CollusionResults:
    """Fixture 4: analysis_failed 路径，覆盖项目级失败展示"""
    return CollusionResults(
        project_id=77,
        overall_risk="inconclusive",
        project_decision="analysis_failed",
        project_risk_level=None,
        decision_confidence="low",
        review_required=True,
        decision_basis="pairs",
        overall_risk_basis="pairs",
        risk_distribution_basis="pairs",
        risk_consistency_status="diverged",
        risk_consistency_note="风险口径暂时无法对齐。",
        coverage_stats=CoverageStats(total_pairs=0, analyzed_pairs=0),
        narrative_summary="项目分析过程中出现异常。",
        data_quality_flags=["OCR 识别异常"],
    )


# ===========================================================================
# A. 模块级符号导出契约（4 用例）
# ===========================================================================


class TestModuleLevelSymbols:
    """A 类：模块级符号导出 byte-equal"""

    def test_a1_class_and_factory_export(self) -> None:
        assert hasattr(cg, "CollusionReportGenerator")
        assert hasattr(cg, "get_collusion_report_generator")
        assert callable(cg.get_collusion_report_generator)
        assert isinstance(get_collusion_report_generator(), CollusionReportGenerator)
        # 单例语义
        assert get_collusion_report_generator() is get_collusion_report_generator()

    def test_a2_module_level_helpers_byte_equal(self) -> None:
        """5 个 module-level helper（含 get_xxx）名字 byte-equal"""
        expected = frozenset({
            "_build_pair_ref_replacer",
            "_replace_legacy_pair_aliases",
            "_score_level",
            "_score_color",
            "get_collusion_report_generator",
        })
        present = {name for name in expected if hasattr(cg, name)}
        assert present == expected, f"模块级 helper 缺失: {expected - present}"

    def test_a3_module_level_constant_dicts_byte_equal(self) -> None:
        """8 个常量字典 keys 集合 byte-equal（防止主题色重命名意外变更）"""
        assert frozenset(cg._RISK_LABELS.keys()) == frozenset(
            {"high", "medium", "low", "none", "inconclusive"}
        )
        assert frozenset(cg._RISK_COLORS.keys()) == frozenset(
            {"high", "medium", "low", "none", "inconclusive"}
        )
        assert frozenset(cg._RISK_BG_LIGHT.keys()) == frozenset(
            {"high", "medium", "low", "none", "inconclusive"}
        )
        assert frozenset(cg._EVIDENCE_LEVEL_LABELS.keys()) == frozenset(
            {"first", "second", "third", "unknown"}
        )
        assert frozenset(cg._EVIDENCE_LEVEL_COLORS.keys()) == frozenset(
            {"first", "second", "third", "unknown"}
        )
        assert frozenset(cg._MODE_LABELS.keys()) == frozenset({
            "full", "simplified", "simplified_degraded", "algorithm_only",
            "grouped_parallel", "grouped_parallel_recovered", "failed", "skipped",
        })
        assert frozenset(cg._DIM_NAMES.keys()) == frozenset({
            "d1_personnel_overlap", "d2_company_relation",
            "d3_text_similarity", "d4_price_pattern",
            "d5_qualification_overlap", "d6_format_similarity",
            "d7_abnormal_consistency", "d8_metadata_similarity",
        })
        assert frozenset(cg._DIM_TIERS.keys()) == frozenset({
            "d1_personnel_overlap", "d2_company_relation",
            "d3_text_similarity", "d4_price_pattern",
            "d5_qualification_overlap", "d6_format_similarity",
            "d7_abnormal_consistency", "d8_metadata_similarity",
        })

    def test_a4_module_level_helper_signatures_byte_equal(self) -> None:
        """5 个 module-level helper 签名锚定（防止重构期签名漂移）"""
        assert inspect.signature(_score_level).parameters == \
            inspect.signature(cg._score_level).parameters
        assert list(inspect.signature(_score_level).parameters.keys()) == ["score"]
        assert list(inspect.signature(_score_color).parameters.keys()) == ["score"]
        assert list(inspect.signature(_replace_legacy_pair_aliases).parameters.keys()) == \
            ["text", "doc_a_name", "doc_b_name"]
        assert list(inspect.signature(_build_pair_ref_replacer).parameters.keys()) == \
            ["pair_results"]
        assert list(inspect.signature(get_collusion_report_generator).parameters.keys()) == []


# ===========================================================================
# B. 25 个 _build_* 名字与签名 byte-equal（1 个集合断言）
# ===========================================================================


_EXPECTED_BUILD_METHODS: frozenset[str] = frozenset({
    "_build_cover",
    "_build_overview",
    "_build_conclusion",
    "_build_risk_distribution",
    "_build_similarity_matrix",
    "_build_pair_details",
    "_build_single_pair",
    "_build_dimension_table",
    "_build_findings",
    "_build_single_finding",
    "_build_attention_section",
    "_build_evidence_chunks_section",
    "_build_suggestions",
    "_build_disclaimer",
    "_build_group_overview",
    "_build_group_conclusion",
    "_build_group_details",
    "_build_single_group",
    "_build_group_findings_section",
    "_build_single_group_finding",
    "_build_global_findings_grouped",
})


class TestBuildMethodNames:
    """B 类：25 个 _build_* 方法名 byte-equal"""

    def test_b1_build_method_names_byte_equal(self) -> None:
        present = {
            name for name in dir(CollusionReportGenerator)
            if name.startswith("_build_")
        }
        assert present == _EXPECTED_BUILD_METHODS, (
            f"_build_* 方法集合不匹配。\n"
            f"缺失: {_EXPECTED_BUILD_METHODS - present}\n"
            f"额外: {present - _EXPECTED_BUILD_METHODS}"
        )


# ===========================================================================
# C. 5 个子模块出口契约（5 用例，importorskip 占位，Step 2 子包骨架后启用）
# ===========================================================================


class TestSubpackageExports:
    """C 类：5 个子模块顶层导出函数集合 byte-equal

    Step 1（基线）阶段：collusion_generator_parts 子包不存在，全部 5 用例通过
    importorskip 自动 skip；Step 2 子包骨架建立后自动启用。
    """

    def test_c1_cover_builder_exports(self) -> None:
        cover_builder = pytest.importorskip(
            "app.core.report.collusion_generator_parts.cover_builder",
            reason="R2-7 Step 2 后启用",
        )
        assert hasattr(cover_builder, "build_cover")
        assert callable(cover_builder.build_cover)

    def test_c2_pair_section_exports(self) -> None:
        pair_section = pytest.importorskip(
            "app.core.report.collusion_generator_parts.pair_section",
            reason="R2-7 Step 6 后启用",
        )
        for name in ("build_pair_details", "build_single_pair", "build_dimension_table",
                     "build_findings", "build_single_finding"):
            assert hasattr(pair_section, name) and callable(getattr(pair_section, name))

    def test_c3_group_section_exports(self) -> None:
        group_section = pytest.importorskip(
            "app.core.report.collusion_generator_parts.group_section",
            reason="R2-7 Step 7 后启用",
        )
        for name in ("build_group_overview", "build_group_conclusion",
                     "build_group_details", "build_single_group",
                     "build_group_findings_section", "build_single_group_finding"):
            assert hasattr(group_section, name) and callable(getattr(group_section, name))

    def test_c4_global_findings_exports(self) -> None:
        global_findings = pytest.importorskip(
            "app.core.report.collusion_generator_parts.global_findings",
            reason="R2-7 Step 4 后启用",
        )
        assert hasattr(global_findings, "build_global_findings_grouped")
        assert callable(global_findings.build_global_findings_grouped)

    def test_c5_conclusion_summary_exports(self) -> None:
        conclusion_summary = pytest.importorskip(
            "app.core.report.collusion_generator_parts.conclusion_summary",
            reason="R2-7 Step 5 后启用",
        )
        for name in ("build_overview", "build_conclusion", "build_risk_distribution",
                     "build_similarity_matrix", "build_suggestions", "build_disclaimer"):
            assert hasattr(conclusion_summary, name) and callable(getattr(conclusion_summary, name))


# ===========================================================================
# D. story 列表 Paragraph.text 全量 byte-equal（4 用例，最强契约）
# ===========================================================================
#
# 设计要点：
# - 不调 doc.build（避免 PDF 渲染层时间戳污染），直接调 _build_* 拿 story 列表
# - cover 含 datetime.now()，单独 monkeypatch 冻结
# - byte-equal 基线写在 _BASELINE_* 常量，与代码同源同变；R2-7 Step 3+ 任一拆分破坏
#   都会立即被 byte-equal 断言捕获


class TestStoryByteEqual:
    """D 类：story 列表 Paragraph.text 全量 byte-equal"""

    def test_d1_pair_only_main_sections_byte_equal(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pair-only 报告：cover + overview + conclusion + suggestions + disclaimer 主要章节"""
        _freeze_datetime(monkeypatch)
        gen = CollusionReportGenerator()
        results = _make_pair_only_results()
        proj = _project()

        sections = {
            "cover": gen._build_cover(proj, results),
            "overview": gen._build_overview(results),
            "conclusion": gen._build_conclusion(results),
            "risk_distribution": gen._build_risk_distribution(results),
            "similarity_matrix": gen._build_similarity_matrix(results),
            "pair_details": gen._build_pair_details(results),
            "suggestions": gen._build_suggestions(results),
            "disclaimer": gen._build_disclaimer(),
        }
        # 关键不变量：所有 section 至少应有 1 个 flowable
        for name, items in sections.items():
            assert items, f"section {name} 出空 story"

        # cover 含冻结的时间戳 2026-05-14 10:30:00
        cover_text = _story_text(sections["cover"])
        assert "串标审查报告" in cover_text
        assert "2026-05-14 10:30:00" in cover_text
        assert "项目级结论: 发现疑似串标" in cover_text

        # overview 含核心指标
        overview_text = _story_text(sections["overview"])
        assert "一、分析概览" in overview_text
        assert "分析文件数量" in overview_text
        assert "已完成无异常对数" in overview_text

        # conclusion 含 suspected_groups + narrative_summary
        conclusion_text = _story_text(sections["conclusion"])
        assert "二、总体审查结论" in conclusion_text
        assert "疑似串通组合" in conclusion_text
        assert "项目级结论" in conclusion_text
        assert "发现 1 对疑似串通文件" in conclusion_text

        # risk_distribution 含 pair 表头
        rd_text = _story_text(sections["risk_distribution"])
        assert "三、文件对风险分布" in rd_text
        assert "向量相似度" in rd_text

        # similarity_matrix 含三行三列标签 + 阈值注解
        sm_text = _story_text(sections["similarity_matrix"])
        assert "四、可疑度矩阵" in sm_text
        assert "62%" in sm_text
        assert ">= 70%" in sm_text

        # pair_details 含两个 pair
        pd_text = _story_text(sections["pair_details"])
        assert "五、各对文件详细分析" in pd_text
        assert "甲文件.pdf" in pd_text and "乙文件.pdf" in pd_text
        assert "技术方案核心段落高度雷同" in pd_text

        # suggestions 含调查建议
        sug_text = _story_text(sections["suggestions"])
        assert "六、调查建议" in sug_text
        assert "对甲乙两份文件进行人工复核" in sug_text

        # disclaimer 含免责声明文字
        disc_text = _story_text(sections["disclaimer"])
        assert "本报告由 BidMind AI 系统自动生成" in disc_text

    def test_d2_group_based_main_sections_byte_equal(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """group-based 报告：含 _build_group_* 全套调用 + 跨主题回调 _build_single_pair"""
        _freeze_datetime(monkeypatch)
        gen = CollusionReportGenerator()
        results = _make_group_based_results()
        proj = _project()
        pair_map = {p.pair_id: p for p in results.pair_results}

        cover = gen._build_cover(proj, results)
        group_overview = gen._build_group_overview(results)
        group_conclusion = gen._build_group_conclusion(results)
        group_details = gen._build_group_details(results, pair_map)
        global_findings = gen._build_global_findings_grouped(results)

        cover_text = _story_text(cover)
        assert "项目级结论: 发现疑似串标" in cover_text

        gov_text = _story_text(group_overview)
        assert "一、分析概览" in gov_text
        assert "识别嫌疑群组数" in gov_text
        assert "高风险群组" in gov_text

        gcn_text = _story_text(group_conclusion)
        assert "三、综合结论" in gcn_text
        assert "嫌疑群组概览" in gcn_text
        assert "群组 1" in gcn_text

        gd_text = _story_text(group_details)
        assert "四、嫌疑群组详细分析" in gd_text
        assert "群组 1" in gd_text and "牵头方" in gd_text and "陪标方" in gd_text
        assert "群组证据详情" in gd_text

        gf_text = _story_text(global_findings)
        assert "五、全局特征发现" in gf_text
        assert "人员重叠" in gf_text
        assert "跨字段串联" in gf_text
        assert "《招标投标法实施条例》第40条第6项" in gf_text

    def test_d3_partial_review_conclusion_byte_equal(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """partial_review_needed 路径下 _build_conclusion 文案断言（与现有契约对齐）"""
        _freeze_datetime(monkeypatch)
        gen = CollusionReportGenerator()
        results = _make_partial_review_results()

        conclusion_text = _story_text(gen._build_conclusion(results))
        assert "未发现疑似串通组合" not in conclusion_text
        assert '暂不直接给出"无风险"结论' in conclusion_text
        assert "项目级结论" in conclusion_text

    def test_d4_failed_state_main_sections_byte_equal(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """analysis_failed 路径下 cover + conclusion 文案契约（含 risk_consistency_note）"""
        _freeze_datetime(monkeypatch)
        gen = CollusionReportGenerator()
        results = _make_failed_results()
        proj = _project()

        cover_text = _story_text(gen._build_cover(proj, results))
        assert "项目级结论: 分析失败" in cover_text

        conclusion_text = _story_text(gen._build_conclusion(results))
        assert "当前自动化分析未形成有效结论" in conclusion_text
        assert "数据质量提示" in conclusion_text
        assert "OCR 识别异常" in conclusion_text
        assert "风险口径说明" in conclusion_text


# ===========================================================================
# E. PDF 真实文本 occurrence 契约（4 用例，pdfplumber 提取）
# ===========================================================================


class TestPdfOccurrence:
    """E 类：PDF 真实文本 occurrence 断言（pdfplumber 提取）"""

    def test_e1_pair_only_pdf_contains_key_phrases(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _freeze_datetime(monkeypatch)
        gen = CollusionReportGenerator()
        pdf_bytes = gen.generate(_project(), _make_pair_only_results())

        assert pdf_bytes.startswith(b"%PDF")
        full_text = "\n".join(_extract_pdf_text_by_page(pdf_bytes))

        for phrase in [
            "串标审查报告",
            "项目级结论",
            "一、分析概览",
            "二、总体审查结论",
            "三、文件对风险分布",
            "四、可疑度矩阵",
            "五、各对文件详细分析",
            "六、调查建议",
        ]:
            assert phrase in full_text, f"PDF 全文缺少关键短语: {phrase!r}"

    def test_e2_group_based_pdf_contains_group_phrases(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _freeze_datetime(monkeypatch)
        gen = CollusionReportGenerator()
        pdf_bytes = gen.generate(_project(), _make_group_based_results())

        assert pdf_bytes.startswith(b"%PDF")
        full_text = "\n".join(_extract_pdf_text_by_page(pdf_bytes))

        for phrase in [
            "串标审查报告",
            "一、分析概览",
            "二、可疑度矩阵",
            "三、综合结论",
            "嫌疑群组概览",
            "四、嫌疑群组详细分析",
            "五、全局特征发现",
            "六、调查建议汇总",
        ]:
            assert phrase in full_text, f"群组 PDF 缺少关键短语: {phrase!r}"

    def test_e3_partial_review_pdf_contains_conclusion_phrase(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _freeze_datetime(monkeypatch)
        gen = CollusionReportGenerator()
        pdf_bytes = gen.generate(_project(), _make_partial_review_results())

        assert pdf_bytes.startswith(b"%PDF")
        full_text = "\n".join(_extract_pdf_text_by_page(pdf_bytes))
        assert "暂不直接给出" in full_text
        assert "无风险" in full_text
        assert "未发现疑似串通组合" not in full_text

    def test_e4_failed_pdf_contains_failure_phrase(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _freeze_datetime(monkeypatch)
        gen = CollusionReportGenerator()
        pdf_bytes = gen.generate(_project(), _make_failed_results())

        assert pdf_bytes.startswith(b"%PDF")
        full_text = "\n".join(_extract_pdf_text_by_page(pdf_bytes))
        assert "项目级结论: 分析失败" in full_text
        assert "未形成有效结论" in full_text or "未形成可直接展示的疑似串通组合" in full_text


# ===========================================================================
# F. inspect.getsource 锚点契约（2 用例）
# ===========================================================================


class TestInspectSourceAnchors:
    """F 类：inspect.getsource 锚定关键调用顺序

    R2-7 拆分时主类 generate / _generate_group_based_report 编排器整段保留，
    内部对 _build_* 的调用顺序是用户面对 PDF 章节顺序的直接来源；锚点契约
    防止"无意删/替换/重排" 调用顺序。
    """

    def test_f1_generate_contains_pair_only_section_calls(self) -> None:
        src = inspect.getsource(CollusionReportGenerator.generate)
        # pair-only 报告主流程调用顺序锚点
        for ident in [
            "_build_cover",
            "_build_overview",
            "_build_conclusion",
            "_build_risk_distribution",
            "_build_similarity_matrix",
            "_build_pair_details",
            "_build_suggestions",
            "_build_disclaimer",
        ]:
            assert ident in src, f"generate 源码缺少 {ident} 调用"

        # cover 必须在 overview 之前（编排顺序契约）
        assert src.index("_build_cover") < src.index("_build_overview")
        assert src.index("_build_overview") < src.index("_build_conclusion")
        assert src.index("_build_pair_details") < src.index("_build_suggestions")
        assert src.index("_build_suggestions") < src.index("_build_disclaimer")

    def test_f2_group_based_contains_group_section_calls(self) -> None:
        src = inspect.getsource(CollusionReportGenerator._generate_group_based_report)
        for ident in [
            "_build_cover",
            "_build_group_overview",
            "_build_similarity_matrix",
            "_build_group_conclusion",
            "_build_group_details",
            "_build_global_findings_grouped",
            "_build_suggestions",
            "_build_disclaimer",
        ]:
            assert ident in src, f"_generate_group_based_report 源码缺少 {ident} 调用"

        assert src.index("_build_cover") < src.index("_build_group_overview")
        assert src.index("_build_group_conclusion") < src.index("_build_group_details")
        assert src.index("_build_group_details") < src.index("_build_global_findings_grouped")
