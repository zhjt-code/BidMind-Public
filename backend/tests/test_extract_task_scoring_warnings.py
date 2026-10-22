"""防御测试: extract_task 评分项提取异常/silent-empty 写入 extraction_warnings (P0-3a)

职责: 锁定 _collect_scoring_warnings 的两个核心场景——
 1. 提取/归并异常不再静默吞掉，必须写入 extraction_warnings
 2.: 评分 chunk 存在但落库 0 项时追加 silent-empty 警告
不做: 不启动真实后台任务、不访问真实数据库
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.tasks.extract_task import _collect_scoring_warnings  # noqa: E402

pytestmark = pytest.mark.scoring


def test_scoring_exception_produces_warning():
    """评分项提取/归并抛异常时，warnings 中必须有可读描述（不得静默吞掉）。"""
    err = RuntimeError("LLM timeout")
    result = _collect_scoring_warnings(
        scoring_err=err,
        scoring_chunk_count=0,
        scoring_item_count=0,
    )
    assert len(result) == 1, "应有且仅有 1 条警告"
    assert "LLM timeout" in result[0], "警告文本必须包含原始异常信息以便排查"


def test_scoring_chunk_exists_but_zero_items_produces_warning():
    """: 评分 chunk 存在但落库 0 项时，必须追加 silent-empty 警告。"""
    result = _collect_scoring_warnings(
        scoring_err=None,
        scoring_chunk_count=3,
        scoring_item_count=0,
    )
    assert len(result) == 1, "应有且仅有 1 条警告"
    assert "3" in result[0], "警告文本应包含 chunk 数量便于人工核对"


def test_no_chunks_and_no_items_no_warning():
    """无评分 chunk 时 0 项属正常（文档无评分表），不应产生警告。"""
    result = _collect_scoring_warnings(
        scoring_err=None,
        scoring_chunk_count=0,
        scoring_item_count=0,
    )
    assert result == [], "文档无评分表时不应有误报"


def test_items_extracted_successfully_no_warning():
    """成功提取评分项时不应产生警告。"""
    result = _collect_scoring_warnings(
        scoring_err=None,
        scoring_chunk_count=3,
        scoring_item_count=5,
    )
    assert result == [], "正常提取时不应产生警告"


def test_presence_absent_replaces_silent_empty_warning():
    """: 门禁判定无表时不得再写「评分表已识别但未提取」。"""
    result = _collect_scoring_warnings(
        scoring_err=None,
        scoring_chunk_count=2,
        scoring_item_count=0,
        presence_absent=True,
    )
    assert len(result) == 1
    assert "未检出投标评分表" in result[0]
    assert "已识别" not in result[0]


def test_exception_takes_priority_over_silent_empty_no_duplicate():
    """有异常时即使 chunk 存在也仅产生 1 条异常警告（异常优先，不重复）。"""
    err = ValueError("parse failed")
    result = _collect_scoring_warnings(
        scoring_err=err,
        scoring_chunk_count=2,
        scoring_item_count=0,
    )
    assert len(result) == 1, "异常优先，不与 silent-empty 重复叠加"
    assert "parse failed" in result[0]


def test_presence_absent_replaces_silent_empty_copy():
    """: 无表门禁不得再用「评分表已识别但未提取」误导文案。"""
    result = _collect_scoring_warnings(
        scoring_err=None,
        scoring_chunk_count=2,
        scoring_item_count=0,
        presence_absent=True,
    )
    assert len(result) == 1
    assert "未检出投标评分表" in result[0]
    assert "评分表已识别" not in result[0]
