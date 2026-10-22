"""extract 池从 review 克隆迁移与 AISettingsModel 四路分支测试。

覆盖目标:
- ensure_extract_pool_from_review: ID 前缀 extract_、thinking_policy=disabled、幂等。
- AISettingsModel.get/set_pool_ids("extract") 与未知 purpose 抛 ValueError。
- get_extract_client 在 extract 空池时回落 _raw_review_client。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai_settings import AISettingsModel
from app.core.ai_settings_extract_migrate import ensure_extract_pool_from_review


def _review_config(config_id: str, model_name: str, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": config_id,
        "api_base": "https://api.example.com",
        "api_key": "sk-test",
        "model_name": model_name,
        "config_type": "cloud",
    }
    item.update(extra)
    return item


def test_ensure_extract_pool_clones_review_with_extract_prefix_and_disabled_thinking() -> None:
    """仅有 review 时克隆出 extract_*，且 thinking_policy 全部为 disabled。"""
    data: dict[str, Any] = {
        "vision_configs": [],
        "review_configs": [
            _review_config(
                "review_cloud_100",
                "qwen-max",
                capabilities={"thinking_policy": "auto"},
            ),
            _review_config("review_200", "deepseek-chat"),
        ],
        "review_model_id": "review_cloud_100",
        "review_pool_ids": ["review_cloud_100", "review_200"],
        "review_pool_priority": {"review_cloud_100": 0, "review_200": 100},
    }
    original_review = copy.deepcopy(data["review_configs"])

    changed = ensure_extract_pool_from_review(data)
    assert changed is True

    extract_configs = cast(list[dict[str, Any]], data["extract_configs"])
    assert [cfg["id"] for cfg in extract_configs] == [
        "extract_cloud_100",
        "extract_200",
    ]
    assert data["extract_model_id"] == "extract_cloud_100"
    assert data["extract_pool_ids"] == ["extract_cloud_100", "extract_200"]
    assert data["extract_pool_priority"] == {
        "extract_cloud_100": 0,
        "extract_200": 100,
    }
    for cfg in extract_configs:
        caps = cfg.get("capabilities")
        assert isinstance(caps, dict)
        assert caps["thinking_policy"] == "disabled"

    # review 池不被污染
    assert data["review_configs"] == original_review

    # 幂等：再次调用返回 False 且不改写
    snapshot = copy.deepcopy(data)
    changed_again = ensure_extract_pool_from_review(data)
    assert changed_again is False
    assert data == snapshot


def test_ai_settings_model_extract_pool_ids_get_set_and_unknown_purpose() -> None:
    """get/set_pool_ids('extract') 读写 extract 池；未知 purpose 抛 ValueError。"""
    model = AISettingsModel(
        extract_pool_ids=["extract_a"],
        review_pool_ids=["review_a"],
    )

    assert model.get_pool_ids("extract") == ["extract_a"]
    model.set_pool_ids("extract", ["extract_b", "extract_c"])
    assert model.extract_pool_ids == ["extract_b", "extract_c"]
    assert model.get_pool_ids("extract") == ["extract_b", "extract_c"]
    # 其它池不受影响
    assert model.review_pool_ids == ["review_a"]

    with pytest.raises(ValueError, match="未知 purpose"):
        model.get_pool_ids("unknown")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="未知 purpose"):
        model.set_pool_ids("unknown", ["x"])  # type: ignore[arg-type]


def test_get_extract_client_falls_back_to_raw_review_when_extract_empty() -> None:
    """extract dispatcher 无通道时 get_extract_client 回落 _raw_review_client。"""
    from app.core.ai import ai_factory

    sentinel = object()
    empty_dispatcher = SimpleNamespace(channel_count=0)

    with patch.object(ai_factory, "_extract_dispatcher", empty_dispatcher):
        with patch.object(
            ai_factory, "_raw_review_client", return_value=sentinel,
        ) as mock_raw:
            result = ai_factory.get_extract_client(project_id=42)

    assert result is sentinel
    mock_raw.assert_called_once_with(42)
