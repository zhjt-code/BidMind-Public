"""任务生命周期判定子模块

职责: 为任务活性、中断与终态收敛提供可独立测试的判定单元；
     确认某 arq_function 的 worker 缺失是否已持续超过宽限期。
不做: 不直接写业务状态、不调度任务、不访问 LLM、不参与派发侧 can_use_arq。

存在原因: `app/core/tasks/__init__.py` 已超复杂度护栏硬阈值（>800 行），
新增判定能力必须落在本子模块。worker 缺失宽限与包入口同文件，
避免分析器漏索引新建子模块导致导入不可解析。
"""

from __future__ import annotations

import time
from typing import Optional

from loguru import logger

WORKER_MISSING_KEY_PREFIX = "bidmind:worker_missing_since:"
DEFAULT_WORKER_MISSING_GRACE_SEC = 90.0

__all__ = [
    "DEFAULT_WORKER_MISSING_GRACE_SEC",
    "WORKER_MISSING_KEY_PREFIX",
    "is_worker_confirmed_missing",
]


def _marker_key(function_name: str) -> str:
    return f"{WORKER_MISSING_KEY_PREFIX}{function_name}"


def _marker_ttl_sec(grace_sec: float) -> int:
    return max(int(grace_sec * 2), 1)


def is_worker_confirmed_missing(
    function_name: str,
    *,
    grace_sec: float = DEFAULT_WORKER_MISSING_GRACE_SEC,
    now: Optional[float] = None,
) -> bool:
    """worker 存活键是否已连续缺失超过宽限。

    存活则清除观测标记并返回 False。缺失则记首次观测时间；
    持续缺失满 ``grace_sec`` 才返回 True。Redis / 探测异常返回 False
    （无法观测 ≠ 死亡）。
    """
    name = str(function_name or "").strip()
    if not name:
        return False
    now_ts = time.time() if now is None else now

    try:
        from app.core.arq_runtime import has_alive_worker_for

        if has_alive_worker_for(name):
            _clear_marker(name)
            return False
    except Exception as exc:
        logger.debug(
            "worker 存活探测失败，按未确认缺失处理: fn={}, err={}",
            name,
            exc,
        )
        return False

    try:
        from app.core.redis import get_sync_redis

        redis_client = get_sync_redis()
        if redis_client is None:
            return False

        key = _marker_key(name)
        ttl = _marker_ttl_sec(grace_sec)
        raw = redis_client.get(key)
        if raw is None:
            created = redis_client.set(key, str(now_ts), nx=True, ex=ttl)
            if created:
                return False
            raw = redis_client.get(key)
            if raw is None:
                return False

        try:
            redis_client.expire(key, ttl)
        except Exception:
            pass

        first_seen = float(raw)
        return (now_ts - first_seen) >= grace_sec
    except Exception as exc:
        logger.debug(
            "worker 缺失标记读写失败，按未确认缺失处理: fn={}, err={}",
            name,
            exc,
        )
        return False


def _clear_marker(function_name: str) -> None:
    try:
        from app.core.redis import get_sync_redis

        redis_client = get_sync_redis()
        if redis_client is None:
            return
        redis_client.delete(_marker_key(function_name))
    except Exception as exc:
        logger.debug(
            "清除 worker 缺失标记失败: fn={}, err={}",
            function_name,
            exc,
        )
