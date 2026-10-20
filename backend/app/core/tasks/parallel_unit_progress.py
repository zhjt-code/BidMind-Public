"""
职责: 把并行单元的已完成数与在途数折成步骤百分比，并判定心跳百分数/阶段字段是否属于当前运行步。
不做: 不写 Redis、不读任务状态、不决定步骤是否完成。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Callable

from app.core.review_contracts import (
    REVIEW_STEP_LABEL_ASSEMBLE,
    REVIEW_STEP_LABEL_EXTRACT,
    REVIEW_STEP_LABEL_READY,
    REVIEW_STEP_LABEL_REVIEW,
)

PARALLEL_UNIT_INFLIGHT_WEIGHT = 0.25

HEARTBEAT_STAGE_SCOPED_KEYS = frozenset(
    {
        "stage_progress_pct",
        "substage",
        "inflight",
        "current",
        "total",
        "message",
        "phase",
    }
)

_REVIEW_STEP_STAGE_TOKENS: dict[str, tuple[str, ...]] = {
    REVIEW_STEP_LABEL_EXTRACT: ("步骤2", "提取"),
    REVIEW_STEP_LABEL_ASSEMBLE: ("步骤3", "匹配", "装配", "assemble"),
    REVIEW_STEP_LABEL_REVIEW: ("步骤4", "审查", "重试", "收尾"),
    REVIEW_STEP_LABEL_READY: ("步骤5",),
}

_HEARTBEAT_PHASE_STAGE_TOKENS: dict[str, tuple[str, ...]] = {
    "self_healing": ("重试",),
    "finalizing": ("收尾",),
}


def parallel_unit_progress_pct(
    done: int,
    total: int,
    inflight: int = 0,
    *,
    weight: float = PARALLEL_UNIT_INFLIGHT_WEIGHT,
) -> int:
    """已完成记满格，在途按权重折算；未完成不超过 99。"""
    if total <= 0:
        return 0
    if done >= total:
        return 100
    weighted = max(done, 0) + max(inflight, 0) * weight
    return max(0, min(99, int(weighted / total * 100)))


def parallel_unit_heartbeat_extra(
    done: int,
    total: int,
    inflight: int = 0,
) -> dict[str, Any]:
    """心跳 extra：current/total 表示已完成量，pct 含在途。"""
    return {
        "current": done,
        "total": total,
        "inflight": inflight,
        "stage_progress_pct": parallel_unit_progress_pct(done, total, inflight),
    }


def invoke_unit_progress(
    callback: Callable[..., None] | None,
    done: int,
    total: int,
    inflight: int = 0,
) -> None:
    """优先 callback(done, total, inflight)，旧 2 参回调自动降级。"""
    if callback is None:
        return
    try:
        callback(done, total, inflight)
    except TypeError:
        callback(done, total)


def _parse_fraction_progress(progress: str) -> tuple[int | None, int | None]:
    text = str(progress or "").strip()
    if "/" not in text:
        return None, None
    left, right = text.split("/", 1)
    try:
        return int(left.strip()), int(right.strip())
    except ValueError:
        return None, None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_heartbeat_unit_counts(
    heartbeat: dict[str, Any] | None,
) -> tuple[int, int, int]:
    """
    职责: 从心跳 current/total/inflight 或 progress 分数解析单元计数。
    不做: 不写 Redis、不决定步骤完成、不折算条宽。
    """
    if not heartbeat:
        return 0, 0, 0
    inflight = max(0, _optional_int(heartbeat.get("inflight")) or 0)
    current = _optional_int(heartbeat.get("current"))
    total = _optional_int(heartbeat.get("total"))
    prog_done, prog_total = _parse_fraction_progress(str(heartbeat.get("progress") or ""))
    done = current if current is not None else (prog_done or 0)
    resolved_total = total if total is not None else (prog_total or 0)
    return max(0, done), max(0, resolved_total), inflight


def merge_running_step_detail(
    persisted_done: int,
    persisted_total: int,
    heartbeat: dict[str, Any] | None = None,
    *,
    stage_tokens: tuple[str, ...] = (),
) -> str:
    """
    职责: 运行中步骤文案取 max(落库, 同属当前步的心跳 current)，完成量为 0 且在途>0 时带在途。
    不做: 不改条宽、不判断步骤是否完成、不处理重试 phase 文案。
    """
    if stage_tokens and not heartbeat_belongs_to_running_step(heartbeat, stage_tokens):
        heartbeat = None
    hb_done, hb_total, inflight = parse_heartbeat_unit_counts(heartbeat)
    try:
        persisted_done_n = max(0, int(persisted_done or 0))
    except (TypeError, ValueError):
        persisted_done_n = 0
    try:
        persisted_total_n = max(0, int(persisted_total or 0))
    except (TypeError, ValueError):
        persisted_total_n = 0
    total = max(persisted_total_n, hb_total)
    done = max(persisted_done_n, hb_done)
    if total <= 0:
        return str((heartbeat or {}).get("progress") or "").strip()
    if done <= 0 and inflight > 0:
        return f"{done}/{total} · 在途 {inflight}"
    return f"{done}/{total}"


def heartbeat_text_matches_tokens(text: str, tokens: tuple[str, ...]) -> bool:
    """任一 token 出现在文本中即视为同属当前步。"""
    hay = str(text or "")
    return any(token and token in hay for token in tokens)


def heartbeat_belongs_to_running_step(
    heartbeat: dict[str, Any] | None,
    stage_tokens: tuple[str, ...],
) -> bool:
    """心跳 stage/substage 是否属于当前运行步。"""
    if not heartbeat or not stage_tokens:
        return False
    stage = str(heartbeat.get("stage") or "")
    if not heartbeat_text_matches_tokens(stage, stage_tokens):
        return False
    substage = str(heartbeat.get("substage") or "")
    if substage and not heartbeat_text_matches_tokens(substage, stage_tokens):
        return False
    return True


def stage_tokens_for_review_step(label: str) -> tuple[str, ...]:
    """审查进度四步各自允许消费的心跳 stage/substage token。"""
    return _REVIEW_STEP_STAGE_TOKENS.get(label, ())


def heartbeat_phase_belongs_to_stage(phase: str, stage: str) -> bool:
    """phase 仅在心跳 stage 带有该 phase 的归属 token 时生效。"""
    tokens = _HEARTBEAT_PHASE_STAGE_TOKENS.get(str(phase or ""), ())
    return bool(tokens) and heartbeat_text_matches_tokens(stage, tokens)


def stale_stage_scoped_keys(written_keys: Iterable[str]) -> tuple[str, ...]:
    """本次写入未带上的阶段私有字段，stage 变化时应从 Hash 删除。"""
    return tuple(sorted(HEARTBEAT_STAGE_SCOPED_KEYS - set(written_keys)))


def should_drop_stale_stage_scoped(old_stage: str, new_stage: str) -> bool:
    """仅在旧 stage 已知且与新 stage 不同时丢弃无主 extra。"""
    return bool(old_stage) and old_stage != new_stage


def redis_stage_scoped_fields_to_drop(
    old_stage: str,
    new_stage: str,
    written_keys: Iterable[str],
) -> tuple[str, ...]:
    """stage 未变（含 keepalive）时返回空，避免清掉同阶段在途百分数。"""
    if not should_drop_stale_stage_scoped(old_stage, new_stage):
        return ()
    return stale_stage_scoped_keys(written_keys)


def running_step_progress_from_heartbeat(
    persisted_pct: int,
    heartbeat: dict[str, Any] | None,
    *,
    stage_tokens: tuple[str, ...] = (),
) -> int:
    """运行中步骤：仅当心跳 stage/substage 属于本步时，与 stage_progress_pct 取大。"""
    try:
        persisted = max(0, min(100, int(persisted_pct)))
    except (TypeError, ValueError):
        persisted = 0
    if not heartbeat or not stage_tokens:
        return persisted
    if not heartbeat_belongs_to_running_step(heartbeat, stage_tokens):
        return persisted
    try:
        hb_pct = max(0, min(100, int(heartbeat.get("stage_progress_pct") or 0)))
    except (TypeError, ValueError):
        hb_pct = 0
    combined = max(persisted, hb_pct)
    if combined >= 100 and persisted < 100:
        return 99
    return combined
