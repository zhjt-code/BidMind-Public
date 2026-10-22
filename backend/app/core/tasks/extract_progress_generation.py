# -*- coding: utf-8 -*-
"""
职责: 为招标要求提取任务生成代际并原子保存当前进度快照。
不做: 不分派任务、不访问数据库、不构建 API 响应、不执行业务提取。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

from app.core.tasks.extract_progress import step_index_for_stage

_HEARTBEAT_KEY_PREFIX = "bidmind:heartbeat:"
_GENERATION_KEY_PREFIX = "bidmind:extract-generation:"
_HEARTBEAT_TTL = 3600

# 本进程绑定：API 入队侧与 Worker/fallback 执行侧各自 bind。
_bound_generations: dict[str, "ExtractProgressGeneration"] = {}
# 本地 fallback 任务归属：用于旧任务 done 回调时避免清掉新代际心跳。
_task_owned_generations: dict[str, "ExtractProgressGeneration"] = {}
# Redis 不可用时的进程内 generation 真源。
_local_generation_meta: dict[str, dict[str, Any]] = {}

_TERMINAL_STAGES = frozenset(
    {"completed", "failed", "失败", "cancelled", "canceled"}
)

_BEGIN_GENERATION_LUA = """
local gen_key = KEYS[1]
local hb_key = KEYS[2]
local ttl = tonumber(ARGV[1])
local run_id = ARGV[2]
local generation = redis.call('HINCRBY', gen_key, 'generation', 1)
redis.call('HSET', gen_key, 'run_id', run_id)
redis.call('EXPIRE', gen_key, ttl)
redis.call('DEL', hb_key)
return generation
"""

_WRITE_HEARTBEAT_LUA = """
local hb_key = KEYS[1]
local gen_key = KEYS[2]
local expect_run_id = ARGV[1]
local expect_generation = ARGV[2]
local stage = ARGV[3]
local progress = ARGV[4]
local phase_rank = tonumber(ARGV[5])
local progress_value = tonumber(ARGV[6])
local ts = ARGV[7]
local is_terminal = ARGV[8] == '1'
local payload_json = ARGV[9]
local ttl = tonumber(ARGV[10])
-- Redis 4+ 拒绝 redis.call 的 bool / 非整数 float；比较用 tonumber，写入只用字符串。
local function redis_arg(v)
  local t = type(v)
  if t == 'string' then return v end
  if t == 'number' or t == 'boolean' then return tostring(v) end
  if t == 'table' then return cjson.encode(v) end
  return ''
end

local cur_run = redis.call('HGET', gen_key, 'run_id')
local cur_gen = redis.call('HGET', gen_key, 'generation')
if (not cur_run) or (not cur_gen) or cur_run ~= expect_run_id or cur_gen ~= expect_generation then
  return {0, 'stale_generation', '0'}
end

local hb_run = redis.call('HGET', hb_key, 'run_id')
if (not hb_run) or hb_run ~= expect_run_id then
  redis.call('DEL', hb_key)
else
  local old_stage = redis.call('HGET', hb_key, 'stage') or ''
  local old_terminal = 0
  if old_stage == 'completed' or old_stage == 'failed' or old_stage == '失败'
     or old_stage == 'cancelled' or old_stage == 'canceled' then
    old_terminal = 1
  end
  if old_terminal == 1 and is_terminal == false then
    redis.call('HSET', hb_key, 'last_heartbeat', ts)
    redis.call('EXPIRE', hb_key, ttl)
    return {0, 'terminal_sticky', redis.call('HGET', hb_key, 'revision') or '0'}
  end

  local old_rank = tonumber(redis.call('HGET', hb_key, 'phase_rank') or '-1')
  local old_prog = tonumber(redis.call('HGET', hb_key, 'progress_value') or '-1')
  if phase_rank < old_rank then
    redis.call('HSET', hb_key, 'last_heartbeat', ts)
    redis.call('EXPIRE', hb_key, ttl)
    return {0, 'phase_regress', redis.call('HGET', hb_key, 'revision') or '0'}
  end
  if phase_rank == old_rank and progress_value < old_prog then
    redis.call('HSET', hb_key, 'last_heartbeat', ts)
    redis.call('EXPIRE', hb_key, ttl)
    return {0, 'progress_regress', redis.call('HGET', hb_key, 'revision') or '0'}
  end
end

-- Redis 3.x（含 Memurai/Win 3.0.504）仅支持单字段 HSET；多字段形式会在
-- HINCRBY 之后抛错并留下半提交的 revision。
local revision = redis.call('HINCRBY', hb_key, 'revision', 1)
redis.call('HSET', hb_key, 'last_heartbeat', ts)
redis.call('HSET', hb_key, 'stage', stage)
redis.call('HSET', hb_key, 'progress', progress)
redis.call('HSET', hb_key, 'run_id', expect_run_id)
redis.call('HSET', hb_key, 'generation', expect_generation)
redis.call('HSET', hb_key, 'phase_rank', ARGV[5])
redis.call('HSET', hb_key, 'progress_value', ARGV[6])
local payload = cjson.decode(payload_json)
for k, v in pairs(payload) do
  local field = redis_arg(k)
  local value = redis_arg(v)
  redis.call('HSET', hb_key, field, value)
end
redis.call('EXPIRE', hb_key, ttl)
redis.call('EXPIRE', gen_key, ttl)
return {1, 'accepted', tostring(revision)}
"""


@dataclass(frozen=True)
class ExtractProgressGeneration:
    """一次招标要求提取运行的代际身份。"""

    run_id: str
    generation: int


@dataclass(frozen=True)
class ExtractHeartbeatWriteOutcome:
    """条件写入结果。"""

    accepted: bool
    reason: str
    revision: int
    snapshot: dict[str, Any]


def begin_extract_generation(task_name: str) -> ExtractProgressGeneration:
    """原子递增代际、清空旧心跳时间线，并绑定到当前进程。"""
    run_id = uuid.uuid4().hex
    generation_value = _increment_generation(task_name, run_id)
    generation = ExtractProgressGeneration(run_id=run_id, generation=generation_value)
    bind_extract_generation(task_name, generation)
    return generation


def bind_extract_generation(
    task_name: str,
    generation: ExtractProgressGeneration,
) -> None:
    """绑定当前进程的提取代际上下文。"""
    _bound_generations[task_name] = generation


def get_bound_extract_generation(
    task_name: str,
) -> Optional[ExtractProgressGeneration]:
    """读取当前进程绑定的提取代际。"""
    return _bound_generations.get(task_name)


def remember_extract_task_owner(
    task_name: str,
    generation: ExtractProgressGeneration,
) -> None:
    """记录本地 fallback 任务所属代际，供 done 回调条件清理。"""
    _task_owned_generations[task_name] = generation


def pop_extract_task_owner(
    task_name: str,
) -> Optional[ExtractProgressGeneration]:
    """取出并清除本地任务代际归属。"""
    return _task_owned_generations.pop(task_name, None)


def should_preserve_extract_heartbeat(
    task_name: str,
    owned: ExtractProgressGeneration,
) -> bool:
    """旧任务收尾时，若当前代际已前进则保留新心跳。"""
    current = _current_generation_meta(task_name)
    if not current:
        return False
    return (
        str(current.get("run_id") or "") != owned.run_id
        or int(current.get("generation") or 0) != owned.generation
    )


def write_extract_heartbeat_if_current(
    task_name: str,
    *,
    stage: str,
    progress: str,
    extra: Optional[dict[str, Any]] = None,
) -> Optional[ExtractHeartbeatWriteOutcome]:
    """若 task 已绑定提取代际，则按代际/阶段单调条件写入；未绑定返回 None。"""
    generation = _bound_generations.get(task_name)
    if generation is None:
        return None

    phase_rank = phase_rank_for_stage(stage)
    progress_value = parse_progress_value(progress)
    ts = time.time()
    extra_payload = dict(extra or {})
    # 身份字段由写入器统一维护，避免调用方覆盖。
    for key in ("run_id", "generation", "revision", "phase_rank", "progress_value"):
        extra_payload.pop(key, None)

    serialized_extra = {
        key: (value if isinstance(value, (str, int, float)) else json.dumps(value))
        for key, value in extra_payload.items()
    }

    redis_outcome = _try_redis_conditional_write(
        task_name=task_name,
        generation=generation,
        stage=stage,
        progress=progress,
        phase_rank=phase_rank,
        progress_value=progress_value,
        ts=ts,
        serialized_extra=serialized_extra,
    )
    if redis_outcome is not None:
        return redis_outcome

    return _local_conditional_write(
        task_name=task_name,
        generation=generation,
        stage=stage,
        progress=progress,
        phase_rank=phase_rank,
        progress_value=progress_value,
        ts=ts,
        extra_payload=extra_payload,
    )


def clear_extract_generation_binding(task_name: str) -> None:
    """仅清除本进程绑定，不触碰 Redis generation key。"""
    _bound_generations.pop(task_name, None)


def phase_rank_for_stage(stage: str) -> int:
    """将心跳 stage 映射为可比较的阶段秩。"""
    normalized = str(stage or "").strip().lower()
    if normalized in {
        "queued",
        "waiting_worker",
        "fallback_waiting",
        "fallback_running",
    }:
        return 0
    if normalized in {"启动", "starting", "unknown", ""}:
        return 1
    if normalized in _TERMINAL_STAGES:
        return 1000
    step_idx = step_index_for_stage(normalized)
    if step_idx >= 0:
        return 10 + step_idx * 10
    # 中文/未知中间态：高于 queued，低于正式步骤。
    return 5


def parse_progress_value(progress: str) -> float:
    """从 progress 字符串解析可比较进度值。"""
    text = str(progress or "").strip()
    if not text:
        return 0.0
    if "|" in text:
        text = text.split("|", 1)[0].strip()
    if "/" in text:
        left, right = text.split("/", 1)
        try:
            done = float(left.strip())
            total = float(right.strip())
            if total > 0:
                return max(0.0, min(done / total, 1.0)) * 100.0
        except ValueError:
            return 0.0
        return 0.0
    if text.endswith("%"):
        text = text[:-1].strip()
    try:
        return float(text)
    except ValueError:
        return 0.0


def _increment_generation(task_name: str, run_id: str) -> int:
    try:
        from app.core.redis import get_sync_redis

        redis = get_sync_redis()
        if redis:
            generation = redis.eval(
                _BEGIN_GENERATION_LUA,
                2,
                f"{_GENERATION_KEY_PREFIX}{task_name}",
                f"{_HEARTBEAT_KEY_PREFIX}{task_name}",
                _HEARTBEAT_TTL,
                run_id,
            )
            value = int(generation)
            _local_generation_meta[task_name] = {
                "generation": value,
                "run_id": run_id,
            }
            return value
    except Exception as exc:  # noqa: BLE001 - Redis 降级不得阻断入队
        logger.debug(f"extract generation redis begin failed [{task_name}]: {exc}")

    previous = int(_local_generation_meta.get(task_name, {}).get("generation") or 0)
    value = previous + 1
    _local_generation_meta[task_name] = {"generation": value, "run_id": run_id}
    return value


def _current_generation_meta(task_name: str) -> Optional[dict[str, Any]]:
    local = _local_generation_meta.get(task_name)
    if local:
        return local
    try:
        from app.core.redis import get_sync_redis

        redis = get_sync_redis()
        if not redis:
            return None
        raw = redis.hgetall(f"{_GENERATION_KEY_PREFIX}{task_name}")
        if not raw:
            return None
        return {
            "generation": int(raw.get("generation") or 0),
            "run_id": str(raw.get("run_id") or ""),
        }
    except Exception:
        return None


def _try_redis_conditional_write(
    *,
    task_name: str,
    generation: ExtractProgressGeneration,
    stage: str,
    progress: str,
    phase_rank: int,
    progress_value: float,
    ts: float,
    serialized_extra: dict[str, Any],
) -> Optional[ExtractHeartbeatWriteOutcome]:
    try:
        from app.core.redis import get_sync_redis

        redis = get_sync_redis()
        if not redis:
            return None
        result = redis.eval(
            _WRITE_HEARTBEAT_LUA,
            2,
            f"{_HEARTBEAT_KEY_PREFIX}{task_name}",
            f"{_GENERATION_KEY_PREFIX}{task_name}",
            generation.run_id,
            str(generation.generation),
            stage,
            progress,
            str(phase_rank),
            str(progress_value),
            str(ts),
            "1" if str(stage).strip().lower() in _TERMINAL_STAGES else "0",
            json.dumps(serialized_extra, ensure_ascii=False),
            str(_HEARTBEAT_TTL),
        )
        accepted = bool(int(result[0]))
        reason = str(result[1])
        revision = int(float(result[2] or 0))
        snapshot = {
            "last_heartbeat": ts,
            "stage": stage,
            "progress": progress,
            "run_id": generation.run_id,
            "generation": generation.generation,
            "revision": revision,
            "phase_rank": phase_rank,
            "progress_value": progress_value,
            **{
                key: _maybe_json_load(value)
                for key, value in serialized_extra.items()
            },
        }
        if not accepted:
            # 拒绝时不覆盖业务字段；调用方仅刷新 last_heartbeat。
            snapshot = {"last_heartbeat": ts}
        return ExtractHeartbeatWriteOutcome(
            accepted=accepted,
            reason=reason,
            revision=revision,
            snapshot=snapshot,
        )
    except Exception as exc:  # noqa: BLE001
        # 脚本半失败会静默降级到本地写入，API 读 Redis 时看不到 stage。
        logger.warning(f"extract heartbeat redis write failed [{task_name}]: {exc}")
        return None


def _local_conditional_write(
    *,
    task_name: str,
    generation: ExtractProgressGeneration,
    stage: str,
    progress: str,
    phase_rank: int,
    progress_value: float,
    ts: float,
    extra_payload: dict[str, Any],
) -> ExtractHeartbeatWriteOutcome:
    meta = _local_generation_meta.get(task_name) or {}
    if (
        str(meta.get("run_id") or "") != generation.run_id
        or int(meta.get("generation") or 0) != generation.generation
    ):
        return ExtractHeartbeatWriteOutcome(
            accepted=False,
            reason="stale_generation",
            revision=0,
            snapshot={"last_heartbeat": ts},
        )

    current = dict(meta.get("heartbeat") or {})
    old_run = str(current.get("run_id") or "")
    if old_run and old_run != generation.run_id:
        current = {}
    old_stage = str(current.get("stage") or "")
    if (
        old_stage.strip().lower() in _TERMINAL_STAGES
        and stage.strip().lower() not in _TERMINAL_STAGES
    ):
        current["last_heartbeat"] = ts
        meta["heartbeat"] = current
        _local_generation_meta[task_name] = meta
        return ExtractHeartbeatWriteOutcome(
            accepted=False,
            reason="terminal_sticky",
            revision=int(current.get("revision") or 0),
            snapshot={"last_heartbeat": ts},
        )

    old_rank = float(current.get("phase_rank") if current.get("phase_rank") is not None else -1)
    old_prog = float(
        current.get("progress_value") if current.get("progress_value") is not None else -1
    )
    if phase_rank < old_rank or (
        phase_rank == old_rank and progress_value < old_prog
    ):
        current["last_heartbeat"] = ts
        meta["heartbeat"] = current
        _local_generation_meta[task_name] = meta
        reason = "phase_regress" if phase_rank < old_rank else "progress_regress"
        return ExtractHeartbeatWriteOutcome(
            accepted=False,
            reason=reason,
            revision=int(current.get("revision") or 0),
            snapshot={"last_heartbeat": ts},
        )

    revision = int(current.get("revision") or 0) + 1
    snapshot = {
        **current,
        **extra_payload,
        "last_heartbeat": ts,
        "stage": stage,
        "progress": progress,
        "run_id": generation.run_id,
        "generation": generation.generation,
        "revision": revision,
        "phase_rank": phase_rank,
        "progress_value": progress_value,
    }
    meta["heartbeat"] = snapshot
    _local_generation_meta[task_name] = meta
    return ExtractHeartbeatWriteOutcome(
        accepted=True,
        reason="accepted",
        revision=revision,
        snapshot=snapshot,
    )


def _maybe_json_load(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def reset_extract_generation_state_for_tests() -> None:
    """测试专用：清空进程内代际状态。"""
    _bound_generations.clear()
    _task_owned_generations.clear()
    _local_generation_meta.clear()


__all__ = [
    "ExtractHeartbeatWriteOutcome",
    "ExtractProgressGeneration",
    "begin_extract_generation",
    "bind_extract_generation",
    "clear_extract_generation_binding",
    "get_bound_extract_generation",
    "parse_progress_value",
    "phase_rank_for_stage",
    "pop_extract_task_owner",
    "remember_extract_task_owner",
    "reset_extract_generation_state_for_tests",
    "should_preserve_extract_heartbeat",
    "write_extract_heartbeat_if_current",
]
