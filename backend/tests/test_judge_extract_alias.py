"""复审客户端并入提取池的工厂契约。

覆盖目标:
- extract 有通道时 get_judge_client 返回提取调度器（含 project 绑定）
- extract 空池时返回 None，且不回落审查、不使用 judge 调度器
- get_judge_dispatcher 与 extract 调度器为同一实例
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai import ai_factory
from app.core.ai.ai_factory import ProjectBoundDispatcher


def test_get_judge_client_uses_extract_dispatcher_when_extract_has_channels() -> None:
    extract_disp = SimpleNamespace(channel_count=2)
    judge_disp = SimpleNamespace(channel_count=5)

    with patch.object(ai_factory, "_extract_dispatcher", extract_disp):
        with patch.object(ai_factory, "_judge_dispatcher", judge_disp):
            with patch.object(ai_factory, "_raw_review_client") as mock_raw:
                result = ai_factory.get_judge_client()

    assert result is extract_disp
    mock_raw.assert_not_called()


def test_get_judge_client_binds_project_to_extract_dispatcher() -> None:
    extract_disp = SimpleNamespace(channel_count=1)

    with patch.object(ai_factory, "_extract_dispatcher", extract_disp):
        result = ai_factory.get_judge_client(project_id=42)

    assert isinstance(result, ProjectBoundDispatcher)
    assert result._dispatcher is extract_disp
    assert result._project_id == 42


def test_get_judge_client_returns_none_when_extract_empty_and_does_not_fallback() -> None:
    empty_extract = SimpleNamespace(channel_count=0)
    judge_disp = SimpleNamespace(channel_count=3)

    with patch.object(ai_factory, "_extract_dispatcher", empty_extract):
        with patch.object(ai_factory, "_judge_dispatcher", judge_disp):
            with patch.object(ai_factory, "_raw_review_client") as mock_raw:
                with patch.object(ai_factory, "get_client_by_config_id") as mock_by_id:
                    result = ai_factory.get_judge_client(project_id=7)

    assert result is None
    mock_raw.assert_not_called()
    mock_by_id.assert_not_called()


def test_get_judge_dispatcher_aliases_extract_dispatcher() -> None:
    extract_disp = SimpleNamespace(channel_count=2)
    judge_disp = SimpleNamespace(channel_count=9)

    with patch.object(ai_factory, "_extract_dispatcher", extract_disp):
        with patch.object(ai_factory, "_judge_dispatcher", judge_disp):
            assert ai_factory.get_judge_dispatcher() is extract_disp
