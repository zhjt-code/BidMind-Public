""" 防御测试: PDF 水印字符污染分值单元格的容忍解析。

映射真实风险(项目1584/评分测试17): 页面水印"日照市公共资源交易…"字符穿透进
分值单元格形成 `20分 照` / `日 18分`, 严格短分值正则整格失配, 该评分行被
静默丢弃, 漏提 38 分。真实页级验收见 test_scoring_assembler_golden 的
test_project1584_*; 本文件锁定容忍解析的单元语义与"不猜数字"红线。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.scoring.score_column_semantics import (  # noqa: E402
    parse_short_score_tolerant,
)
from app.core.extractor.scoring.scoring_table_parser import (  # noqa: E402
    parse_scoring_table,
)

import pytest

pytestmark = pytest.mark.scoring

# 精简自项目1584 chunk 158738(水印字已按真实位置保留)
WATERMARK_POLLUTED_TABLE = """[评分表 第33-34页]
| 评审项目 |  | 分值 | 评审要素 |
| --- | --- | --- | --- |
| 商 务 部 分 34 分 | 报价 | 30分 | 满足磋商文件要求且最终价格最低的报价为基准价。报 价得分＝（基准价／报价）×30。 |
|  | 类似项目业绩 | 4分 | 每提供 1 份得 2分，最高得 4分。 |
| 技 术 部 分 66 分 | 施工方案与技 术措施 | 20分 照 | 方案科学、合理、可行，本项最高得 20 分，每出现一处存在瑕疵 或不完善扣1分，扣完为止。 |
|  | 质量管理体系 与措施 | 日 18分 | 有完整的质量管理体系。本项最高得 18 分，每出现一处存在瑕疵或不完善扣2 分， 扣完为止。 |
|  | 安全管理体系 与措施 | 15分 | 有完整的机构和安全生产保障体系。本项 最高得15分。 |
|  | 环境保护管理 体系与措施 | 5分 | 环境保护机构健全、措施合理的得5分。 |
|  | 工程进度计划 与措施 | 8分 | 计划编排合理、可行的得 8分。 |
"""


def test_tolerant_recovers_watermark_polluted_cells():
    """水印噪声字剥离: 噪声在前/在后都要恢复正确分值。"""
    assert parse_short_score_tolerant("20分 照") == 20.0
    assert parse_short_score_tolerant("日 18分") == 18.0
    assert parse_short_score_tolerant("日 照 18分") == 18.0
    # 干净格保持原语义
    assert parse_short_score_tolerant("30分") == 30.0
    assert parse_short_score_tolerant("满分 30分") == 30.0


def test_tolerant_keeps_no_guess_red_line():
    """ 红线: 评分规则片段/歧义格禁止猜数字。"""
    # 评分/数量语义字 token 拒绝
    assert parse_short_score_tolerant("低于 60分") is None
    assert parse_short_score_tolerant("最高 20分") is None
    assert parse_short_score_tolerant("扣 3分") is None
    assert parse_short_score_tolerant("得 5分") is None
    assert parse_short_score_tolerant("第 5 分") is None
    # 两个分值 token 歧义拒绝
    assert parse_short_score_tolerant("30分 40分") is None
    # 长文本 token(规则正文断字)拒绝
    assert parse_short_score_tolerant("每出现一处存在瑕疵 或不完善扣2分") is None
    assert parse_short_score_tolerant("本项最高得 15分") is None
    # 既有断字重组语义不变: 数字内部断空格在 normalize 后仍是合法短分值
    assert parse_short_score_tolerant("1 0分") == 10.0
    # 单 token 失配不进入容忍路径
    assert parse_short_score_tolerant("低于60分") is None
    assert parse_short_score_tolerant("") is None


def test_watermark_polluted_table_extracts_all_seven_items():
    """整表解析: 污染行(20/18分)与干净行须全部产出, 合计100。"""
    result = parse_scoring_table(WATERMARK_POLLUTED_TABLE, section_path="评分相关章节")

    assert result.confident is True
    by_name = {it.score_item_name.replace(" ", ""): it for it in result.items}
    assert by_name["施工方案与技术措施"].score_weight == 20.0
    assert by_name["质量管理体系与措施"].score_weight == 18.0
    assert by_name["报价"].score_weight == 30.0
    assert by_name["类似项目业绩"].score_weight == 4.0
    assert by_name["安全管理体系与措施"].score_weight == 15.0
    assert by_name["环境保护管理体系与措施"].score_weight == 5.0
    assert by_name["工程进度计划与措施"].score_weight == 8.0
    assert len(result.items) == 7
    total = sum(it.score_weight for it in result.items)
    assert total == 100.0
