"""PDF报告生成模块

使用 ReportLab 将审查结果生成 PDF 报告（纯 Python 实现）。
"""

from app.core.report.generator import (
    ReportGenerator,
    get_report_generator,
)

__all__ = ["ReportGenerator", "get_report_generator"]
