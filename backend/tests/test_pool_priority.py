"""调度池优先级 5 档校验测试。

覆盖目标:
- 接受 5 个合法档位: 0/50/100/200/999
- 拒绝其他档位（如 25/75/300/1000）并提示允许集合
- 单点档位 (0/50/200/999) 各自最多 1 个，否则按档位中文名抛错
- 普通 (100) 不限数量，可多个共存
- 混合配置: 五档各占 1 个允许通过
- 非池内 ID 仍按既有约束拒绝
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
    set_pool_ids,
    set_pool_priority,
)


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

    try:
        from app.core.ai import ai_factory

        monkeypatch.setattr(ai_factory, "sync_channel_priorities", lambda purpose=None: None)
    except ImportError:
        pass
    return settings_file


def _make_cfg(idx: int) -> ModelConfigItem:
    return ModelConfigItem(
        id=f"cfg_{idx}",
        api_base=f"https://api.example{idx}.com",
        api_key="sk-test",
        api_keys=[ApiKeyItem(key="sk-extra", label="备", enabled=True)],
        model_name=f"m{idx}",
        config_type="cloud",
    )


async def _seed_pool(n: int) -> list[str]:
    """创建 n 个云端配置并加入审查池，返回 ID 列表。"""
    ids: list[str] = []
    for i in range(n):
        await add_config("review", _make_cfg(i))
        ids.append(f"cfg_{i}")
    await set_pool_ids("review", ids)
    return ids


@pytest.mark.asyncio
async def test_accepts_all_five_priority_levels(isolated_settings):
    """五档同时存在，每档各 1 个应通过。"""
    ids = await _seed_pool(5)
    priority = {
        ids[0]: 0,
        ids[1]: 50,
        ids[2]: 100,
        ids[3]: 200,
        ids[4]: 999,
    }
    state = await set_pool_priority("review", priority)
    assert state.get_pool_priority("review") == priority


@pytest.mark.asyncio
async def test_rejects_unknown_priority_value(isolated_settings):
    ids = await _seed_pool(2)
    bad = {ids[0]: 25, ids[1]: 100}
    with pytest.raises(ValueError, match="无效优先级"):
        await set_pool_priority("review", bad)


@pytest.mark.asyncio
async def test_rejects_duplicate_preferred(isolated_settings):
    ids = await _seed_pool(2)
    with pytest.raises(ValueError, match="默认首选最多设置1个"):
        await set_pool_priority("review", {ids[0]: 0, ids[1]: 0})


@pytest.mark.asyncio
async def test_rejects_duplicate_second(isolated_settings):
    ids = await _seed_pool(2)
    with pytest.raises(ValueError, match="次选最多设置1个"):
        await set_pool_priority("review", {ids[0]: 50, ids[1]: 50})


@pytest.mark.asyncio
async def test_rejects_duplicate_backup(isolated_settings):
    ids = await _seed_pool(2)
    with pytest.raises(ValueError, match="备用最多设置1个"):
        await set_pool_priority("review", {ids[0]: 200, ids[1]: 200})


@pytest.mark.asyncio
async def test_rejects_duplicate_last_resort(isolated_settings):
    ids = await _seed_pool(2)
    with pytest.raises(ValueError, match="末位备选最多设置1个"):
        await set_pool_priority("review", {ids[0]: 999, ids[1]: 999})


@pytest.mark.asyncio
async def test_normal_allows_multiple(isolated_settings):
    """普通档允许多个共存（自动负载均衡）。"""
    ids = await _seed_pool(3)
    priority = {ids[0]: 100, ids[1]: 100, ids[2]: 100}
    state = await set_pool_priority("review", priority)
    assert state.get_pool_priority("review") == priority


@pytest.mark.asyncio
async def test_rejects_id_not_in_pool(isolated_settings):
    ids = await _seed_pool(2)
    with pytest.raises(ValueError, match="配置ID不在调度池中"):
        await set_pool_priority(
            "review", {ids[0]: 50, "ghost_id": 100},
        )
