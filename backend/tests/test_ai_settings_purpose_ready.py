"""用途就绪判定：调度池是权威，遗留单选 ID 只是影子。

夹具覆盖同形态两例：vision 与 scoring（池满、单选为空）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.scoring

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core import ai_settings
from app.core.ai_settings import (
    AISettingsModel,
    ModelConfigItem,
    add_config,
    delete_config,
    load_ai_settings,
    set_model_id,
    set_pool_ids,
)
from app.core.ai_settings_purpose_ready import (
    purpose_has_models,
    sync_all_legacy_model_ids,
    sync_legacy_model_id,
)
from app.core.parser.image_recognition_service import get_vl_deps


def _cfg(purpose: str, config_id: str, name: str) -> ModelConfigItem:
    return ModelConfigItem(
        id=config_id,
        api_base="https://api.example.com/v1",
        api_key="sk-test",
        model_name=name,
        config_type="cloud",
    )


def _pool_model(
    purpose: str,
    *,
    ids: list[str],
    model_id: str = "",
    priority: dict[str, int] | None = None,
) -> AISettingsModel:
    configs = [_cfg(purpose, cid, f"model-{cid}") for cid in ids]
    kwargs: dict = {
        f"{purpose}_configs": configs,
        f"{purpose}_pool_ids": list(ids),
        f"{purpose}_model_id": model_id,
        f"{purpose}_pool_priority": priority or {},
    }
    return AISettingsModel(**kwargs)


@pytest.fixture
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    settings_dir = tmp_path / "ai_settings_dir"
    settings_file = settings_dir / "ai_settings.json"
    monkeypatch.setattr(ai_settings, "SETTINGS_DIR", settings_dir)
    monkeypatch.setattr(ai_settings, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(ai_settings, "_sync_to_runtime", lambda *a, **kw: None)

    async def _noop_publish(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(ai_settings, "publish_runtime_invalidate", _noop_publish)
    return settings_file


def test_purpose_has_models_when_vision_pool_filled_and_legacy_id_empty() -> None:
    model = _pool_model("vision", ids=["vision_a", "vision_b"], model_id="")
    assert purpose_has_models(model, "vision") is True


def test_purpose_has_models_when_scoring_pool_filled_and_legacy_id_empty() -> None:
    model = _pool_model("scoring", ids=["scoring_a"], model_id="")
    assert purpose_has_models(model, "scoring") is True


def test_purpose_has_models_false_when_pool_and_id_empty() -> None:
    model = AISettingsModel()
    assert purpose_has_models(model, "vision") is False
    assert purpose_has_models(model, "scoring") is False


def test_purpose_has_models_true_when_pool_empty_but_legacy_id_valid() -> None:
    model = _pool_model("review", ids=["review_solo"], model_id="review_solo")
    model.review_pool_ids = []
    assert purpose_has_models(model, "review") is True


def test_purpose_has_models_false_when_pool_ids_stale() -> None:
    model = AISettingsModel(
        vision_pool_ids=["vision_gone"],
        vision_model_id="",
        vision_configs=[],
    )
    assert purpose_has_models(model, "vision") is False


def test_sync_promotes_preferred_pool_member() -> None:
    model = _pool_model(
        "vision",
        ids=["vision_second", "vision_pref"],
        model_id="",
        priority={"vision_pref": 0, "vision_second": 50},
    )
    assert sync_legacy_model_id(model, "vision") is True
    assert model.vision_model_id == "vision_pref"


def test_sync_all_covers_scoring_as_second_purpose() -> None:
    model = _pool_model("scoring", ids=["scoring_pref"], model_id="")
    changed = sync_all_legacy_model_ids(model)
    assert "scoring" in changed
    assert model.scoring_model_id == "scoring_pref"


def test_sync_keeps_valid_legacy_id_when_pool_emptied() -> None:
    model = _pool_model("extract", ids=["extract_keep"], model_id="extract_keep")
    model.extract_pool_ids = []
    assert sync_legacy_model_id(model, "extract") is False
    assert model.extract_model_id == "extract_keep"


def test_sync_clears_dangling_legacy_id() -> None:
    model = AISettingsModel(vision_model_id="vision_deleted", vision_configs=[])
    assert sync_legacy_model_id(model, "vision") is True
    assert model.vision_model_id == ""


def test_get_vl_deps_available_when_vision_pool_filled_and_legacy_id_empty() -> None:
    settings = _pool_model("vision", ids=["vision_cloud_1"], model_id="")
    mock_client = MagicMock()
    fake_dispatcher = MagicMock(channel_count=1)
    fake_dispatcher.get_primary_group_channel_count.return_value = 1

    with (
        patch("app.core.ai_settings.load_ai_settings", return_value=settings),
        patch(
            "app.core.ai.ai_factory.get_vision_dispatcher",
            return_value=fake_dispatcher,
        ),
        patch("app.core.ai.get_vision_client", return_value=mock_client),
        patch(
            "app.core.parser.adaptive_concurrency.AdaptiveConcurrency",
            return_value=MagicMock(current=1),
        ),
    ):
        deps = get_vl_deps(purpose="文档处理")

    assert deps.available is True
    assert deps.vision_client is mock_client


@pytest.mark.asyncio
async def test_delete_config_promotes_remaining_vision_pool_member(isolated_settings):
    await add_config("vision", _cfg("vision", "vision_old", "old-vl"))
    await add_config("vision", _cfg("vision", "vision_keep", "keep-vl"))
    await set_pool_ids("vision", ["vision_old", "vision_keep"])
    await set_model_id("vision", "vision_old")

    updated = await delete_config("vision", "vision_old")

    assert updated.vision_model_id == "vision_keep"
    assert "vision_old" not in updated.vision_pool_ids


@pytest.mark.asyncio
async def test_delete_config_promotes_remaining_scoring_pool_member(isolated_settings):
    await add_config("scoring", _cfg("scoring", "scoring_old", "old-score"))
    await add_config("scoring", _cfg("scoring", "scoring_keep", "keep-score"))
    await set_pool_ids("scoring", ["scoring_old", "scoring_keep"])
    await set_model_id("scoring", "scoring_old")

    updated = await delete_config("scoring", "scoring_old")

    assert updated.scoring_model_id == "scoring_keep"


@pytest.mark.asyncio
async def test_load_heals_empty_legacy_id_from_pool(isolated_settings):
    await add_config("vision", _cfg("vision", "vision_heal", "heal-vl"))
    await set_pool_ids("vision", ["vision_heal"])
    loaded = load_ai_settings()
    loaded.vision_model_id = ""
    ai_settings._save_ai_settings_sync(loaded)

    healed = load_ai_settings()
    assert healed.vision_model_id == "vision_heal"


@pytest.mark.asyncio
async def test_load_heals_empty_scoring_legacy_id_from_pool(isolated_settings):
    await add_config("scoring", _cfg("scoring", "scoring_heal", "heal-score"))
    await set_pool_ids("scoring", ["scoring_heal"])
    loaded = load_ai_settings()
    loaded.scoring_model_id = ""
    ai_settings._save_ai_settings_sync(loaded)

    healed = load_ai_settings()
    assert healed.scoring_model_id == "scoring_heal"
