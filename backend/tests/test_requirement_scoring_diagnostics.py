"""评分解析诊断在确认页核计报告中的透传测试。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.services.requirement_service import _build_scoring_totals_report  # noqa: E402

pytestmark = pytest.mark.scoring


def test_parse_diagnostics_from_scoring_metadata_reach_totals_report():
    """落库评分项携带的 parse_diagnostics 应在核计异常时出现在确认页横幅诊断中。"""
    diagnostic = "疑似评分行分值未识别: 施工方案与技术措施 | 分值格=最高 20分"
    requirements = [
        SimpleNamespace(
            requirement_kind="scoring_item",
            is_key_review=1,
            score_weight=80.0,
            source_page=33,
            description="安全管理体系与措施",
            scoring_metadata={
                "volume": "technical",
                "score_rules": ["本项最高得15分"],
                "parse_diagnostics": [diagnostic, diagnostic],
            },
        )
    ]

    payload = _build_scoring_totals_report(requirements)

    assert payload is not None
    assert payload["needs_review"] is True
    assert payload["diagnostics"].count(diagnostic) == 1
