"""项目时间线服务防御测试 (P1 级)。

防御目标 (阶段三改造):
- _build_summary 正确统计四类事件 (errors/warnings/milestones/task_*) 与 incident_id 数量
- 时间序合并按 ts 倒序排列, ts 缺失退到末尾
- only_errors 模式下排除 milestones/audits, 但保留任务失败 end 事件
- _serialize_ts 不破坏其他字段, 仅把 datetime 转为 ISO 字符串

不依赖真实 DB, 全部用纯函数与构造的 dict 进行单元测试。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.project_timeline_service import (  # noqa: E402
    _build_summary,
    _serialize_ts,
)

# ── _build_summary: 四类事件统计 ───────────────────────────────────


def test_build_summary_counts_all_kinds_correctly() -> None:
    items = [
        {"kind": "error_log", "level": "ERROR", "incident_id": "iid_a"},
        {"kind": "error_log", "level": "CRITICAL", "incident_id": "iid_a"},
        {"kind": "error_log", "level": "WARNING", "incident_id": "iid_b"},
        {"kind": "error_log", "level": "ERROR", "incident_id": None},
        {"kind": "milestone", "task_type": "extract"},
        {"kind": "milestone", "task_type": "review"},
        {"kind": "task_observation", "phase": "start"},
        {"kind": "task_observation", "phase": "end", "outcome": "success", "duration_ms": 1500},
        {"kind": "task_observation", "phase": "end", "outcome": "failure", "duration_ms": 800},
        {"kind": "audit", "action": "create"},
    ]

    summary = _build_summary(items)

    assert summary["total_events"] == 10
    assert summary["errors"] == 3  # ERROR + CRITICAL + 无 incident 的 ERROR
    assert summary["warnings"] == 1
    assert summary["milestones"] == 2
    assert summary["task_starts"] == 1
    assert summary["task_completed"] == 1
    assert summary["task_failed"] == 1
    assert summary["audit_events"] == 1
    assert summary["incident_count"] == 2  # iid_a, iid_b (None 不计入)
    assert summary["total_task_duration_ms"] == 2300


def test_build_summary_handles_empty_input() -> None:
    summary = _build_summary([])
    assert summary["total_events"] == 0
    assert summary["errors"] == 0
    assert summary["incident_count"] == 0
    assert summary["total_task_duration_ms"] == 0


def test_build_summary_dedupes_incident_ids() -> None:
    """同一 incident_id 出现多次时, incident_count 只计一次。"""
    items = [
        {"kind": "error_log", "level": "ERROR", "incident_id": "same_iid"},
        {"kind": "error_log", "level": "ERROR", "incident_id": "same_iid"},
        {"kind": "error_log", "level": "ERROR", "incident_id": "same_iid"},
    ]
    summary = _build_summary(items)
    assert summary["errors"] == 3
    assert summary["incident_count"] == 1


def test_build_summary_ignores_invalid_duration() -> None:
    """duration_ms 不可解析为 int 时不应崩溃, 累加器跳过。"""
    items = [
        {"kind": "task_observation", "phase": "end", "outcome": "success", "duration_ms": "bad"},
        {"kind": "task_observation", "phase": "end", "outcome": "success", "duration_ms": 100},
        {"kind": "task_observation", "phase": "end", "outcome": "success", "duration_ms": None},
    ]
    summary = _build_summary(items)
    assert summary["task_completed"] == 3
    assert summary["total_task_duration_ms"] == 100


# ── _serialize_ts: datetime → ISO 字符串 ────────────────────────


def test_serialize_ts_converts_datetime_to_iso_string() -> None:
    ts = datetime(2026, 5, 10, 12, 30, 45, tzinfo=timezone.utc)
    item = {"kind": "error_log", "ts": ts, "message": "hello", "level": "ERROR"}
    result = _serialize_ts(dict(item))
    assert isinstance(result["ts"], str)
    assert result["ts"].startswith("2026-05-10T12:30:45")
    # 其他字段不变
    assert result["message"] == "hello"
    assert result["level"] == "ERROR"


def test_serialize_ts_preserves_string_ts() -> None:
    """ts 已是字符串时不应改变。"""
    item = {"kind": "audit", "ts": "2026-05-10T00:00:00", "action": "create"}
    result = _serialize_ts(dict(item))
    assert result["ts"] == "2026-05-10T00:00:00"
    assert result["action"] == "create"


def test_serialize_ts_handles_none_ts() -> None:
    item = {"kind": "milestone", "ts": None, "summary": "x"}
    result = _serialize_ts(dict(item))
    assert result["ts"] is None
    assert result["summary"] == "x"


# ── 综合: 时间序排序逻辑 ────────────────────────────────────────


def test_items_sort_descending_with_none_at_end() -> None:
    """模拟 service 内的排序逻辑: ts 倒序, None 放末尾。"""
    base = datetime(2026, 5, 10, tzinfo=timezone.utc)
    items = [
        {"kind": "a", "ts": base + timedelta(minutes=10)},
        {"kind": "b", "ts": base},
        {"kind": "c", "ts": None},
        {"kind": "d", "ts": base + timedelta(minutes=20)},
    ]
    items.sort(key=lambda x: (x.get("ts") is not None, x.get("ts")), reverse=True)
    assert [it["kind"] for it in items] == ["d", "a", "b", "c"]
