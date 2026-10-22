""" 防御测试: is_total_row 精确化——长规则正文中的合计子串不命中。

映射真实风险:
- 真实评分行 ['前10项有效业绩的水表数量合计情况', '6分'] 被 is_total_row 误判为合计汇总行,
 导致 _column_has_short_score 排除该行、分值列双向验证失败、评分行静默丢失。

RED 失败预期:
- test_bug798_public_is_total_row_rejects_long_cell: 业务断言失败（当前返回 True）
- test_bug798_strict_policy_*: ImportError（score_total_row_policy 尚不存在）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.extractor.scoring.score_column_semantics import is_total_row  # noqa: E402

pytestmark = pytest.mark.scoring


# ---------------------------------------------------------------------------
# 业务断言 RED（公共接口，模块已存在）
# ---------------------------------------------------------------------------


def test_bug798_public_is_total_row_rejects_long_cell():
    """ 核心: 公共接口不得把长规则正文"合计"子串命中为合计行。

 RED: 当前实现 row_text 全串匹配 → True，违反此断言。
 GREEN: 委托 score_total_row_policy 后 → False。
 """
    assert is_total_row(["前10项有效业绩的水表数量合计情况", "6分"]) is False


def test_public_api_standalone_合计_still_true():
    """回归: 独立短格"合计"必须继续命中（既有行为保留）。"""
    assert is_total_row(["合计", "100"]) is True


def test_public_api_标准分合计_still_true():
    """回归: test_scoring_table_parser.py 中的 is_total_row 断言不得回归。"""
    assert is_total_row(["标准分合计", "", "", "100.0"]) is True


def test_public_api_得分小计_still_true():
    assert is_total_row(["得分小计", "50"]) is True


# ---------------------------------------------------------------------------
# 新模块测试（RED: ImportError，GREEN 后逐一通过）
# ---------------------------------------------------------------------------


def test_bug798_strict_policy_long_cell_false():
    """score_total_row_policy.is_total_row_strict 精确判定: 长规则正文不命中。"""
    from app.core.extractor.scoring.score_total_row_policy import (  # type: ignore[import]
        is_total_row_strict,
    )

    assert is_total_row_strict(["前10项有效业绩的水表数量合计情况", "6分"]) is False


def test_strict_standalone_合计_true():
    from app.core.extractor.scoring.score_total_row_policy import (  # type: ignore[import]
        is_total_row_strict,
    )

    assert is_total_row_strict(["合计", "100"]) is True


def test_strict_得分小计_true():
    from app.core.extractor.scoring.score_total_row_policy import (  # type: ignore[import]
        is_total_row_strict,
    )

    assert is_total_row_strict(["得分小计", "50"]) is True


def test_strict_平均分_true():
    from app.core.extractor.scoring.score_total_row_policy import (  # type: ignore[import]
        is_total_row_strict,
    )

    assert is_total_row_strict(["平均分", "85"]) is True


def test_strict_加权得分_true():
    from app.core.extractor.scoring.score_total_row_policy import (  # type: ignore[import]
        is_total_row_strict,
    )

    assert is_total_row_strict(["加权得分", "90"]) is True


def test_strict_最终得分_true():
    from app.core.extractor.scoring.score_total_row_policy import (  # type: ignore[import]
        is_total_row_strict,
    )

    assert is_total_row_strict(["最终得分", "95"]) is True


def test_strict_标准分合计_short_row_true():
    """既有测试场景: 短格"标准分合计"仍命中。"""
    from app.core.extractor.scoring.score_total_row_policy import (  # type: ignore[import]
        is_total_row_strict,
    )

    assert is_total_row_strict(["标准分合计", "", "", "100.0"]) is True


def test_strict_technical_合计_short_cell_true():
    """技术评分合计 (6字, ≤ 阈值) 仍命中。"""
    from app.core.extractor.scoring.score_total_row_policy import (  # type: ignore[import]
        is_total_row_strict,
    )

    assert is_total_row_strict(["技术评分合计", "60"]) is True


def test_strict_long_text_with_得分小计_false():
    """长单元格中包含"得分小计"子串不命中。"""
    from app.core.extractor.scoring.score_total_row_policy import (  # type: ignore[import]
        is_total_row_strict,
    )

    long_cell = "前5项技术得分小计情况及权重分配说明"
    assert is_total_row_strict([long_cell, "30"]) is False


# ---------------------------------------------------------------------------
# C1 短反例: 中等长度格含合计词但非汇总行（endswith 判据）
# ---------------------------------------------------------------------------


def test_C1_数量合计情况_false():
    """C1 核心: '数量合计情况' 含'合计'子串但结尾非合计词，必须 False。

    RED: 当前 in-contains 判据返回 True（'合计' in '数量合计情况'）。
    GREEN: endswith 判据返回 False（不以'合计'结尾）。
    """
    from app.core.extractor.scoring.score_total_row_policy import (  # type: ignore[import]
        is_total_row_strict,
    )

    assert is_total_row_strict(["数量合计情况", "6"]) is False


def test_C1_某得分小计情况_false():
    """C1 核心: '某得分小计情况' 含'得分小计'子串但结尾非合计词，必须 False。

    RED: in-contains 判据返回 True; GREEN: endswith 返回 False。
    """
    from app.core.extractor.scoring.score_total_row_policy import (  # type: ignore[import]
        is_total_row_strict,
    )

    assert is_total_row_strict(["某得分小计情况", "30"]) is False


def test_C1_public_api_数量合计情况_false():
    """C1: 公共接口 is_total_row 同步覆盖短反例。"""
    assert is_total_row(["数量合计情况", "6"]) is False


def test_C1_endswith_技术评分合计_still_true():
    """C1 回归: '技术评分合计' endswith '合计' 仍命中。"""
    from app.core.extractor.scoring.score_total_row_policy import (  # type: ignore[import]
        is_total_row_strict,
    )

    assert is_total_row_strict(["技术评分合计", "60"]) is True


def test_C1_TOTAL_ROW_TERMS_re_exported_from_semantics():
    """Minor: score_column_semantics 应从 score_total_row_policy 导入 TOTAL_ROW_TERMS, 避免双定义漂移。"""
    from app.core.extractor.scoring.score_column_semantics import TOTAL_ROW_TERMS as sem_terms
    from app.core.extractor.scoring.score_total_row_policy import TOTAL_ROW_TERMS as pol_terms

    assert sem_terms is pol_terms, (
        "score_column_semantics.TOTAL_ROW_TERMS 必须是 score_total_row_policy.TOTAL_ROW_TERMS 的同一对象（导入而非复制）"
    )
