from app.core.report.generator import ReportGenerator

import pytest

pytestmark = pytest.mark.scoring


def test_generate_allows_business_text_with_angle_brackets() -> None:
    generator = ReportGenerator()

    summary = {
        "weighted_score": 86.5,
        "average_score": 86.5,
        "conclusion": "pending",
        "conclusion_reason": "存在单井误差<12%的量化阈值描述，需人工复核。",
        "total_count": 1,
        "compliant_count": 0,
        "non_compliant_count": 1,
        "high_risk_count": 1,
    }
    results = [
        {
            "category": "technical",
            "requirement_summary": "要求满足构造误差<5米。",
            "requirement_description": "要求满足构造误差<5米，且饱和/露点压力误差<3%。",
            "response_summary": "已响应",
            "response_content": "投标文件承诺单井拟合误差<12%，全区压力场误差<5%。",
            "source_pages": "2-3",
            "compliance_level": "partially",
            "score": 72,
            "risk_level": "high",
            "comment": "发现关键量化指标存在偏差：单井拟合误差<12%，建议人工复核。",
            "response_missing_elements": ["饱和/露点压力误差<3%"],
            "relevance_score": 48,
            "relevance_note": "仅部分覆盖<核心阈值>",
            "reconcile_note": "证据锚点含<阈值>文本，已做转义。",
        }
    ]

    pdf_bytes = generator.generate(
        project_name="测试项目<456>",
        summary=summary,
        results=results,
        high_risk_items=results,
        include_debug=True,
    )

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_generate_allows_very_long_requirement_sub_requirements() -> None:
    """: 超长招标子项不能因单个 PDF 表格行过高导致报告生成失败。"""
    generator = ReportGenerator()

    summary = {
        "weighted_score": 60.0,
        "average_score": 60.0,
        "conclusion": "pending",
        "total_count": 1,
        "compliant_count": 0,
        "non_compliant_count": 1,
        "high_risk_count": 0,
    }
    sub_requirements = [
        (
            "投标人须逐项响应检测任务、设备配置、人员安排、进度计划、质量控制、安全措施、"
            "成果交付和验收标准，并在技术方案中提供对应证明材料与实施路径。"
        )
        * 12
        for _ in range(40)
    ]
    results = [
        {
            "category": "technical",
            "requirement_description": "投标人须完整响应技术服务要求。",
            "response_content": "投标文件已提供总体响应，细项需人工复核。",
            "source_pages": "31",
            "compliance_level": "partially",
            "score": 60,
            "risk_level": "medium",
            "comment": "招标子项较多，报告生成仍应成功。",
            "requirement_sub_requirements": sub_requirements,
        }
    ]

    pdf_bytes = generator.generate(
        project_name="超长招标子项项目",
        summary=summary,
        results=results,
    )

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_requirement_sub_requirement_flow_labels_result_index() -> None:
    """多条结果连续渲染时，招标子项段落标题应能关联原结果序号。"""
    generator = ReportGenerator()

    flow = generator._build_requirement_sub_requirement_flow(7, ["子项A"])

    assert flow[1].getPlainText() == "第 7 条 - 招标子项"


# ---------------------------------------------------------------------------
# TDD C3: 评分项逐条评审章节
# ---------------------------------------------------------------------------

def _para_text(story: list) -> str:
    """提取 story 中所有 Paragraph 的纯文本。"""
    return "\n".join(
        item.getPlainText() for item in story if hasattr(item, "getPlainText")
    )


def test_scoring_detail_story_empty_for_no_scoring_items() -> None:
    """无评分项时章节应返回空列表（不渲染多余章节）。"""
    from app.core.report.scoring_detail_section import build_scoring_detail_story

    generator = ReportGenerator()
    results = [{"requirement_kind": "normal", "comment": "一般要求，无需评分"}]
    story = build_scoring_detail_story(results, "Helvetica", generator.styles)
    assert story == []


def test_scoring_detail_story_filters_scoring_items() -> None:
    """只提取评分项全集: scoring_item, 或 is_key_review 且满分>0。"""
    from app.core.report.scoring_detail_section import build_scoring_detail_story

    generator = ReportGenerator()
    results = [
        {"requirement_kind": "normal", "comment": "普通条款"},
        {
            "requirement_kind": "scoring_item",
            "score_item_name": "技术方案",
            "max_score": 20.0,
        },
        {
            "requirement_kind": "normal",
            "max_score": 15.0,
            "comment": "普通带分条款不应进入评分章",
        },
    ]
    story = build_scoring_detail_story(results, "Helvetica", generator.styles)
    assert len(story) > 0
    text = _para_text(story)
    assert "评分项逐条评审" in text
    assert "普通带分条款不应进入评分章" not in text


def test_scoring_detail_story_excludes_normal_with_max_score_only() -> None:
    """: 普通条款仅有 max_score 时不进入 PDF 评分章。"""
    from app.core.report.scoring_detail_section import build_scoring_detail_story

    generator = ReportGenerator()
    results = [
        {
            "requirement_kind": "normal",
            "max_score": 15.0,
            "comment": "普通带分条款",
        },
    ]
    story = build_scoring_detail_story(results, "Helvetica", generator.styles)
    assert story == []


def test_scoring_detail_story_includes_key_review_with_max_score() -> None:
    """: 回填到 normal 主行的重点评审项进入评分章。"""
    from app.core.report.scoring_detail_section import build_scoring_detail_story

    generator = ReportGenerator()
    results = [
        {
            "requirement_kind": "normal",
            "is_key_review": True,
            "max_score": 10.0,
            "score_item_name": "重点评审回填项",
        },
    ]
    story = build_scoring_detail_story(results, "Helvetica", generator.styles)
    assert len(story) > 0
    assert "评分项逐条评审" in _para_text(story)


def test_scoring_detail_story_includes_scoring_item_with_zero_max() -> None:
    """scoring_item 即使满分为 0 仍进入评分章（谓词对 scoring_item 恒真）。"""
    from app.core.report.scoring_detail_section import build_scoring_detail_story

    generator = ReportGenerator()
    results = [
        {
            "requirement_kind": "scoring_item",
            "max_score": 0,
            "score_item_name": "零分评分项",
        },
    ]
    story = build_scoring_detail_story(results, "Helvetica", generator.styles)
    assert len(story) > 0
    assert "评分项逐条评审" in _para_text(story)


def test_scoring_detail_story_prefers_summary_over_pack_dump() -> None:
    """未确认命中不得把目录/扫描件原文写入评分项响应摘要。"""
    from app.core.report.scoring_detail_section import build_scoring_detail_story

    generator = ReportGenerator()
    results = [
        {
            "requirement_kind": "scoring_item",
            "score_item_name": "报价",
            "max_score": 10.0,
            "comment": "待复核",
            "response_content": "【检索已命中待确认】\n目 录\n一、投标函 ........................ 1",
            "response_summary": "未查询到报价信息",
        }
    ]
    text = _para_text(build_scoring_detail_story(results, "Helvetica", generator.styles))
    assert "未查询到报价信息" in text
    assert "投标函" not in text
    assert "目 录" not in text


def test_scoring_detail_story_fallback_comment_when_no_rule_assessments() -> None:
    """rule_assessments=None 时应以 comment 字段作为判定摘要兜底（老数据兼容）。"""
    from app.core.report.scoring_detail_section import build_scoring_detail_story

    generator = ReportGenerator()
    results = [
        {
            "requirement_kind": "scoring_item",
            "max_score": 10.0,
            "rule_assessments": None,
            "comment": "投标人未提供充分证明材料，扣2分。",
        }
    ]
    story = build_scoring_detail_story(results, "Helvetica", generator.styles)
    text = _para_text(story)
    assert "未提供充分证明材料" in text


def test_scoring_detail_story_rule_assessments_in_paragraphs() -> None:
    """ 遵守: 逐规则判定长文本必须出现在可分页 Paragraph 中，不在 Table 单元格字符串里。"""
    from app.core.report.scoring_detail_section import build_scoring_detail_story

    generator = ReportGenerator()
    reason_text = "投标人已充分响应所有评分要素，提供了完整的实施方案与证明材料"
    results = [
        {
            "requirement_kind": "scoring_item",
            "max_score": 30.0,
            "rule_assessments": [
                {
                    "rule_excerpt": "应提供详细实施方案",
                    "status": "met",
                    "points": 30.0,
                    "reason": reason_text,
                    "evidence_pages": [3, 5],
                }
            ],
        }
    ]
    story = build_scoring_detail_story(results, "Helvetica", generator.styles)
    # reason_text 必须出现在 Paragraph 中（可分页）
    text = _para_text(story)
    assert reason_text in text


def test_scoring_detail_story_groups_by_lot_volume() -> None:
    """按 lot/volume 分组，组标题应出现在输出中。"""
    from app.core.report.scoring_detail_section import build_scoring_detail_story

    generator = ReportGenerator()
    results = [
        {
            "requirement_kind": "scoring_item",
            "max_score": 10.0,
            "lot": "采购包1",
            "volume": "technical",
        },
        {
            "requirement_kind": "scoring_item",
            "max_score": 5.0,
            "lot": "采购包2",
            "volume": "price",
        },
    ]
    story = build_scoring_detail_story(results, "Helvetica", generator.styles)
    text = _para_text(story)
    assert "采购包1" in text
    assert "采购包2" in text
    assert "技术标" in text
    assert "价格" in text
    assert "technical" not in text


def test_generator_produce_pdf_with_scoring_detail_chapter() -> None:
    """generator.generate() 产出的 PDF 中评分项章节正常生成（集成测试）。"""
    generator = ReportGenerator()
    summary = {
        "weighted_score": 75.0,
        "average_score": 75.0,
        "conclusion": "pending",
        "total_count": 1,
        "compliant_count": 1,
        "non_compliant_count": 0,
        "high_risk_count": 0,
        "scoring_item_count": 1,
        "estimated_item_count": 1,
        "estimated_total_score": 18.0,
        "max_total_score": 20.0,
        "estimated_score_by_volume": {
            "技术标": {"estimated": 18.0, "max": 20.0, "count": 1}
        },
    }
    results = [
        {
            "requirement_kind": "scoring_item",
            "score_item_name": "技术方案",
            "max_score": 20.0,
            "estimated_score": 18.0,
            "rule_assessments": [
                {
                    "rule_excerpt": "应提供详细实施方案",
                    "status": "met",
                    "points": 18.0,
                    "reason": "投标人提供了完整方案文件",
                    "evidence_pages": [5],
                }
            ],
            "response_content": "投标人提供了完整的技术方案，涵盖所有要点。",
            "category": "technical",
            "compliance_level": "fully",
            "score": 90,
            "risk_level": "none",
            "comment": "完全符合要求",
        }
    ]
    pdf_bytes = generator.generate(
        project_name="评分项逐条评审测试项目",
        summary=summary,
        results=results,
    )
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_generate_survives_malformed_document_qa_payload() -> None:
    """质检章节数据异常只降级本章节, 整份审查报告照常生成(主链路不受质检拖累)。"""
    generator = ReportGenerator()
    summary = {
        "weighted_score": 80.0,
        "average_score": 80.0,
        "conclusion": "pending",
        "total_count": 1,
        "compliant_count": 1,
        "non_compliant_count": 0,
        "high_risk_count": 0,
    }
    results = [
        {
            "category": "technical",
            "requirement_summary": "提供实施方案",
            "response_summary": "已响应",
            "compliance_level": "fully",
            "score": 80,
            "risk_level": "none",
            "comment": "符合",
        }
    ]
    pdf_bytes = generator.generate(
        project_name="质检载荷异常测试项目",
        summary=summary,
        results=results,
        document_qa={"scan": "broken", "summary": ["x"], "findings": ["not-a-finding"]},
    )
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000
