"""文档门禁校验模块

在解析前对上传的招标/投标PDF做轻量要素校验，识别"随手上传的非标书文档"，
给出非阻断式提示。核心信号: 目录结构 + 标书要素词表；另含可用性预检。
"""

from app.core.parser.doc_gate.contracts import DocGateResult, GateVerdict
from app.core.parser.doc_gate.sampler import GateSample, sample_pdf_bytes
from app.core.parser.doc_gate.gate_engine import evaluate_doc_gate
from app.core.parser.doc_gate.usability import (
    UsabilityAssessment,
    assess_pdf_usability,
    build_usability_gate_result,
)

__all__ = [
    "DocGateResult",
    "GateVerdict",
    "GateSample",
    "sample_pdf_bytes",
    "evaluate_doc_gate",
    "UsabilityAssessment",
    "assess_pdf_usability",
    "build_usability_gate_result",
]