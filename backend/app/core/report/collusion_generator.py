"""串标审查 PDF 报告生成器

使用 ReportLab 生成，复用 generator.py 的字体注册基础设施。

报告结构（对标投标审查报告的专业度）：
  封面 → 分析概览 → 总体结论 → 风险分布 → 相似度矩阵
  → 各对详细分析（维度+证据） → 调查建议 → 声明
"""

import re
from io import BytesIO
from typing import Callable, Optional
from xml.sax.saxutils import escape as xml_escape

from loguru import logger
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.collusion.identity_display import replace_document_identity_tokens
from app.core.report.generator import _FONT_NAME
from app.models.project import Project
from app.schemas.collusion import CollusionPairResult, CollusionResults

# ---------------------------------------------------------------------------
# 常量 / 映射
# ---------------------------------------------------------------------------

_RISK_LABELS = {
    "high": "高风险",
    "medium": "中风险",
    "low": "低风险",
    "none": "无风险",
    "inconclusive": "无法判定",
}

# 风险等级排序权重（报告各章节按风险降序排列共用；未知等级由调用方 .get(x, 5) 兜底）
_RISK_ORDER = {"high": 0, "medium": 1, "low": 2, "none": 3, "inconclusive": 4}

_RISK_COLORS = {
    "high": colors.HexColor("#D9534F"),
    "medium": colors.HexColor("#F0AD4E"),
    "low": colors.HexColor("#5BC0DE"),
    "none": colors.HexColor("#5CB85C"),
    "inconclusive": colors.HexColor("#999999"),
}

_RISK_BG_LIGHT = {
    "high": colors.HexColor("#FFEEEE"),
    "medium": colors.HexColor("#FFF8E6"),
    "low": colors.HexColor("#EBF7FF"),
    "none": colors.HexColor("#EEFFEE"),
    "inconclusive": colors.HexColor("#F5F5F5"),
}

_EVIDENCE_LEVEL_LABELS = {
    "first": "一级(直接定性)",
    "second": "二级(强力佐证)",
    "third": "三级(辅助参考)",
    "unknown": "参考信息",
}

_EVIDENCE_LEVEL_COLORS = {
    "first": colors.HexColor("#D9534F"),
    "second": colors.HexColor("#F0AD4E"),
    "third": colors.HexColor("#5BC0DE"),
    "unknown": colors.HexColor("#999999"),
}

_MODE_LABELS = {
    "full": "完整分析(8维度LLM)",
    "simplified": "简化分析(4维度LLM)",
    "simplified_degraded": "简化分析(降级)",
    "algorithm_only": "算法对比",
    "grouped_parallel": "完整分析(分组并行)",
    "grouped_parallel_recovered": "完整分析(含补充分析)",
    "failed": "分析失败",
    "skipped": "待补充分析",
}

_DIM_NAMES = {
    "d1_personnel_overlap": ("D1", "人员信息重叠"),
    "d2_company_relation": ("D2", "企业实质关联"),
    "d3_text_similarity": ("D3", "技术方案文本雷同"),
    "d4_price_pattern": ("D4", "商务报价异常关联"),
    "d5_qualification_overlap": ("D5", "资质业绩雷同"),
    "d6_format_similarity": ("D6", "文档格式与结构"),
    "d7_abnormal_consistency": ("D7", "异常一致性"),
    "d8_metadata_similarity": ("D8", "文档元数据"),
}

_DIM_TIERS = {
    "d1_personnel_overlap": "I",
    "d2_company_relation": "I",
    "d3_text_similarity": "II",
    "d4_price_pattern": "II",
    "d5_qualification_overlap": "II",
    "d6_format_similarity": "III",
    "d7_abnormal_consistency": "III",
    "d8_metadata_similarity": "III",
}

_THEME_PRIMARY = colors.HexColor("#2D5986")
_THEME_ACCENT = colors.HexColor("#4A90D9")
_THEME_BG_HEADER = colors.HexColor("#2D5986")
_THEME_BG_LIGHT = colors.HexColor("#F5F8FC")
_THEME_BG_ALT = colors.HexColor("#EDF2F7")
_THEME_TEXT = colors.HexColor("#333333")
_THEME_TEXT_LIGHT = colors.HexColor("#666666")
_THEME_TEXT_MUTED = colors.HexColor("#999999")
_THEME_BORDER = colors.HexColor("#D0D5DD")

_PAGE_WIDTH = A4[0] - 30 * mm  # 可用内容宽度 (15mm 左右边距)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _build_pair_ref_replacer(pair_results: list[CollusionPairResult]):
    """构建 '对X' / pair_id / 文档身份编号 -> 文件名 替换函数（对齐结果页 buildPairRefReplacer）。"""

    def _risk_key(pair: CollusionPairResult) -> int:
        raw = pair.risk_level or pair.overall_risk or ""
        if hasattr(raw, "value"):
            raw = raw.value
        return _RISK_ORDER.get(str(raw).lower(), 5)

    def _label(pair: CollusionPairResult) -> str:
        name_a = re.sub(r"\.pdf$", "", pair.doc_a_name or "", flags=re.IGNORECASE)
        name_b = re.sub(r"\.pdf$", "", pair.doc_b_name or "", flags=re.IGNORECASE)
        return f"【{name_a}×{name_b}】"

    def _doc_name(pair: CollusionPairResult, side: str) -> str:
        raw = pair.doc_a_name if side == "a" else pair.doc_b_name
        return re.sub(r"\.pdf$", "", raw or "", flags=re.IGNORECASE)

    sorted_pairs = sorted(pair_results, key=_risk_key)
    doc_id_to_name: dict[int, str] = {}
    for pair in pair_results:
        if pair.doc_a_id is not None:
            doc_id_to_name[int(pair.doc_a_id)] = _doc_name(pair, "a")
        if pair.doc_b_id is not None:
            doc_id_to_name[int(pair.doc_b_id)] = _doc_name(pair, "b")

    def replace(text: str | None) -> str:
        if not text:
            return ""
        result = text
        for pair in pair_results:
            if not pair.pair_id:
                continue
            result = re.sub(re.escape(pair.pair_id), _label(pair), result)
        result = replace_document_identity_tokens(
            result,
            doc_id_to_name,
            pair_ordinal_count=len(sorted_pairs),
        )
        for idx, pair in enumerate(sorted_pairs):
            result = re.sub(rf"(?<!文件)对{idx + 1}(?=[^0-9]|$)", _label(pair), result)
        result = re.sub(r"(?<!文件)对(\d+)(?=[^0-9年月日份%]|$)", "（引用无效）", result)
        result = re.sub(r"（引用无效）([、，,]\s*（引用无效）)+", "（引用无效）", result)
        result = re.sub(r"[、，,]\s*（引用无效）", "", result)
        result = re.sub(r"（证据来源：\s*（引用无效）\s*）", "", result)
        return result.strip()

    return replace


def _replace_legacy_pair_aliases(text: str, doc_a_name: str, doc_b_name: str) -> str:
    """将遗留的文件A/B占位词替换为真实文件名（仅用于展示层兼容）"""
    result = text or ""
    result = re.sub(r"投标文件A|文件A|文档A|A方", doc_a_name, result)
    result = re.sub(r"投标文件B|文件B|文档B|B方", doc_b_name, result)
    return result


def _render_findings_by_level(
    grouped: dict[str, list],
    *,
    style_name: str,
    render_one: Callable[[int, object], list],
) -> list:
    """按证据级别输出「色条标题 + 逐条证据」（pair / group 两个主题共用）

    grouped 必须含 first/second/third/unknown 四个桶（分桶规则由调用方自持，
    两主题对 dict 型 finding 的取 level 方式不同）；render_one(idx, finding)
    返回单条证据的 flowable 列表。
    """
    items: list = []
    for level_key in ["first", "second", "third", "unknown"]:
        level_findings = grouped[level_key]
        if not level_findings:
            continue

        level_label = _EVIDENCE_LEVEL_LABELS.get(level_key, "参考信息")
        level_color = _EVIDENCE_LEVEL_COLORS.get(level_key, _THEME_TEXT_MUTED)

        # 级别标题色条
        lvl_data = [[Paragraph(
            f"{level_label} -- {len(level_findings)} 条",
            ParagraphStyle(style_name, fontName=_FONT_NAME, fontSize=9,
                           textColor=colors.white),
        )]]
        lvl_tbl = Table(lvl_data, colWidths=[_PAGE_WIDTH])
        lvl_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), level_color),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ]))
        items.append(lvl_tbl)

        for fi_idx, f in enumerate(level_findings, 1):
            items += render_one(fi_idx, f)

        items.append(Spacer(1, 2 * mm))

    return items


def _score_level(score: float) -> str:
    """将 0-1 分值转为文字描述"""
    if score >= 0.7:
        return "高"
    if score >= 0.4:
        return "中"
    if score > 0.05:
        return "低"
    return "-"


def _score_color(score: float) -> colors.Color:
    """将 0-1 分值转为颜色"""
    if score >= 0.7:
        return colors.HexColor("#D9534F")
    if score >= 0.4:
        return colors.HexColor("#F0AD4E")
    return _THEME_TEXT


# ---------------------------------------------------------------------------
# 报告生成器
# ---------------------------------------------------------------------------

class CollusionReportGenerator:
    """串标审查 PDF 报告生成器"""

    def __init__(self) -> None:
        self._init_styles()

    def _init_styles(self):
        """初始化所有样式，与投标审查报告保持一致的设计语言"""
        base = getSampleStyleSheet()
        fn = _FONT_NAME

        self.s_title = ParagraphStyle(
            "CTitle", parent=base["Title"],
            fontName=fn, fontSize=22, leading=28,
            alignment=TA_CENTER, spaceAfter=4,
            textColor=_THEME_PRIMARY,
        )
        self.s_subtitle = ParagraphStyle(
            "CSubtitle", parent=base["Normal"],
            fontName=fn, fontSize=12, leading=16,
            alignment=TA_CENTER, textColor=_THEME_TEXT_LIGHT,
        )
        self.s_meta = ParagraphStyle(
            "CMeta", parent=base["Normal"],
            fontName=fn, fontSize=9, leading=12,
            alignment=TA_CENTER, textColor=_THEME_TEXT_MUTED,
        )
        self.s_h1 = ParagraphStyle(
            "CH1", parent=base["Heading1"],
            fontName=fn, fontSize=14, leading=20,
            spaceBefore=14, spaceAfter=8,
            textColor=_THEME_PRIMARY,
            borderPadding=(0, 0, 0, 6),
        )
        self.s_h2 = ParagraphStyle(
            "CH2", parent=base["Heading2"],
            fontName=fn, fontSize=12, leading=16,
            spaceBefore=10, spaceAfter=6,
            textColor=_THEME_TEXT,
        )
        self.s_h3 = ParagraphStyle(
            "CH3", parent=base["Heading3"],
            fontName=fn, fontSize=10, leading=14,
            spaceBefore=6, spaceAfter=4,
            textColor=_THEME_TEXT,
        )
        self.s_body = ParagraphStyle(
            "CBody", parent=base["Normal"],
            fontName=fn, fontSize=10, leading=14,
            spaceAfter=4, textColor=_THEME_TEXT,
        )
        self.s_body_indent = ParagraphStyle(
            "CBodyIndent", parent=base["Normal"],
            fontName=fn, fontSize=9, leading=13,
            spaceAfter=3, leftIndent=12,
            textColor=_THEME_TEXT,
        )
        self.s_small = ParagraphStyle(
            "CSmall", parent=base["Normal"],
            fontName=fn, fontSize=8, leading=11,
            spaceAfter=2, textColor=_THEME_TEXT_LIGHT,
        )
        self.s_quote = ParagraphStyle(
            "CQuote", parent=base["Normal"],
            fontName=fn, fontSize=8, leading=11,
            leftIndent=16, rightIndent=8,
            textColor=colors.HexColor("#555555"),
            backColor=colors.HexColor("#F9F9F9"),
            borderPadding=(4, 4, 4, 4),
            spaceBefore=2, spaceAfter=4,
        )
        self.s_footer = ParagraphStyle(
            "CFooter", parent=base["Normal"],
            fontName=fn, fontSize=8, leading=10,
            alignment=TA_CENTER, textColor=_THEME_TEXT_MUTED,
        )

    def _to_safe_paragraph_text(self, text: object) -> str:
        """将业务文本安全转义后传给 ReportLab Paragraph。"""
        if text is None:
            return ""
        normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
        escaped = xml_escape(normalized)
        return escaped.replace("\n", "<br/>")

    # ====================================================================
    # 公共接口
    # ====================================================================

    def generate(self, project: Project, results: CollusionResults) -> bytes:
        """生成报告 PDF 字节流"""
        if results.groups:
            return self._generate_group_based_report(project, results)

        buf = BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=A4,
            rightMargin=15 * mm, leftMargin=15 * mm,
            topMargin=15 * mm, bottomMargin=15 * mm,
        )

        replace_refs = _build_pair_ref_replacer(results.pair_results or [])

        story: list = []
        story += self._build_cover(project, results, replace_refs)
        story.append(PageBreak())
        story += self._build_overview(results)
        story += self._build_conclusion(results, replace_refs)
        story += self._build_risk_distribution(results)
        story += self._build_similarity_matrix(results)
        story.append(PageBreak())
        story += self._build_pair_details(results, replace_refs)
        story += self._build_suggestions(results, replace_refs)
        story += self._build_disclaimer()

        try:
            doc.build(story)
        except Exception as e:
            logger.error(f"串标报告 PDF 构建失败: {e}", exc_info=True)
            raise

        return buf.getvalue()

    # ====================================================================
    # 群组报告
    # ====================================================================

    def _generate_group_based_report(self, project: Project, results_data: CollusionResults) -> bytes:
        """生成以嫌疑群组为主线的串标审查报告

        报告结构:
          封面 -> 分析概览 -> 相似度矩阵 -> 综合结论(含群组概览表)
          -> 各群组详细分析(角色+网络+策略+关联证据详情+调查建议)
          -> 全局特征发现 -> 调查建议汇总 -> 免责声明
        """
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4,
            rightMargin=15 * mm, leftMargin=15 * mm,
            topMargin=15 * mm, bottomMargin=15 * mm,
        )

        replace_refs = _build_pair_ref_replacer(results_data.pair_results or [])
        pair_map: dict[str, CollusionPairResult] = {
            p.pair_id: p for p in (results_data.pair_results or [])
        }

        story: list = []

        # 封面 (复用 pair 报告的封面渲染)
        story += self._build_cover(project, results_data, replace_refs)
        story.append(PageBreak())

        # 一、分析概览
        story += self._build_group_overview(results_data)

        # 二、可疑度矩阵
        matrix_items = self._build_similarity_matrix(results_data)
        if matrix_items:
            matrix_items[0] = Paragraph("二、可疑度矩阵", self.s_h1)
            story += matrix_items

        story.append(PageBreak())

        # 三、综合结论
        story += self._build_group_conclusion(results_data, replace_refs)

        # 四、嫌疑群组详细分析
        story += self._build_group_details(results_data, pair_map, replace_refs)

        # 五、全局特征发现
        story += self._build_global_findings_grouped(results_data)

        # 六、调查建议汇总
        sug_items = self._build_suggestions(results_data, replace_refs)
        if sug_items:
            sug_items[0] = Paragraph("六、调查建议汇总", self.s_h1)
            story += sug_items

        # 免责声明
        story += self._build_disclaimer()

        try:
            doc.build(story)
        except Exception as e:
            logger.error(f"群组串标报告 PDF 构建失败: {e}", exc_info=True)
            raise

        return buffer.getvalue()

    # ------------------------------------------------------------------
    # 群组报告 - 辅助方法
    # ------------------------------------------------------------------

    def _build_group_overview(self, results: CollusionResults) -> list:
        """thin forwarder -> collusion_generator_parts.group_section.build_group_overview (R2-7 Step 7)"""
        from app.core.report.collusion_generator_parts import group_section
        return group_section.build_group_overview(self, results)

    def _build_group_conclusion(self, results: CollusionResults,
                                replace_refs=None) -> list:
        """thin forwarder -> collusion_generator_parts.group_section.build_group_conclusion (R2-7 Step 7)"""
        from app.core.report.collusion_generator_parts import group_section
        return group_section.build_group_conclusion(self, results, replace_refs)

    def _build_group_details(self, results: CollusionResults,
                             pair_map: dict[str, CollusionPairResult],
                             replace_refs=None) -> list:
        """thin forwarder -> collusion_generator_parts.group_section.build_group_details (R2-7 Step 7)"""
        from app.core.report.collusion_generator_parts import group_section
        return group_section.build_group_details(self, results, pair_map, replace_refs)

    def _build_single_group(self, group, pair_map, replace_refs=None) -> list:
        """thin forwarder -> collusion_generator_parts.group_section.build_single_group (R2-7 Step 7)"""
        from app.core.report.collusion_generator_parts import group_section
        return group_section.build_single_group(self, group, pair_map, replace_refs)

    def _build_group_findings_section(
        self, grp_num: int, findings: list, replace_refs=None,
    ) -> list:
        """thin forwarder -> collusion_generator_parts.group_section.build_group_findings_section (R2-7 Step 7)"""
        from app.core.report.collusion_generator_parts import group_section
        return group_section.build_group_findings_section(self, grp_num, findings, replace_refs)

    def _build_single_group_finding(
        self, idx: int, finding, replace_refs=None,
    ) -> list:
        """thin forwarder -> collusion_generator_parts.group_section.build_single_group_finding (R2-7 Step 7)"""
        from app.core.report.collusion_generator_parts import group_section
        return group_section.build_single_group_finding(self, idx, finding, replace_refs)

    def _build_global_findings_grouped(self, results: CollusionResults) -> list:
        """thin forwarder -> collusion_generator_parts.global_findings.build_global_findings_grouped (R2-7 Step 4)"""
        from app.core.report.collusion_generator_parts import global_findings
        return global_findings.build_global_findings_grouped(self, results)

    # ====================================================================
    # 封面
    # ====================================================================

    def _build_cover(self, project: Project, results: CollusionResults,
                     replace_refs=None) -> list:
        """thin forwarder -> collusion_generator_parts.cover_builder.build_cover (R2-7 Step 3)"""
        from app.core.report.collusion_generator_parts import cover_builder
        return cover_builder.build_cover(self, project, results, replace_refs)

    # ====================================================================
    # 分析概览
    # ====================================================================

    def _build_overview(self, results: CollusionResults) -> list:
        """thin forwarder -> collusion_generator_parts.conclusion_summary.build_overview (R2-7 Step 5)"""
        from app.core.report.collusion_generator_parts import conclusion_summary
        return conclusion_summary.build_overview(self, results)

    # ====================================================================
    # 总体审查结论
    # ====================================================================

    def _build_conclusion(self, results: CollusionResults, replace_refs=None) -> list:
        """thin forwarder -> collusion_generator_parts.conclusion_summary.build_conclusion (R2-7 Step 5)"""
        from app.core.report.collusion_generator_parts import conclusion_summary
        return conclusion_summary.build_conclusion(self, results, replace_refs)

    # ====================================================================
    # 风险分布
    # ====================================================================

    def _build_risk_distribution(self, results: CollusionResults) -> list:
        """thin forwarder -> collusion_generator_parts.conclusion_summary.build_risk_distribution (R2-7 Step 5)"""
        from app.core.report.collusion_generator_parts import conclusion_summary
        return conclusion_summary.build_risk_distribution(self, results)

    # ====================================================================
    # 可疑度矩阵
    # ====================================================================

    def _build_similarity_matrix(self, results: CollusionResults) -> list:
        """thin forwarder -> collusion_generator_parts.conclusion_summary.build_similarity_matrix (R2-7 Step 5)"""
        from app.core.report.collusion_generator_parts import conclusion_summary
        return conclusion_summary.build_similarity_matrix(self, results)

    # ====================================================================
    # 各对详细分析
    # ====================================================================

    def _build_pair_details(self, results: CollusionResults, replace_refs=None) -> list:
        """thin forwarder -> collusion_generator_parts.pair_section.build_pair_details (R2-7 Step 6)"""
        from app.core.report.collusion_generator_parts import pair_section
        return pair_section.build_pair_details(self, results, replace_refs)

    def _build_single_pair(self, idx: int, pair: CollusionPairResult,
                           replace_refs=None, section_prefix: str = "5.") -> list:
        """thin forwarder -> collusion_generator_parts.pair_section.build_single_pair (R2-7 Step 6)"""
        from app.core.report.collusion_generator_parts import pair_section
        return pair_section.build_single_pair(self, idx, pair, replace_refs, section_prefix)

    def _build_dimension_table(self, pair: CollusionPairResult) -> list:
        """thin forwarder -> collusion_generator_parts.pair_section.build_dimension_table (R2-7 Step 6)"""
        from app.core.report.collusion_generator_parts import pair_section
        return pair_section.build_dimension_table(self, pair)

    def _build_attention_section(self, pair: CollusionPairResult) -> list:
        """thin forwarder -> collusion_generator_parts.pair_section.build_attention_section (T-03)"""
        from app.core.report.collusion_generator_parts import pair_section
        return pair_section.build_attention_section(self, pair)

    def _build_evidence_chunks_section(self, pair: CollusionPairResult) -> list:
        """thin forwarder -> collusion_generator_parts.pair_section.build_evidence_chunks_section (T-05)"""
        from app.core.report.collusion_generator_parts import pair_section
        return pair_section.build_evidence_chunks_section(self, pair)

    def _build_findings(
        self,
        pair: CollusionPairResult,
        replace_refs=None,
        doc_a_id: int | None = None,
        doc_b_id: int | None = None,
        doc_a_name: str = "",
        doc_b_name: str = "",
    ) -> list:
        """thin forwarder -> collusion_generator_parts.pair_section.build_findings (R2-7 Step 6)"""
        from app.core.report.collusion_generator_parts import pair_section
        return pair_section.build_findings(
            self, pair, replace_refs,
            doc_a_id=doc_a_id, doc_b_id=doc_b_id,
            doc_a_name=doc_a_name, doc_b_name=doc_b_name,
        )

    def _build_single_finding(
        self,
        idx: int,
        finding,
        replace_refs=None,
        doc_a_id: int | None = None,
        doc_b_id: int | None = None,
        doc_a_name: str = "文档1",
        doc_b_name: str = "文档2",
    ) -> list:
        """thin forwarder -> collusion_generator_parts.pair_section.build_single_finding (R2-7 Step 6)"""
        from app.core.report.collusion_generator_parts import pair_section
        return pair_section.build_single_finding(
            self, idx, finding, replace_refs,
            doc_a_id=doc_a_id, doc_b_id=doc_b_id,
            doc_a_name=doc_a_name, doc_b_name=doc_b_name,
        )

    # ====================================================================
    # 调查建议
    # ====================================================================

    def _build_suggestions(self, results: CollusionResults, replace_refs=None) -> list:
        """thin forwarder -> collusion_generator_parts.conclusion_summary.build_suggestions (R2-7 Step 5)"""
        from app.core.report.collusion_generator_parts import conclusion_summary
        return conclusion_summary.build_suggestions(self, results, replace_refs)

    # ====================================================================
    # 声明
    # ====================================================================

    def _build_disclaimer(self) -> list:
        """thin forwarder -> collusion_generator_parts.conclusion_summary.build_disclaimer (R2-7 Step 5)"""
        from app.core.report.collusion_generator_parts import conclusion_summary
        return conclusion_summary.build_disclaimer(self)


# ===========================================================================
# 单例工厂
# ===========================================================================

_generator_instance: Optional[CollusionReportGenerator] = None


def get_collusion_report_generator() -> CollusionReportGenerator:
    """获取串标报告生成器单例"""
    global _generator_instance
    if _generator_instance is None:
        _generator_instance = CollusionReportGenerator()
    return _generator_instance
