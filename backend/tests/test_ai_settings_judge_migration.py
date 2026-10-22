"""AI 设置 judge 历史配置迁移测试。

覆盖目标:
- 已存在 judge_configs 时, 历史克隆出的 review_* ID 必须规范化为 judge_*。
- 首次从 review_configs 克隆 judge_configs 时, 也必须生成 judge_* ID。
- review 池自身配置不应被迁移过程污染。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai_settings import _migrate_old_format


def _review_config(config_id: str, model_name: str) -> dict[str, Any]:
    return {
        "id": config_id,
        "api_base": "https://api.example.com",
        "api_key": "sk-test",
        "model_name": model_name,
        "config_type": "cloud",
    }


def test_migrate_existing_judge_review_prefix_ids_to_judge_prefix() -> None:
    """历史 judge 池中的 review_* ID 应整体改写为 judge_* 并同步引用字段。"""
    data: dict[str, Any] = {
        "vision_configs": [],
        "review_configs": [
            _review_config("review_cloud_100", "qwen-max"),
        ],
        "review_model_id": "review_cloud_100",
        "review_pool_ids": ["review_cloud_100"],
        "review_pool_priority": {"review_cloud_100": 0},
        "judge_configs": [
            _review_config("review_cloud_100", "gemini-3.5-flash"),
            _review_config("review_200", "gpt-5-mini"),
        ],
        "judge_model_id": "review_cloud_100",
        "judge_pool_ids": ["review_cloud_100", "review_200"],
        "judge_pool_priority": {"review_cloud_100": 0, "review_200": 100},
    }
    original_review_configs = copy.deepcopy(data["review_configs"])

    migrated = _migrate_old_format(data)
    judge_configs = cast(list[dict[str, Any]], migrated["judge_configs"])

    assert migrated["review_configs"] == original_review_configs
    assert [config["id"] for config in judge_configs] == [
        "judge_cloud_100",
        "judge_200",
    ]
    assert migrated["judge_model_id"] == "judge_cloud_100"
    assert migrated["judge_pool_ids"] == ["judge_cloud_100", "judge_200"]
    assert migrated["judge_pool_priority"] == {
        "judge_cloud_100": 0,
        "judge_200": 100,
    }
    assert judge_configs[0]["model_name"] == "gemini-3.5-flash"


def test_migrate_initial_judge_clone_uses_judge_prefix_ids() -> None:
    """首次克隆 review 配置到 judge 池时不得保留 review_* ID。"""
    data: dict[str, Any] = {
        "vision_configs": [],
        "review_configs": [
            _review_config("review_cloud_100", "qwen-max"),
            _review_config("review_200", "deepseek-chat"),
        ],
        "review_model_id": "review_cloud_100",
        "review_pool_ids": ["review_cloud_100", "review_200"],
        "review_pool_priority": {"review_cloud_100": 0, "review_200": 100},
    }

    migrated = _migrate_old_format(data)
    judge_configs = cast(list[dict[str, Any]], migrated["judge_configs"])

    assert [config["id"] for config in judge_configs] == [
        "judge_cloud_100",
        "judge_200",
    ]
    assert migrated["judge_model_id"] == "judge_cloud_100"
    assert migrated["judge_pool_ids"] == ["judge_cloud_100", "judge_200"]
    assert migrated["judge_pool_priority"] == {
        "judge_cloud_100": 0,
        "judge_200": 100,
    }
