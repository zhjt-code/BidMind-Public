"""PDF报告生成器

使用 ReportLab 生成 PDF 报告（纯 Python 实现，无需 GTK3）。
"""

import os
from datetime import datetime
from io import BytesIO
from typing import Optional
from xml.sax.saxutils import escape as xml_escape

from loguru import logger
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.bid_envelope import format_page_locations
from app.core.extractor.evidence_verdict import user_facing_response_text
from app.core.report.scoring_detail_section import build_scoring_detail_story


# 注册中文字体
def _register_chinese_font():
    """注册中文字体

    .ttc (TrueType Collection) 文件必须指定 subfontIndex，否则 reportlab
    无法正确解析字体表，导致中文字符渲染为乱码或方块。
    """
    font_candidates = [
        # (路径, subfontIndex)  -- .ttf 不需要 subfontIndex，传 0 即可
        # Windows
        (r"C:\Windows\Fonts\simhei.ttf", 0),
        (r"C:\Windows\Fonts\msyh.ttc", 0),
        (r"C:\Windows\Fonts\simsun.ttc", 0),
        # Linux (Docker: fonts-wqy-microhei)
        ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
    ]

    for font_path, sub_idx in font_candidates:
        if os.path.exists(font_path):
            try:
                pdfmetrics.registerFont(
                    TTFont('ChineseFont', font_path, subfontIndex=sub_idx)
                )
                logger.info(f"已注册中文字体: {font_path} (subfontIndex={sub_idx})")
                return True
            except Exception as e:
                logger.warning(f"注册字体失败 {font_path}: {e}")
                continue

    logger.warning("未找到中文字体，PDF可能显示乱码")
    return False


# 尝试注册中文字体
_CHINESE_FONT_AVAILABLE = _register_chinese_font()
_FONT_NAME = 'ChineseFont' if _CHINESE_FONT_AVAILABLE else 'Helvetica'


class ReportGenerator:
    """PDF报告生成器
    
    使用 ReportLab 生成 PDF 报告。
    
    示例:
        >>> generator = ReportGenerator()
        >>> pdf_bytes = generator.generate(project_name, summary, results)
        >>> with open("report.pdf", "wb") as f:
        >>>     f.write(pdf_bytes)
    """
    
    def __init__(self, output_dir: Optional[str] = None):
        """初始化报告生成器
        
        Args:
            output_dir: PDF输出目录（保留签名兼容，当前未使用）
        """
        # 初始化样式
        self.styles = getSampleStyleSheet()
        self._init_styles()
    
    def _init_styles(self):
        """初始化自定义样式"""
        # 标题样式
        self.styles.add(ParagraphStyle(
            name='ChineseTitle',
            fontName=_FONT_NAME,
            fontSize=18,
            leading=24,
            alignment=1,  # 居中
            spaceAfter=12,
        ))
        
        # 副标题样式
        self.styles.add(ParagraphStyle(
            name='ChineseHeading',
            fontName=_FONT_NAME,
            fontSize=14,
            leading=18,
            spaceBefore=12,
            spaceAfter=6,
        ))
        
        # 正文样式
        self.styles.add(ParagraphStyle(
            name='ChineseBody',
            fontName=_FONT_NAME,
            fontSize=10,
            leading=14,
        ))
        
        # 小字样式
        self.styles.add(ParagraphStyle(
            name='ChineseSmall',
            fontName=_FONT_NAME,
            fontSize=8,
            leading=10,
            textColor=colors.grey,
        ))
    
    def generate(
        self,
        project_name: str,
        summary: dict,
        results: list[dict],
        high_risk_items: Optional[list[dict]] = None,
        include_debug: bool = False,
        document_qa: Optional[dict] = None,
    ) -> bytes:
        """生成PDF报告
        
        Args:
            project_name: 项目名称
            summary: 审查汇总数据
            results: 审查结果列表
            high_risk_items: 高风险项列表
            
        Returns:
            bytes: PDF文件内容
        """
        buffer = BytesIO()
        
        # 创建文档
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=15*mm,
            leftMargin=15*mm,
            topMargin=15*mm,
            bottomMargin=15*mm,
        )
        
        # 构建内容
        story = []
        
        # 标题
        story.append(Paragraph(
            "投标文件审查报告",
            self.styles['ChineseTitle']
        ))
        story.append(Paragraph(
            self._to_safe_paragraph_text(f"项目: {project_name}"),
            self.styles['ChineseHeading']
        ))
        story.append(Paragraph(
            self._to_safe_paragraph_text(
                f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ),
            self.styles['ChineseSmall']
        ))
        story.append(Spacer(1, 12))
        
        # 汇总信息
        story.append(Paragraph("审查汇总", self.styles['ChineseHeading']))
        
        summary_data = [
            ["指标", "数值"],
            ["综合符合度", f"{summary.get('weighted_score', summary.get('average_score', 0)):.1f}"],
            ["平均分", f"{summary.get('average_score', 0):.1f}"],
            ["审查结论", self._get_conclusion_text(summary.get('conclusion', 'pending'))],
            ["结论原因", summary.get('conclusion_reason', '')],
            ["总项数", str(summary.get('total_count', 0))],
            ["符合项", str(summary.get('compliant_count', 0))],
            ["不符合项", str(summary.get('non_compliant_count', 0))],
            ["高风险项", str(summary.get('high_risk_count', 0))],
        ]
        
        summary_table = Table(summary_data, colWidths=[80, 400])
        summary_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), _FONT_NAME),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4A90D9')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F5F5F5')]),
            ('PADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(summary_table)
        story.append(Spacer(1, 12))

        # 评分项预估得分小节（P2 闭环，与结果页 ReviewStats 对齐）
        story.extend(self._build_scoring_summary(summary))
        
        # 高风险项（如果有）
        if high_risk_items:
            story.append(Paragraph("高风险项（否决项）", self.styles['ChineseHeading']))
            
            # 对高风险项进行排序
            sorted_high_risk = self._sort_results(high_risk_items)
            
            risk_data = [["分类", "招标要求", "审查意见"]]
            for item in sorted_high_risk:
                risk_data.append([
                    self._get_category_text(item.get('category', '')),
                    Paragraph(
                        self._to_safe_paragraph_text(
                            item.get('requirement_summary', '')
                        ),
                        self.styles['ChineseBody'],
                    ),
                    Paragraph(
                        self._to_business_comment_text(item.get('comment', '')),
                        self.styles['ChineseBody'],
                    ),
                ])
            
            risk_table = Table(risk_data, colWidths=[50, 200, 230])
            risk_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), _FONT_NAME),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#D9534F')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#FFEEEE'), colors.HexColor('#FFE5E5')]),
                ('PADDING', (0, 0), (-1, -1), 6),
            ]))
            story.append(risk_table)
            story.append(Spacer(1, 12))
        
        # 详细审查结果
        story.append(PageBreak())
        story.append(Paragraph("详细审查结果", self.styles['ChineseHeading']))
        
        # 对审查结果进行排序
        sorted_results = self._sort_results(results)
        
        # 分页显示详细结果
        for i, result in enumerate(sorted_results, 1):
            long_detail_flow = []
            result_data = [
                ["序号", str(i)],
                ["分类", self._get_category_text(result.get('category', ''))],
                ["招标要求", Paragraph(
                    self._to_safe_paragraph_text(
                        result.get('requirement_description')
                        or result.get('requirement_summary', '')
                    ),
                    self.styles['ChineseBody'],
                )],
                ["招标来源页码", str(
                    "、".join(str(p) for p in (result.get('requirement_source_pages') or []))
                    or (result.get('requirement_source_page') or '-')
                )],
                ["投标响应", Paragraph(
                    self._to_safe_paragraph_text(
                        user_facing_response_text(
                            content=result.get('response_content'),
                            summary=result.get('response_summary'),
                        )
                        or '未响应'
                    ),
                    self.styles['ChineseBody'],
                )],
                # 投标信封分卷时带卷名(如 "商务标 P3、P5；技术标 P12"), 整本回退原页码串
                ["来源页码", format_page_locations(
                    result.get('source_page_locations'),
                    fallback=result.get('source_pages'),
                )],
                ["符合程度", (
                    "待判定" if result.get("pending_judgment")
                    else self._get_compliance_text(result.get('compliance_level', ''))
                )],
                ["评分", str(result.get('score', 0))],
                ["风险等级", self._get_risk_text(result.get('risk_level', ''))],
                [
                    "审查意见",
                    Paragraph(
                        self._to_business_comment_text(result.get('comment', '')),
                        self.styles['ChineseBody'],
                    ),
                ],
            ]
            # 评分项预估得分行（T-01，与结果页评分项徽标对齐）
            if result.get("requirement_kind") == "scoring_item" or result.get("is_key_review"):
                est = result.get("estimated_score")
                mx = result.get("max_score")
                if est is not None and mx:
                    result_data.append(["评分项预估", f"{est} / 满分 {mx}"])
                elif mx:
                    result_data.append(["评分项满分", f"{mx}（预估待补充）"])
            # 招标要求子项（T-07）
            sub_reqs = result.get("requirement_sub_requirements") or []
            if sub_reqs:
                long_detail_flow.extend(
                    self._build_requirement_sub_requirement_flow(i, sub_reqs)
                )
            response_completeness = result.get("response_completeness")
            response_missing_elements = result.get("response_missing_elements")
            response_evidence_anchor = result.get("response_evidence_anchor")
            if response_completeness:
                result_data.append(["响应完整性", str(response_completeness)])
            if response_missing_elements:
                missing_text = "；".join(result.get("response_missing_elements") or [])
                result_data.append([
                    "缺失要素",
                    Paragraph(
                        self._to_safe_paragraph_text(missing_text or "-"),
                        self.styles["ChineseBody"],
                    ),
                ])
            if response_evidence_anchor:
                result_data.append(["证据锚点", str(response_evidence_anchor)])
            relevance_score = result.get('relevance_score')
            relevance_note = result.get('relevance_note')
            if relevance_score is not None and relevance_score < 50:
                label = "相关性风险"
                note_text = relevance_note or ""
                result_data.append([
                    label,
                    Paragraph(
                        self._to_safe_paragraph_text(
                            f"相关度: {relevance_score}/100  {note_text}"
                        ),
                        self.styles['ChineseBody'],
                    ),
                ])
            if include_debug:
                result_data[2:2] = [
                    ["事实组编号", str(result.get('fact_key') or result.get('canonical_key') or '-')],
                    ["证据族", self._to_family_type_text(str(result.get("family_type") or ""))],
                    ["主体范围", self._to_scope_text(str(result.get("subject_scope") or ""))],
                    ["证据关系", self._to_evidence_relation_text(str(result.get("evidence_relation") or ""))],
                    ["一致性标记", self._to_consistency_flag_text(str(result.get("consistency_flag") or ""))],
                    [
                        "收敛说明",
                        Paragraph(
                            self._to_safe_paragraph_text(
                                result.get('reconcile_note', '') or '-'
                            ),
                            self.styles['ChineseBody'],
                        ),
                    ],
                ]
            
            # 根据风险等级设置颜色
            risk_level = result.get('risk_level', 'none')
            if risk_level == 'high':
                header_color = colors.HexColor('#D9534F')
            elif risk_level == 'medium':
                header_color = colors.HexColor('#F0AD4E')
            else:
                header_color = colors.HexColor('#5CB85C')
            
            result_table = Table(result_data, colWidths=[80, 400])
            result_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), _FONT_NAME),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#F0F0F0')),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('PADDING', (0, 0), (-1, -1), 6),
                ('LINEABOVE', (0, 0), (-1, 0), 2, header_color),
            ]))
            story.append(result_table)
            if long_detail_flow:
                story.extend(long_detail_flow)
            story.append(Spacer(1, 8))
        
        # 评分项逐条评审章节（C3）
        story.extend(build_scoring_detail_story(sorted_results, _FONT_NAME, self.styles))

        # 文档质检独立章节(不参与响应审查评分与结论)
        from app.core.report.document_qa_section import build_document_qa_story

        story.extend(build_document_qa_story(
            document_qa, self.styles, safe_text=self._to_safe_paragraph_text,
        ))

        # 免责声明
        story.append(Spacer(1, 20))
        story.append(Table(
            [['']],
            colWidths=[480],
            style=TableStyle([('LINEABOVE', (0, 0), (-1, 0), 0.5, colors.grey)]),
        ))
        story.append(Spacer(1, 8))
        story.append(Paragraph(
            "本报告由 BidMind AI 系统自动生成，仅供参考，最终判定请以人工审核为准。",
            self.styles['ChineseSmall'],
        ))
        story.append(Paragraph(
            "报告中的风险评估基于文档内容的自动化分析，不构成法律意见。",
            self.styles['ChineseSmall'],
        ))

        # 生成PDF
        doc.build(story)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        
        logger.info(f"生成PDF报告: project={project_name}, size={len(pdf_bytes)} bytes")
        
        return pdf_bytes

    def _build_requirement_sub_requirement_flow(
        self, result_index: int, sub_requirements: object,
    ) -> list:
        """将招标子项渲染为可分页段落，避免 ReportLab 表格单行超高。"""
        if isinstance(sub_requirements, (str, bytes)):
            raw_items = [sub_requirements]
        else:
            try:
                raw_items = list(sub_requirements)  # type: ignore[arg-type]
            except TypeError:
                raw_items = [sub_requirements]

        items = [
            str(item).strip()
            for item in raw_items
            if item is not None and str(item).strip()
        ]
        if not items:
            return []

        flow = [
            Spacer(1, 2),
            Paragraph(f"第 {result_index} 条 - 招标子项", self.styles["ChineseSmall"]),
        ]
        for idx, item in enumerate(items, 1):
            flow.append(Paragraph(
                self._to_safe_paragraph_text(f"{idx}. {item}"),
                self.styles["ChineseBody"],
            ))
        return flow

    def _build_scoring_summary(self, summary: dict) -> list:
        """评分项预估得分小节（仅当存在评分项时渲染，与结果页 ReviewStats 一致）"""
        scoring_count = summary.get("scoring_item_count", 0) or 0
        if scoring_count <= 0:
            return []
        flow: list = [Paragraph("评分项预估得分（AI 预估，仅供参考）", self.styles['ChineseHeading'])]
        estimated_item_count = summary.get("estimated_item_count", 0) or 0
        if estimated_item_count <= 0:
            flow.append(Paragraph(
                self._to_safe_paragraph_text(
                    "本项目审查在评分项功能上线前完成，暂无预估得分，建议重新审查以获取评分项预估。"
                ),
                self.styles['ChineseBody'],
            ))
            flow.append(Spacer(1, 12))
            return flow
        # 多采购包文档各包独立百分制, 按包分行核计, 禁止跨包相加成"200分制"。
        by_lot = summary.get("estimated_score_by_lot", {}) or {}
        if by_lot:
            rows = self._scoring_rows_by_lot(by_lot)
        else:
            est_total = summary.get("estimated_total_score", 0.0) or 0.0
            max_total = summary.get("max_total_score", 0.0) or 0.0
            # 满分合计偏离100(多卷/加权/非100分制)时机械求和不可信,
            # 合计行不展示统一总分(用"—"), 与结果页 ReviewStats 降级一致。
            reliable = summary.get("scoring_total_reliable", True)
            total_est_cell = f"{est_total:.1f}" if reliable else "—"
            total_max_cell = f"{max_total:.1f}" if reliable else "—"
            rows = [["卷别", "预估得分", "满分", "评分项数"],
                    ["合计", total_est_cell, total_max_cell, str(scoring_count)]]
            by_volume = summary.get("estimated_score_by_volume", {}) or {}
            for volume, data in by_volume.items():
                rows.append([
                    str(volume),
                    f"{(data.get('estimated', 0.0) or 0.0):.1f}",
                    f"{(data.get('max', 0.0) or 0.0):.1f}",
                    str(data.get('count', 0) or 0),
                ])
        scoring_table = Table(rows, colWidths=[150, 110, 110, 110])
        scoring_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), _FONT_NAME),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4A90D9')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (0, 1), (0, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F5F5F5')]),
            ('PADDING', (0, 0), (-1, -1), 6),
        ]))
        flow.append(scoring_table)
        note_text = "注: 预估得分由 AI 依据投标响应推算，仅供评标参考，不替代专家评分。"
        if by_lot:
            note_text += "本项目按采购包分别评审，各包满分独立核计，不汇总跨包总分。"
            if any(d.get("unassigned") for d in by_lot.values()):
                note_text += "另有部分评分项未识别采购包归属，请人工核对其所属包。"
        elif not summary.get("scoring_total_reliable", True):
            note_text += (
                "本评分表为多卷/加权/非100分制，系统暂不汇总统一总分（合计以“—”表示），请按卷分项参考。"
            )
        flow.append(Paragraph(note_text, self.styles['ChineseSmall']))
        flow.append(Spacer(1, 12))
        return flow

    def _scoring_rows_by_lot(self, by_lot: dict) -> list:
        """按采购包组装评分表行：每包一行主计，包内按卷缩进明细。"""
        rows = [["采购包 / 卷别", "预估得分", "满分", "评分项数"]]
        for lot, data in by_lot.items():
            has_estimate = (data.get("estimated_count", 0) or 0) > 0
            est_cell = (
                f"{(data.get('estimated', 0.0) or 0.0):.1f}"
                if has_estimate and data.get("reliable", False)
                else "—"
            )
            rows.append([
                str(lot),
                est_cell,
                f"{(data.get('max', 0.0) or 0.0):.1f}",
                str(data.get("count", 0) or 0),
            ])
            for volume, vdata in (data.get("by_volume", {}) or {}).items():
                vol_est = f"{(vdata.get('estimated', 0.0) or 0.0):.1f}" if has_estimate else "—"
                rows.append([
                    f"  · {volume}",
                    vol_est,
                    f"{(vdata.get('max', 0.0) or 0.0):.1f}",
                    str(vdata.get("count", 0) or 0),
                ])
        return rows

    def _to_family_type_text(self, value: str) -> str:
        mapping = {
            "performance": "业绩",
            "audit_finance": "审计财务",
            "qualification": "资质",
            "generic": "通用",
            "": "通用",
        }
        return mapping.get(value, value or "通用")

    def _to_scope_text(self, value: str) -> str:
        mapping = {
            "enterprise": "企业",
            "personnel": "人员",
            "both": "企业+人员",
            "generic": "未识别",
            "": "未识别",
        }
        return mapping.get(value, value or "未识别")

    def _to_evidence_relation_text(self, value: str) -> str:
        mapping = {
            "shared": "共享",
            "independent": "独立",
            "undetermined": "待判定",
            "": "待判定",
        }
        return mapping.get(value, value or "待判定")

    def _to_consistency_flag_text(self, value: str) -> str:
        mapping = {
            "aligned": "已对齐",
            "needs_review": "待复核",
            "conflicted": "冲突",
            "unchecked": "未检查",
            "": "未检查",
        }
        return mapping.get(value, value or "未检查")

    def _to_business_comment_text(self, text: str) -> str:
        if not text:
            return ""
        normalized = str(text)
        replacements = {
            "Golden Rule": "规则",
            "needs_review": "待复核",
            "conflicted": "冲突",
            "aligned": "已对齐",
            "shared": "共享",
            "independent": "独立",
            "undetermined": "待判定",
        }
        for src, target in replacements.items():
            normalized = normalized.replace(src, target)
        return self._to_safe_paragraph_text(normalized)

    def _to_safe_paragraph_text(self, text: object) -> str:
        """将业务文本转成可安全传入 ReportLab Paragraph 的字符串。"""
        if text is None:
            return ""
        normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
        escaped = xml_escape(normalized)
        return escaped.replace("\n", "<br/>")
    
    def _get_conclusion_text(self, conclusion: str) -> str:
        """获取结论文本"""
        mapping = {
            "pass": "通过",
            "fail": "不通过",
            "pending": "待澄清"
        }
        return mapping.get(conclusion, "待处理")
    
    def _get_compliance_text(self, level: str) -> str:
        """获取符合程度文本"""
        mapping = {
            "fully": "完全符合",
            "partially": "部分符合",
            "not": "不符合",
            "missing": "未响应"
        }
        return mapping.get(level, level)
    
    def _get_risk_text(self, level: str) -> str:
        """获取风险等级文本"""
        mapping = {
            "high": "高",
            "medium": "中",
            "low": "低",
            "none": "无"
        }
        return mapping.get(level, level)
    
    def _get_category_text(self, category: str) -> str:
        """获取分类文本"""
        mapping = {
            "QUAL": "资质要求",
            "TECH": "技术要求",
            "COMM": "商务要求",
            "PERF": "业绩要求",
            "STAFF": "人员要求",
            "FORMAT": "格式要求",
            "qualification": "资质要求",
            "technical": "技术要求",
            "commercial": "商务要求",
            "performance": "业绩要求",
            "staffing": "人员要求",
            "format": "格式要求"
        }
        return mapping.get(category, category)
    
    def _sort_results(self, results: list[dict]) -> list[dict]:
        """对审查结果进行排序
        
        一级排序（符合程度）: 不符合 > 部分符合 > 完全符合 > 未响应
        二级排序（分类）: 资质 > 商务 > 技术 > 人员 > 格式 > 业绩
        
        Args:
            results: 审查结果列表
            
        Returns:
            list[dict]: 排序后的结果列表
        """
        # 一级排序：符合程度
        compliance_order = {
            "not": 1,
            "partially": 2,
            "fully": 3,
            "missing": 4,
        }
        
        # 二级排序：分类
        category_order = {
            # 枚举 name（大写）
            "QUAL": 1,
            "COMM": 2,
            "TECH": 3,
            "STAFF": 4,
            "FORMAT": 5,
            "PERF": 6,
            # 枚举 value（后端实际返回的格式）
            "qualification": 1,
            "commercial": 2,
            "technical": 3,
            "staffing": 4,
            "format": 5,
            "performance": 6,
        }
        
        def get_category_order(category: str) -> int:
            """获取分类排序权重"""
            return category_order.get(category, category_order.get(category.upper(), 99))
        
        def sort_key(item: dict):
            """排序键函数"""
            compliance = item.get('compliance_level', 'missing')
            category = item.get('category', '')
            return (
                compliance_order.get(compliance, 99),
                get_category_order(category)
            )
        
        return sorted(results, key=sort_key)


# 工厂函数
def get_report_generator(output_dir: Optional[str] = None) -> ReportGenerator:
    """获取报告生成器实例
    
    Args:
        output_dir: PDF输出目录（可选）
        
    Returns:
        ReportGenerator: 生成器实例
    """
    return ReportGenerator(output_dir=output_dir)
