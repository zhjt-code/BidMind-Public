"""评分链调参面可枚举性护栏（治理阶段 5）。

职责: 让"改一个阈值会影响什么"可机械枚举，并禁止再出现同名不同义的阈值常量。
不做: 不改阈值取值、不强制把常量集中到单一模块。

为什么不做"物理集中到单一常量表":
  圆桌方案原拟把全部阈值原值搬入 scoring_tuning.py。实测该做法要让 20+ 个
  模块反向依赖一个中心模块，与"降低耦合、缩小爆炸半径"的首要目标相抵触，
  且搬移本身零行为收益却带来大面积改动。本测试用**机械清点 + 冲突禁入**
  达成同一目标（调参面可枚举、暗雷不可复现），改动面为零。

已消除的暗雷（本轮，仅改名不改值，T0 零差分佐证）:
  - `_CLOSURE_TOLERANCE` 曾在 3 个模块同名，取值 0.01 / 1.0 相差 100 倍；
    方案级闭合容差改名为 `_SCHEME_CLOSURE_TOLERANCE`。
  - `_WEIGHT_SUM_TOLERANCE` 曾同名于两处，量纲不同（权重小数和 vs 百分比份额和）；
    后者改名为 `_SHARE_PERCENT_SUM_TOLERANCE`。
"""
from __future__ import annotations

import ast
import collections
import os
import pathlib

import pytest

pytestmark = pytest.mark.scoring

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-key-not-for-prod")
os.environ.setdefault("ADMIN_PASSWORD", "<TEST_PASSWORD>")

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCORING_DIR = BACKEND_ROOT / "app/core/extractor/scoring"
PARSER_DIR = BACKEND_ROOT / "app/core/parser"

_THRESHOLD_SUFFIXES = (
    "THRESHOLD", "TOLERANCE", "RATIO", "MIN", "MAX", "LIMIT", "EPS", "CUTOFF", "MARGIN",
)

# 允许同名不同值的例外（必须写明理由）；空表示"零例外"。
_SAME_NAME_DIFFERENT_VALUE_ALLOWLIST: dict[str, str] = {}


def _is_threshold_name(name: str) -> bool:
    stripped = name.lstrip("_")
    if not stripped or not stripped.isupper():
        return False
    return any(stripped.endswith(suffix) for suffix in _THRESHOLD_SUFFIXES)


def _iter_scoring_modules() -> list[pathlib.Path]:
    files = list(SCORING_DIR.rglob("*.py"))
    files += [p for p in PARSER_DIR.rglob("*.py") if p.name.startswith("scoring")]
    return sorted(f for f in files if "__pycache__" not in str(f))


def collect_thresholds() -> dict[str, list[tuple[str, float]]]:
    """机械清点评分链模块级阈值常量：{常量名: [(模块名, 取值), ...]}。"""
    found: dict[str, list[tuple[str, float]]] = collections.defaultdict(list)
    for path in _iter_scoring_modules():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name) or not _is_threshold_name(target.id):
                    continue
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, SyntaxError):
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                found[target.id].append((path.name, float(value)))
    return dict(found)


class TestTuningSurfaceIsEnumerable:
    """调参面必须可机械枚举，且不得出现同名不同义常量。"""

    def test_threshold_inventory_is_non_empty(self) -> None:
        """清点器本身必须有效——若返回空，说明命名约定已变，护栏失效。"""
        inventory = collect_thresholds()
        assert len(inventory) >= 10, (
            f"仅清点到 {len(inventory)} 个阈值常量，疑似命名约定变更导致护栏空转"
        )

    def test_no_same_name_different_value_thresholds(self) -> None:
        """同名阈值在不同模块取值必须一致，否则读代码的人会按错误直觉推断。

        这是真实暗雷: `_CLOSURE_TOLERANCE` 曾以 0.01 与 1.0 两个取值同名存在，
        差 100 倍且语义不同（行级父子闭合 vs 方案级百分制闭合）。
        """
        conflicts: list[str] = []
        for name, occurrences in sorted(collect_thresholds().items()):
            values = {value for _, value in occurrences}
            if len(values) <= 1 or name in _SAME_NAME_DIFFERENT_VALUE_ALLOWLIST:
                continue
            detail = ", ".join(f"{mod}={val}" for mod, val in occurrences)
            conflicts.append(f"{name}: {detail}")
        assert not conflicts, (
            "以下阈值常量同名但取值不同，必须按语义改名消歧（仅改名不改值）:\n  "
            + "\n  ".join(conflicts)
        )

    def test_scoring_feature_flags_are_declared_in_settings(self) -> None:
        """评分链 feature flag 必须在 Settings 显式声明，禁止只靠 getattr 兜底。

 只靠 getattr 兜底的开关一旦声明被删就静默改变行为（同族）。
 """
        from app.core.config import Settings

        required = (
            "SCORING_GROUP_ANCHOR_ENABLED",
            "SCORING_STUB_RECOVERY_ENABLED",
            "SCORING_STRUCTURE_SIGNALS_ENABLED",
            "TENDER_SCORING_EXTRACT_ENABLED",
        )
        missing = [name for name in required if name not in Settings.model_fields]
        assert not missing, f"以下评分开关未在 Settings 声明: {missing}"

    def test_getattr_flag_defaults_match_settings_defaults(self) -> None:
        """getattr 兜底默认值必须与 Settings 声明一致，避免"删声明即变行为"。"""
        from app.core.config import Settings

        pattern_files = {
            "SCORING_GROUP_ANCHOR_ENABLED": PARSER_DIR / "scoring_chunk_builder.py",
            "SCORING_STUB_RECOVERY_ENABLED": PARSER_DIR / "scoring_chunk_sources.py",
        }
        for flag, path in pattern_files.items():
            if not path.exists():
                continue
            source = path.read_text(encoding="utf-8")
            if f'"{flag}"' not in source:
                continue
            declared = Settings.model_fields[flag].default
            # getattr(settings, "FLAG", <default>) 中的 <default> 必须等于声明默认值
            assert f'"{flag}", {declared}' in source.replace("'", '"'), (
                f"{path.name} 中 {flag} 的 getattr 兜底默认值与 Settings 声明({declared})不一致"
            )
