"""
职责: 判定用途是否已配置，并用调度池回填遗留单选 ID
不做: 不读写磁盘、不重建 Dispatcher、不判断通道健康或密钥
"""

from __future__ import annotations

from typing import Any, Iterable

_PURPOSES: tuple[str, ...] = ("vision", "review", "judge", "extract", "scoring")


def _valid_pool_ids(settings: Any, purpose: str) -> list[str]:
    getter = getattr(settings, "get_pool_ids", None)
    raw = getter(purpose) if callable(getter) else getattr(
        settings, f"{purpose}_pool_ids", None
    )
    raw = raw or []
    if not isinstance(raw, list):
        return []
    lookup = getattr(settings, "get_config_by_id", None)
    valid: list[str] = []
    for cid in raw:
        if not cid or not isinstance(cid, str):
            continue
        if callable(lookup) and lookup(purpose, cid) is None:
            continue
        valid.append(cid)
    return valid


def _legacy_id(settings: Any, purpose: str) -> str:
    getter = getattr(settings, "get_model_id", None)
    value = getter(purpose) if callable(getter) else getattr(
        settings, f"{purpose}_model_id", ""
    )
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value) if value else ""


def _priority_of(settings: Any, purpose: str, config_id: str) -> int:
    fn = getattr(settings, "get_priority_for", None)
    if callable(fn):
        return int(fn(purpose, config_id))
    mapping = getattr(settings, f"{purpose}_pool_priority", None) or {}
    if isinstance(mapping, dict):
        return int(mapping.get(config_id, 100))
    return 100


def _pick_preferred(settings: Any, purpose: str, pool_ids: Iterable[str]) -> str:
    ids = list(pool_ids)
    if not ids:
        return ""
    return min(
        ids,
        key=lambda cid: (_priority_of(settings, purpose, cid), ids.index(cid)),
    )


def purpose_has_models(settings: Any, purpose: str) -> bool:
    """池内仍有有效配置，或遗留单选仍指向有效配置。"""
    if _valid_pool_ids(settings, purpose):
        return True
    current = _legacy_id(settings, purpose)
    if not current:
        return False
    lookup = getattr(settings, "get_config_by_id", None)
    if not callable(lookup):
        return True
    return lookup(purpose, current) is not None


def sync_legacy_model_id(settings: Any, purpose: str) -> bool:
    """把遗留单选 ID 对齐到当前有效池；池空时仅在配置仍存在时保留单选。

    Returns:
        True 表示改写了 model_id。
    """
    pool = _valid_pool_ids(settings, purpose)
    current = _legacy_id(settings, purpose)
    if pool:
        if current in pool:
            return False
        settings.set_model_id(purpose, _pick_preferred(settings, purpose, pool))
        return True
    if current and settings.get_config_by_id(purpose, current) is not None:
        return False
    if current:
        settings.set_model_id(purpose, "")
        return True
    return False


def sync_all_legacy_model_ids(settings: Any) -> list[str]:
    """对全部用途做一次影子 ID 对齐，返回发生改写的 purpose 列表。"""
    changed: list[str] = []
    for purpose in _PURPOSES:
        if sync_legacy_model_id(settings, purpose):
            changed.append(purpose)
    return changed
