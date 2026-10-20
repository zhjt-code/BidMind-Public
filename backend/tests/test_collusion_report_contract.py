from types import SimpleNamespace

from app.core.report.collusion_generator import CollusionReportGenerator
from app.schemas.collusion import CollusionResults, CoverageStats


def _extract_text(items) -> str:
    parts: list[str] = []
    for item in items:
        getter = getattr(item, "getPlainText", None)
        if callable(getter):
            parts.append(getter())
    return "\n".join(parts)


def test_report_conclusion_uses_project_decision_instead_of_missing_group() -> None:
    generator = CollusionReportGenerator()
    results = CollusionResults(
        project_id=1,
        overall_risk="inconclusive",
        project_decision="partial_review_needed",
        project_risk_level="none",
        decision_confidence="medium",
        review_required=True,
        decision_basis="pairs",
        decision_engine_version=2,
        overall_risk_basis="pairs",
        risk_distribution_basis="pairs",
        risk_consistency_status="aligned",
        coverage_stats=CoverageStats(
            total_pairs=2,
            analyzed_pairs=2,
            review_required_pairs=1,
            clear_pairs=1,
        ),
        suspected_groups=[],
        synthesis_summary="当前仍有 1 对文件待复核。",
        narrative_summary="当前已完成 1 对无异常核查，但仍有 1 对处于待复核状态，暂不直接给出“无风险”结论。",
        investigation_suggestions=["人工复核待补充分析的文件对"],
    )

    text = _extract_text(generator._build_conclusion(results))

    assert "未发现疑似串通组合" not in text
    assert "暂不直接给出“无风险”结论" in text


def test_group_report_allows_business_text_with_angle_brackets() -> None:
    generator = CollusionReportGenerator()
    project = SimpleNamespace(name="串标项目<测试>")
    results = CollusionResults(
        project_id=1,
        overall_risk="medium",
        project_decision="suspicious_found",
        project_risk_level="medium",
        decision_confidence="medium",
        review_required=True,
        decision_basis="groups",
        overall_risk_basis="groups",
        risk_distribution_basis="groups",
        risk_consistency_status="aligned",
        risk_consistency_note="风险口径说明含<阈值>文本。",
        has_tender_doc=True,
        coverage_stats=CoverageStats(
            total_pairs=1,
            analyzed_pairs=1,
            llm_analyzed_pairs=1,
            coverage_ratio=1.0,
        ),
        pair_results=[
            {
                "pair_id": "1:2",
                "doc_a_id": 1,
                "doc_b_id": 2,
                "doc_a_name": "文件A<样例>.pdf",
                "doc_b_name": "文件B<样例>.pdf",
                "overall_risk": "medium",
                "similarity_score": 0.56,
                "analysis_mode": "full",
                "findings": [
                    {
                        "level": "second",
                        "dimension": "D3",
                        "description": "技术参数存在<高度相似>现象。",
                        "detail": "多处表述出现<阈值>级别重合。",
                        "quote_a": "单井拟合误差<12%",
                        "page_a": "3",
                        "quote_b": "单井拟合误差<12%",
                        "page_b": "5",
                        "certainty": 0.72,
                    }
                ],
            }
        ],
        groups=[
            {
                "group_index": 0,
                "member_doc_ids": [1, 2],
                "member_names": ["文件A<样例>.pdf", "文件B<样例>.pdf"],
                "risk_level": "medium",
                "group_confidence": 0.68,
                "uncertain_edge_count": 1,
                "roles": [
                    {
                        "doc_id": 1,
                        "company_name": "甲方<公司>",
                        "role": "牵头方",
                        "role_evidence": "报价结构与技术方案中存在<统一模板>痕迹。",
                    }
                ],
                "collusion_strategy": "疑似采用<分工报价>策略。",
                "key_evidence_summary": "关键指标均出现<12%>一致性。",
                "investigation_focus": ["复核<关键页码>与盖章信息"],
                "related_pair_ids": ["1:2"],
                "network_description": "两份文件存在<引用无效>之外的直接关联。",
                "findings": [
                    {
                        "level": "first",
                        "dimension": "D1",
                        "description": "群组级证据描述含<标签>文本。",
                        "detail": "群组级分析含<未闭合风险>内容。",
                        "certainty": 0.84,
                        "evidences": [
                            {
                                "doc_name": "文件A<样例>.pdf",
                                "quote": "负责人身份证号尾号<重复>",
                                "location": "第2页",
                            }
                        ],
                        "involved_doc_names": ["文件A<样例>.pdf", "文件B<样例>.pdf"],
                    }
                ],
            }
        ],
        global_findings=[
            {
                "type": "personnel_overlap",
                "involved_doc_ids": [1, 2],
                "involved_names": ["文件A<样例>.pdf", "文件B<样例>.pdf"],
                "description": "全局特征发现含<重复联系人>文本。",
            }
        ],
        synthesis_summary="综合摘要含<5%>阈值。",
        narrative_summary="项目级结论含<3%>误差阈值，需要人工复核。",
        investigation_suggestions=["建议核查<报价清单>与联系人信息。"],
        data_quality_flags=["OCR 提取含<低置信度>片段。"],
        profile_quality_flags=["行业画像信号含<待确认>项。"],
    )

    pdf_bytes = generator.generate(project, results)

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000
