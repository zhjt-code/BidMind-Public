"""评分项提取子阶段进度折算。

职责: 把评分项提取的阶段/单位计数折算为 scoring_extract 的 (current, total, message)
不做: 不写心跳、不调用 LLM/DB、不决定是否救援
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Optional

SCORING_PHASE_SPANS: dict[str, tuple[int, int]] = {
    "chunks": (0, 60),
    "l2_repair": (60, 75),
    "l3_tail": (75, 85),
    "pdf_chat": (85, 90),
    "vlm": (90, 95),
    # pdf_direct 主路径(2026-08-31): 定位+轮次直读占满评分段, persist 收尾
    "pdf_direct": (0, 95),
    "persist": (95, 100),
}


def map_scoring_phase(
    phase: str,
    done: int,
    total: int,
    detail: str,
) -> tuple[int, int, str]:
    """把子阶段完成数折成 scoring_extract 的 current/total/message。"""
    start, end = SCORING_PHASE_SPANS[phase]
    if total <= 0:
        current = end
    else:
        frac = min(1.0, max(0.0, done / total))
        current = start + int((end - start) * frac)
    return current, 100, detail


def emit_scoring_progress(
    callback: Optional[Callable[..., None]],
    phase: str,
    done: int,
    total: int,
    detail: str,
) -> None:
    """有回调则发射 scoring_extract 心跳参数; 无回调则静默。"""
    if callback is None:
        return
    current, tot, msg = map_scoring_phase(phase, done, total, detail)
    callback("scoring_extract", current, tot, msg)


__all__ = [
    "SCORING_PHASE_SPANS",
    "emit_scoring_progress",
    "map_scoring_phase",
]
