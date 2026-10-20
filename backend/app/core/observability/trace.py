"""结构化 trace 日志发射器。

B0 产出 guard_run 事件；后续批次扩展 gate_run / failure_state_determined 等。
trace schema 参考 docs/evidence_gate_design.md §7.1。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from . import OBSERVABILITY_ENABLED

logger = logging.getLogger("observability.trace")

_TRACE_VERSION = "0.1.0"


def emit(event: str, payload: dict[str, Any]) -> None:
    """发射一条结构化 trace 日志。

    所有 trace 均为 JSON 单行写入 logger，便于后续接入日志后端。
    当 OBSERVABILITY_ENABLED=False 时静默跳过。
    """
    if not OBSERVABILITY_ENABLED:
        return

    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "trace_version": _TRACE_VERSION,
        "event": event,
        **payload,
    }
    logger.info(json.dumps(record, ensure_ascii=False, default=str))


def guard_run_trace(
    *,
    guard_name: str,
    flow: str,
    target_id: Any,
    applied: bool,
    verdict_hint: str,
    latency_ms: float,
    input_hash: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """发射 guard_run 事件（B0 专用，观测现有 guard 执行情况）。"""
    payload: dict[str, Any] = {
        "guard_name": guard_name,
        "flow": flow,
        "target_id": target_id,
        "applied": applied,
        "verdict_hint": verdict_hint,
        "latency_ms": round(latency_ms, 3),
        "input_hash": input_hash,
    }
    if extra:
        payload["extra"] = extra
    emit("guard_run", payload)
