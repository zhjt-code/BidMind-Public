"""读取服务纯函数: 现势汇总计数 + 检查器状态合并(最劣者胜) + 图片页数"""

from app.services.document_qa.read_service import (
    _merge_checker_status,
    _summarize,
    image_page_count,
)


class _RunStub:
    def __init__(self, checker_status):
        self.checker_status = checker_status


def test_summarize_counts_dispositions():
    findings = [
        {"category": "toc", "severity": "P1", "certainty": "probable", "disposition": "pending"},
        {"category": "toc", "severity": "P2", "certainty": "possible", "disposition": "confirmed"},
        {"category": "hygiene", "severity": "P2", "certainty": "certain", "disposition": "false_positive"},
    ]
    summary = _summarize(findings)
    assert summary["total"] == 3
    assert summary["by_category"] == {"toc": 2, "hygiene": 1}
    assert summary["by_disposition"] == {
        "pending": 1, "confirmed": 1, "false_positive": 1,
    }


def test_merge_checker_status_worst_wins():
    merged = _merge_checker_status([
        _RunStub({"toc": "ok", "language": "ok"}),
        _RunStub({"toc": "failed", "language": "ok", "layout": "deferred"}),
    ])
    by_code = {item["code"]: item["status"] for item in merged}
    assert by_code["toc"] == "failed"
    assert by_code["language"] == "ok"
    assert by_code["layout"] == "deferred"


def test_image_page_count_reads_run_features():
    assert image_page_count({"image_pages": [6, 9, 14]}) == 3
    assert image_page_count({"image_pages": []}) == 0
    # 旧 run 没有该字段、失败 run 没有特征: 前端不显示说明
    assert image_page_count({"page_count": 74}) is None
    assert image_page_count(None) is None
