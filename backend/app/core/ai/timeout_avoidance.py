"""
职责: 按 dispatcher purpose 记录最近超时的 model_name/group，供后续 _dispatch 起步注入 avoid
不做: 通道选择、AIMD、熔断、节点路由、业务重试
"""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_models: dict[str, dict[str, float]] = {}
_groups: dict[str, dict[str, float]] = {}


def reset_timeout_avoids() -> None:
    """清空进程内粘滞记录。测试隔离用，生产路径不调用。"""
    with _lock:
        _models.clear()
        _groups.clear()


def _enabled() -> bool:
    try:
        from app.core.config import settings
        return bool(getattr(settings, "LLM_TIMEOUT_STICKY_ENABLED", True))
    except Exception:
        return True


def _ttl_seconds() -> float:
    try:
        from app.core.config import settings
        return float(getattr(settings, "LLM_TIMEOUT_STICKY_SECONDS", 180) or 0)
    except Exception:
        return 180.0


def record_timeout(
    purpose: str,
    model_name: str,
    model_group: str,
    now: float | None = None,
) -> None:
    """记录一次超时，供同 purpose 的后续 dispatch 起步避开。"""
    if not _enabled():
        return
    ttl = _ttl_seconds()
    if ttl <= 0 or not purpose:
        return
    expires = (now if now is not None else time.monotonic()) + ttl
    with _lock:
        if model_name:
            _models.setdefault(purpose, {})[model_name] = expires
        if model_group:
            _groups.setdefault(purpose, {})[model_group] = expires


def current_avoids(
    purpose: str,
    now: float | None = None,
) -> tuple[set[str], set[str]]:
    """返回 (avoid_groups, avoid_models)；过期项不返回。"""
    if not _enabled() or not purpose:
        return set(), set()
    ts = now if now is not None else time.monotonic()
    with _lock:
        group_map = _groups.get(purpose, {})
        model_map = _models.get(purpose, {})
        groups = {name for name, exp in group_map.items() if exp > ts}
        models = {name for name, exp in model_map.items() if exp > ts}
        for name in [k for k, exp in group_map.items() if exp <= ts]:
            group_map.pop(name, None)
        for name in [k for k, exp in model_map.items() if exp <= ts]:
            model_map.pop(name, None)
    return groups, models
