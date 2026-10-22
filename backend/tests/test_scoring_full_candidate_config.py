"""Task-2 RED: L3 专用配置模块测试。

职责: 验证 ScoringFullCandidateSettings 默认值、非法值拒绝、.env.example 完整性与 config.py 不变性。
不做: 不测试 LLM、数据库、API 路由或提取流程。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.scoring

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

_BACKEND_DIR = Path(__file__).parent.parent
_ENV_EXAMPLE = _BACKEND_DIR / ".env.example"
_CONFIG_PY = _BACKEND_DIR / "app" / "core" / "config.py"

_REQUIRED_ENV_KEYS = [
    "SCORING_FULL_CANDIDATE_MODE",
    "SCORING_FULL_CANDIDATE_CONTEXT_CHAR_BUDGET",
    "SCORING_FULL_CANDIDATE_MAX_TOKENS",
    "SCORING_FULL_CANDIDATE_NEIGHBOR_CHUNKS",
    "SCORING_FULL_CANDIDATE_MAX_SCOPES",
    "SCORING_FULL_CANDIDATE_TIMEOUT_S",
]


def _make_settings(**overrides):
    """从关键字参数创建 settings，不依赖环境变量。"""
    from app.core.scoring_full_candidate_config import ScoringFullCandidateSettings
    return ScoringFullCandidateSettings(**overrides)


# ---------------------------------------------------------------------------
# A. 默认值精确性
# ---------------------------------------------------------------------------


class TestDefaultValues:
    def test_default_mode_is_shadow(self) -> None:
        s = _make_settings()
        assert s.SCORING_FULL_CANDIDATE_MODE == "shadow"

    def test_default_char_budget(self) -> None:
        s = _make_settings()
        assert s.SCORING_FULL_CANDIDATE_CONTEXT_CHAR_BUDGET == 24000

    def test_default_max_tokens(self) -> None:
        s = _make_settings()
        assert s.SCORING_FULL_CANDIDATE_MAX_TOKENS == 5000

    def test_default_neighbor_chunks(self) -> None:
        s = _make_settings()
        assert s.SCORING_FULL_CANDIDATE_NEIGHBOR_CHUNKS == 2

    def test_default_max_scopes(self) -> None:
        s = _make_settings()
        assert s.SCORING_FULL_CANDIDATE_MAX_SCOPES == 3

    def test_default_timeout(self) -> None:
        s = _make_settings()
        assert s.SCORING_FULL_CANDIDATE_TIMEOUT_S == 180.0

    def test_module_exports_settings_instance(self) -> None:
        from app.core.scoring_full_candidate_config import scoring_full_candidate_settings
        assert scoring_full_candidate_settings is not None
        assert scoring_full_candidate_settings.SCORING_FULL_CANDIDATE_MODE == "shadow"


# ---------------------------------------------------------------------------
# B. 非法值拒绝
# ---------------------------------------------------------------------------


class TestInvalidValues:
    def test_invalid_mode_rejected(self) -> None:
        with pytest.raises((ValidationError, ValueError)):
            _make_settings(SCORING_FULL_CANDIDATE_MODE="active")

    def test_mode_empty_rejected(self) -> None:
        with pytest.raises((ValidationError, ValueError)):
            _make_settings(SCORING_FULL_CANDIDATE_MODE="")

    def test_char_budget_zero_rejected(self) -> None:
        with pytest.raises((ValidationError, ValueError)):
            _make_settings(SCORING_FULL_CANDIDATE_CONTEXT_CHAR_BUDGET=0)

    def test_char_budget_negative_rejected(self) -> None:
        with pytest.raises((ValidationError, ValueError)):
            _make_settings(SCORING_FULL_CANDIDATE_CONTEXT_CHAR_BUDGET=-1)

    def test_max_tokens_zero_rejected(self) -> None:
        with pytest.raises((ValidationError, ValueError)):
            _make_settings(SCORING_FULL_CANDIDATE_MAX_TOKENS=0)

    def test_max_tokens_negative_rejected(self) -> None:
        with pytest.raises((ValidationError, ValueError)):
            _make_settings(SCORING_FULL_CANDIDATE_MAX_TOKENS=-100)

    def test_max_scopes_zero_rejected(self) -> None:
        with pytest.raises((ValidationError, ValueError)):
            _make_settings(SCORING_FULL_CANDIDATE_MAX_SCOPES=0)

    def test_timeout_zero_rejected(self) -> None:
        with pytest.raises((ValidationError, ValueError)):
            _make_settings(SCORING_FULL_CANDIDATE_TIMEOUT_S=0.0)

    def test_timeout_negative_rejected(self) -> None:
        with pytest.raises((ValidationError, ValueError)):
            _make_settings(SCORING_FULL_CANDIDATE_TIMEOUT_S=-1.0)

    def test_neighbor_negative_rejected(self) -> None:
        with pytest.raises((ValidationError, ValueError)):
            _make_settings(SCORING_FULL_CANDIDATE_NEIGHBOR_CHUNKS=-1)

    def test_neighbor_zero_allowed(self) -> None:
        # neighbor=0 表示不包含邻居，合法
        s = _make_settings(SCORING_FULL_CANDIDATE_NEIGHBOR_CHUNKS=0)
        assert s.SCORING_FULL_CANDIDATE_NEIGHBOR_CHUNKS == 0


# ---------------------------------------------------------------------------
# C. 有效 mode 值
# ---------------------------------------------------------------------------


class TestValidModeValues:
    def test_mode_off(self) -> None:
        s = _make_settings(SCORING_FULL_CANDIDATE_MODE="off")
        assert s.SCORING_FULL_CANDIDATE_MODE == "off"

    def test_mode_shadow(self) -> None:
        s = _make_settings(SCORING_FULL_CANDIDATE_MODE="shadow")
        assert s.SCORING_FULL_CANDIDATE_MODE == "shadow"

    def test_mode_enforce(self) -> None:
        s = _make_settings(SCORING_FULL_CANDIDATE_MODE="enforce")
        assert s.SCORING_FULL_CANDIDATE_MODE == "enforce"


# ---------------------------------------------------------------------------
# D. .env.example 完整性
# ---------------------------------------------------------------------------


class TestEnvExample:
    def test_env_example_exists(self) -> None:
        assert _ENV_EXAMPLE.exists(), f".env.example 不存在: {_ENV_EXAMPLE}"

    def test_all_six_keys_present(self) -> None:
        content = _ENV_EXAMPLE.read_text(encoding="utf-8", errors="replace")
        missing = [key for key in _REQUIRED_ENV_KEYS if key not in content]
        assert not missing, f".env.example 缺少以下键: {missing}"

    def test_example_defaults_to_shadow(self) -> None:
        content = _ENV_EXAMPLE.read_text(encoding="utf-8", errors="replace")
        assert "SCORING_FULL_CANDIDATE_MODE=shadow" in content

    def test_no_real_secrets_in_example(self) -> None:
        content = _ENV_EXAMPLE.read_text(encoding="utf-8", errors="replace")
        secret_markers = ("sk-", "bearer ", "eyj")
        for key in _REQUIRED_ENV_KEYS:
            for line in content.splitlines():
                if line.strip().startswith(key):
                    value = line.split("=", 1)[1].strip().lower()
                    assert not any(marker in value for marker in secret_markers)
                    break


# ---------------------------------------------------------------------------
# E. config.py 未修改
# ---------------------------------------------------------------------------


class TestConfigPyUnmodified:
    def test_config_py_exists(self) -> None:
        assert _CONFIG_PY.exists()

    def test_config_py_does_not_contain_full_candidate_settings(self) -> None:
        content = _CONFIG_PY.read_text(encoding="utf-8", errors="replace")
        # 确认 SCORING_FULL_CANDIDATE_MODE 未写入 config.py
        assert "SCORING_FULL_CANDIDATE_MODE" not in content
        assert "SCORING_FULL_CANDIDATE_CONTEXT_CHAR_BUDGET" not in content

    def test_config_py_line_count_intact(self) -> None:
        lines = _CONFIG_PY.read_text(encoding="utf-8", errors="replace").splitlines()
        # 原文件约 910 行，新增配置行未追加进去
        assert len(lines) >= 900, f"config.py 行数异常: {len(lines)}"
        assert "SCORING_FULL_CANDIDATE_MAX_TOKENS" not in "\n".join(lines)
