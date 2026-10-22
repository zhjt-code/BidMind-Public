"""报告章节: 有发现/空载荷两态均可生成; 分卷页码带卷名; 载荷异常只降级本章节"""

from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph

from app.core.report.document_qa_section import (
    _FALLBACK_TEXT,
    _format_evidence_pages,
    build_document_qa_story,
)


def _styles():
    sample = getSampleStyleSheet()
    return {
        "ChineseHeading": sample["Heading2"],
        "ChineseBody": sample["BodyText"],
        "ChineseSmall": sample["BodyText"],
    }


def test_story_with_findings():
    payload = {
        "scan": {"status": "completed"},
        "summary": {
            "total": 1,
            "by_severity": {"P1": 1},
            "by_disposition": {"pending": 1},
        },
        "findings": [{
            "category": "numbering",
            "severity": "P1",
            "certainty": "certain",
            "title": "附件编号不连续",
            "message": "缺少附件3",
            "disposition": "pending",
            "evidence": {"items": [
                {"page": 5, "bid_part": "commercial"},
                {"page": 12, "bid_part": "technical"},
            ]},
        }],
    }
    story = build_document_qa_story(payload, _styles())
    assert len(story) > 3


def test_story_without_payload():
    story = build_document_qa_story(None, _styles())
    assert len(story) >= 3  # PageBreak + 标题 + 未执行说明


def test_evidence_pages_grouped_by_part():
    text = _format_evidence_pages({"items": [
        {"page": 3, "bid_part": "commercial"},
        {"page": 5, "bid_part": "commercial"},
        {"page": 12, "bid_part": "technical"},
    ]})
    assert text == "商务标 P3、P5；技术标 P12"


def test_evidence_pages_integral_no_part_label():
    text = _format_evidence_pages({"items": [{"page": 3, "bid_part": None}]})
    assert text == "P3"


def _texts(story) -> list[str]:
    return [item.getPlainText() for item in story if isinstance(item, Paragraph)]


def test_malformed_findings_degrade_to_notice():
    """证据是列表、发现项混入非字典: 章节只剩兜底说明, 不抛给报告生成。"""
    payload = {
        "scan": {"status": "completed"},
        "summary": {"total": 2, "by_severity": {"P2": 2}, "by_disposition": {"pending": 2}},
        "findings": [
            {"category": "toc", "severity": "P2", "title": "t", "message": "m", "evidence": ["bad"]},
            "not-a-finding",
        ],
    }
    texts = _texts(build_document_qa_story(payload, _styles()))
    assert texts[0] == "文档质检"
    assert texts[-1] == _FALLBACK_TEXT
    assert not any("m（位置" in text for text in texts)


def test_malformed_summary_and_scan_degrade_to_notice():
    """汇总是列表、扫描状态是字符串(第二种畸形): 同一兜底, 不输出半截汇总表。"""
    payload = {"scan": "broken", "summary": ["x"], "findings": []}
    story = build_document_qa_story(payload, _styles())
    texts = _texts(story)
    assert texts[-1] == _FALLBACK_TEXT
    assert len(story) == 5  # PageBreak + 标题 + 说明 + Spacer + 兜底说明
