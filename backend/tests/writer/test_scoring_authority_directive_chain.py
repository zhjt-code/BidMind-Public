""" 防御测试: 权威评分项规则文本必须进入写作指令。

映射风险: 确定性评分表解析把规则文本放在 description 但不填 score_rules 时,
写标权威替换后章节生成只看到"评分项名称+满分", 丢失得分/扣分规则。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from app.core.extractor.scoring.scoring_table_parser import parse_scoring_table  # noqa: E402
from app.core.writer.composer.clause_directive import build_clause_directives  # noqa: E402
from app.core.writer.scoring_source import _to_scoring_clause  # noqa: E402

pytestmark = pytest.mark.scoring


DETAIL_TABLE_WITH_RULE = """[评分表 第12-12页]
| 序号 | 评分项目 | 评分标准 | 标准分（分） |
| --- | --- | --- | --- |
| 1 | 技术方案 | 方案覆盖全部技术指标得10分，缺一项扣2分。 | 10.0 |
"""


def test_authoritative_scoring_rules_reach_clause_directive() -> None:
    parsed = parse_scoring_table(DETAIL_TABLE_WITH_RULE, section_path="评标办法/技术评分表")

    assert parsed.confident is True
    assert len(parsed.items) == 1
    item = parsed.items[0]
    assert item.score_rules == ["方案覆盖全部技术指标得10分，缺一项扣2分。"]

    clause = _to_scoring_clause(
        project_id=1,
        document_id=2,
        item=item,
        sort_order=0,
        totals_needs_review=False,
    )
    directive = build_clause_directives([clause], max_chars=2000)

    assert "评分响应点" in directive
    assert "技术方案（满分10.0分）" in directive
    assert "得分要点：方案覆盖全部技术指标得10分，缺一项扣2分。" in directive
