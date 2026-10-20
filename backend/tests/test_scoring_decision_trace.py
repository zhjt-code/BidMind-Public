"""评分裁决轨迹通道防御测试（治理阶段 3）。

职责: 保证 trace 通道①默认关闭且零行为影响；②不污染生产输出通道；
      ③branch_id 台账与源码机械一致；④确实能区分不同解析路由。
不做: 不断言具体样本走哪条路由（那是 T0 快照与 golden 的职责）。

设计红线（F22）: trace 禁止复用 diagnostics——后者会进入用户可见报告并
经 scoring_diagnostic_policy 翻转 needs_review，写它等于改变生产裁决。
"""
from __future__ import annotations

import ast
import os
import pathlib
import re

import pytest

pytestmark = pytest.mark.scoring

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-key-not-for-prod")
os.environ.setdefault("ADMIN_PASSWORD", "<TEST_PASSWORD>")

from app.core.extractor.scoring.scoring_decision_trace import (  # noqa: E402
    capture_decision_trace,
    is_tracing,
    record,
)
from app.core.extractor.scoring.scoring_table_parser import parse_scoring_table  # noqa: E402

SCORING_DIR = (
    pathlib.Path(__file__).resolve().parents[1] / "app/core/extractor/scoring"
)
REGISTRY_PATH = SCORING_DIR / "BRANCH_REGISTRY.md"
# 埋点调用形态: _trace("DOMAIN/semantic-id")
_TRACE_CALL = re.compile(r'_trace\(\s*"([^"]+)"\s*\)')
_REGISTRY_ID = re.compile(r"`([A-Z_]+/[a-z0-9\-]+)`")


def _source_branch_ids() -> set[str]:
    ids: set[str] = set()
    for path in SCORING_DIR.rglob("*.py"):
        ids.update(_TRACE_CALL.findall(path.read_text(encoding="utf-8")))
    return ids


def _registry_branch_ids() -> set[str]:
    return set(_REGISTRY_ID.findall(REGISTRY_PATH.read_text(encoding="utf-8")))


class TestDecisionTraceChannel:
    """通道本身的行为契约。"""

    def test_disabled_by_default_and_record_is_noop(self) -> None:
        """未开启采集时 record 不抛错、不产生任何状态。"""
        assert is_tracing() is False
        record("PARSE_ROUTE/detail-lexical-roles")  # 不得抛出
        assert is_tracing() is False

    def test_capture_collects_hits_in_order(self) -> None:
        with capture_decision_trace() as trace:
            record("A/one")
            record("B/two")
            record("A/one")
        assert trace.hits == {"A/one": 2, "B/two": 1}
        assert trace.ordered_ids == ["A/one", "B/two"]
        assert trace.as_snapshot() == {"A/one": 2, "B/two": 1}

    def test_context_restored_after_capture(self) -> None:
        """采集会话退出后必须恢复关闭态，避免污染后续用例。"""
        with capture_decision_trace():
            assert is_tracing() is True
        assert is_tracing() is False


class TestDecisionTraceIsBehaviorNeutral:
    """开关不得改变任何解析产物（零倒退前提）。"""

    _GRID = [
        ["评分因素", "分值", "评分标准"],
        ["施工组织设计", "20", "方案完整得20分"],
        ["项目业绩", "10", "每个业绩得2分"],
    ]

    def _parse(self):
        return parse_scoring_table("", grid=[list(r) for r in self._GRID])

    def test_parse_result_identical_with_and_without_trace(self) -> None:
        """同一输入在开启/关闭采集两种模式下产物必须完全一致。"""
        off = self._parse()
        with capture_decision_trace() as trace:
            on = self._parse()

        assert trace.hits, "采集态应至少命中一条裁决分支，否则埋点未生效"
        assert off.confident == on.confident
        assert len(off.items) == len(on.items)
        for left, right in zip(off.items, on.items):
            assert left.model_dump() == right.model_dump()

    def test_trace_does_not_leak_into_diagnostics(self) -> None:
        """F22 红线: 采集内容不得出现在 diagnostics（生产可见且参与 needs_review）。"""
        with capture_decision_trace() as trace:
            parsed = self._parse()
        joined = " ".join(parsed.diagnostics or [])
        for branch_id in trace.hits:
            assert branch_id not in joined, (
                f"branch_id {branch_id} 泄漏进 diagnostics——"
                "该通道会进入用户可见报告并可翻转 needs_review"
            )


class TestBranchRegistryIntegrity:
    """台账与源码必须机械一致，防止 ID 静默增减。"""

    def test_every_source_branch_id_is_registered(self) -> None:
        missing = sorted(_source_branch_ids() - _registry_branch_ids())
        assert not missing, (
            f"以下 branch_id 已埋点但未登记于 BRANCH_REGISTRY.md: {missing}"
        )

    def test_every_registered_branch_id_exists_in_source(self) -> None:
        """台账不得留孤儿 ID；确需退役时移入"已退役分支"小节并标注接替 ID。"""
        retired_section = REGISTRY_PATH.read_text(encoding="utf-8").split("## 已退役分支")[-1]
        retired = set(_REGISTRY_ID.findall(retired_section))
        orphans = sorted(_registry_branch_ids() - _source_branch_ids() - retired)
        assert not orphans, (
            f"以下 branch_id 登记在册但源码中已不存在: {orphans}"
        )

    def test_registry_covers_regression_epicenter_files(self) -> None:
        """回归震中文件必须有埋点，否则定位能力覆盖不到最高频改动区。"""
        for filename in ("scoring_table_parser.py", "score_totals.py"):
            source = (SCORING_DIR / filename).read_text(encoding="utf-8")
            assert _TRACE_CALL.search(source), f"{filename} 缺少裁决埋点"

    def test_trace_calls_are_single_line_statements(self) -> None:
        """埋点只允许是既有分支体内的独立单行调用，不得包裹进新函数或表达式。

        这是复杂度护栏的机械体现: scoring_table_parser.py 已超 800 行 Hard 阈值，
        本轮只允许"加单行"，禁止新增方法或改变分支条件。
        """
        for filename in ("scoring_table_parser.py", "score_totals.py"):
            tree = ast.parse((SCORING_DIR / filename).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not (isinstance(node.func, ast.Name) and node.func.id == "_trace"):
                    continue
                assert len(node.args) == 1 and isinstance(node.args[0], ast.Constant), (
                    f"{filename}: _trace 参数必须是字面量常量，禁止动态拼接"
                )
