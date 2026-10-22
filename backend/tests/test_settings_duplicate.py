"""模型配置复制功能测试。

覆盖目标:
- 基础复制：完整带过 api_key / api_keys / temperature 等敏感字段
- 跨用途复制：vision -> review，源用途不被污染
- 重名追加：自动 (副本) / (副本 N)
- 源不存在：抛 ValueError("源配置不存在 ...")，供 API 层映射 404
- 上限保护：达到 cloud=5 / local=2 上限时拒绝粘贴
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core import ai_settings
from app.core.ai_settings import (
    ApiKeyItem,
    ModelConfigItem,
    add_config,
    duplicate_config,
)


@pytest.fixture
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """重定向持久化路径到 tmp_path，并屏蔽 dispatcher / redis 副作用。"""
    settings_dir = tmp_path / "ai_settings_dir"
    settings_file = settings_dir / "ai_settings.json"
    monkeypatch.setattr(ai_settings, "SETTINGS_DIR", settings_dir)
    monkeypatch.setattr(ai_settings, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(ai_settings, "_sync_to_runtime", lambda *a, **kw: None)

    async def _noop_publish(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(ai_settings, "publish_runtime_invalidate", _noop_publish)
    return settings_file


def _build_cloud_config(
    purpose: str,
    *,
    config_id: str | None = None,
    model_name: str = "gemini-3-flash",
    api_base: str = "https://api.example.com",
) -> ModelConfigItem:
    return ModelConfigItem(
        id=config_id or f"{purpose}_seed_{model_name}",
        api_base=api_base,
        api_key="<TEST_API_KEY_PRIMARY>",
        api_keys=[
            ApiKeyItem(key="sk-extra-1", label="备1", enabled=True),
            ApiKeyItem(key="sk-extra-2", label="备2", enabled=False),
        ],
        model_name=model_name,
        config_type="cloud",
        temperature=0.2,
    )


@pytest.mark.asyncio
async def test_duplicate_basic_copies_secret_fields(isolated_settings):
    src = _build_cloud_config("review", config_id="review_src_1")
    await add_config("review", src)

    new = await duplicate_config("review", "review_src_1", "review")

    assert new.id != src.id
    assert new.model_name == "gemini-3-flash (副本)"
    assert new.api_base == src.api_base
    assert new.api_key == "<TEST_API_KEY_PRIMARY>"
    assert [k.key for k in new.api_keys] == ["sk-extra-1", "sk-extra-2"]
    assert [k.label for k in new.api_keys] == ["备1", "备2"]
    assert [k.enabled for k in new.api_keys] == [True, False]
    assert new.temperature == 0.2
    assert new.config_type == "cloud"


@pytest.mark.asyncio
async def test_duplicate_cross_purpose_does_not_pollute_source(isolated_settings):
    src = _build_cloud_config("vision", config_id="vision_src_1")
    await add_config("vision", src)

    new = await duplicate_config("vision", "vision_src_1", "review")

    state = ai_settings.load_ai_settings()
    vision_ids = {c.id for c in state.vision_configs}
    review_ids = {c.id for c in state.review_configs}

    assert "vision_src_1" in vision_ids, "源配置必须仍存在"
    assert new.id not in vision_ids, "新ID不应被插入到源用途"
    assert new.id in review_ids, "新ID必须出现在目标用途"

    pasted = next(c for c in state.review_configs if c.id == new.id)
    assert pasted.api_key == "<TEST_API_KEY_PRIMARY>"
    assert pasted.model_name == "gemini-3-flash"


@pytest.mark.asyncio
async def test_duplicate_rename_increments_when_collision(isolated_settings):
    src = _build_cloud_config("review", config_id="r1")
    await add_config("review", src)

    n1 = await duplicate_config("review", "r1", "review")
    n2 = await duplicate_config("review", "r1", "review")
    n3 = await duplicate_config("review", "r1", "review")

    assert n1.model_name == "gemini-3-flash (副本)"
    assert n2.model_name == "gemini-3-flash (副本 2)"
    assert n3.model_name == "gemini-3-flash (副本 3)"


@pytest.mark.asyncio
async def test_duplicate_source_not_found(isolated_settings):
    with pytest.raises(ValueError, match=r"^源配置不存在"):
        await duplicate_config("review", "nonexistent_id", "review")


@pytest.mark.asyncio
async def test_duplicate_respects_cloud_limit(isolated_settings):
    for i in range(5):
        await add_config(
            "review",
            _build_cloud_config(
                "review",
                config_id=f"r_{i}",
                model_name=f"m{i}",
                api_base=f"https://api.example{i}.com",
            ),
        )

    with pytest.raises(ValueError, match="云端模型配置已达上限"):
        await duplicate_config("review", "r_0", "review")
