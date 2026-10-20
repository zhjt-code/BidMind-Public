"""
职责: 将 review 池配置克隆为 extract 池（ID前缀改写 + thinking_policy=disabled）
不做: 不负责持久化读写、不负责调度器构建、不持有运行时状态
"""

from __future__ import annotations

import copy
from typing import Any


def _to_extract_config_id(config_id: str) -> str:
    """将 review 池配置 ID 转为 extract 池 ID。"""
    if config_id.startswith("review_"):
        return "extract_" + config_id[len("review_"):]
    return config_id


def _dedupe_config_id(candidate: str, used_ids: set[str]) -> str:
    """在同一用途配置列表内生成不冲突的 ID。"""
    if candidate not in used_ids:
        return candidate
    base = f"{candidate}_migrated"
    if base not in used_ids:
        return base
    index = 2
    while f"{base}_{index}" in used_ids:
        index += 1
    return f"{base}_{index}"


def ensure_extract_pool_from_review(data: dict[str, Any]) -> bool:
    """若无 extract_configs，从 review 池深拷贝并改写为 extract 池。

    Returns:
        True 表示发生了迁移写入；False 表示已有 extract_configs 未改动。
    """
    if "extract_configs" in data:
        return False

    review_configs = data.get("review_configs", [])
    if not isinstance(review_configs, list):
        review_configs = []

    extract_configs: list[Any] = copy.deepcopy(review_configs)
    id_map: dict[str, str] = {}
    used_ids: set[str] = set()

    for config in extract_configs:
        if not isinstance(config, dict):
            continue

        caps = config.get("capabilities")
        if not isinstance(caps, dict):
            caps = {}
            config["capabilities"] = caps
        caps["thinking_policy"] = "disabled"

        old_id = str(config.get("id", "") or "")
        if not old_id:
            continue
        new_id = _dedupe_config_id(_to_extract_config_id(old_id), used_ids)
        config["id"] = new_id
        used_ids.add(new_id)
        id_map[old_id] = new_id

    def _map_id(config_id: str) -> str:
        return id_map.get(config_id, config_id)

    review_model_id = str(data.get("review_model_id", "") or "")
    data["extract_configs"] = extract_configs
    data["extract_model_id"] = _map_id(review_model_id) if review_model_id else ""

    review_pool_ids = data.get("review_pool_ids", [])
    if isinstance(review_pool_ids, list):
        data["extract_pool_ids"] = [_map_id(str(pool_id)) for pool_id in review_pool_ids]
    else:
        data["extract_pool_ids"] = []

    review_pool_priority = data.get("review_pool_priority", {})
    if isinstance(review_pool_priority, dict):
        data["extract_pool_priority"] = {
            _map_id(str(config_id)): priority
            for config_id, priority in review_pool_priority.items()
        }
    else:
        data["extract_pool_priority"] = {}

    return True
