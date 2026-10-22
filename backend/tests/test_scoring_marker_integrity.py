"""评分回归入口完整性元测试（机械护栏）。

职责: 机械推导「哪些测试属于评分域」，并强制其携带 `scoring` marker，
 使 `pytest -m scoring` 成为可信的评分域全量入口。
不做: 不断言业务行为、不导入被测模块、不做性能判断。

背景:
 此前回归入口为 `pytest -k "scoring"`，按测试项**名词面**匹配。
 实测有 19 个消费评分链但文件名不含 scoring 的测试文件被静默剔除
 （如 test_table_scope_resolution_bug798.py 37 个用例 0 入选），
 缺口正压在作用域/总分对账/列角色三条历史复发链上。

失效模式与 同构（fixture 漏出 SAMPLE_KEYS 致期望成为从未执行的死代码）：
 靠人工维护的清单会静默漏项，必须由元测试机械自检。

归属判据（三者取并集，均为机械推导，无人工清单）:
 1. 静态 import 了评分链模块（含函数内 import）；
 2. 文件名包含 "scoring"；
 3. 含名称包含 "scoring" 的测试函数（覆盖评分产出的下游消费者，
 如报告契约、requirement API 契约、writer 指令链）。

判据 3 保证 `-m scoring` 是旧入口 `-k "scoring"` 的**超集**，
即换入口只会多跑不会少跑，由 test_marker_domain_superset_of_keyword_match 锁定。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

pytestmark = pytest.mark.scoring

TESTS_ROOT = pathlib.Path(__file__).resolve().parent

# 评分链模块前缀（单点定义，新增评分子包时在此扩展）
SCORING_MODULE_PREFIXES: tuple[str, ...] = (
    "app.core.extractor.scoring",
    "app.core.parser.scoring_",
    "app.core.ai.prompts.scoring_",
    "app.core.ai.prompts.scheme_reader",
)
# services 层按模块名含 scoring 判定（scoring_ledger_report_service 等）
SCORING_SERVICE_PREFIX = "app.services."
SCORING_SERVICE_HINT = "scoring"

MARKER_NAME = "scoring"


def _iter_test_files() -> list[pathlib.Path]:
    return sorted(TESTS_ROOT.rglob("test_*.py"))


def _parse(path: pathlib.Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return None


def _imported_modules(tree: ast.Module) -> set[str]:
    """收集全部 import 的模块名（含函数内 import，故用 ast.walk）。"""
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _imports_scoring_chain(modules: set[str]) -> bool:
    for module in modules:
        if any(
            module == prefix.rstrip(".") or module.startswith(prefix)
            for prefix in SCORING_MODULE_PREFIXES
        ):
            return True
        if module.startswith(SCORING_SERVICE_PREFIX) and SCORING_SERVICE_HINT in module:
            return True
    return False


def _has_scoring_named_test(tree: ast.Module) -> bool:
    """是否存在名称含 scoring 的测试函数（旧 `-k scoring` 的命中形态）。"""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test") and SCORING_SERVICE_HINT in node.name:
                return True
        elif isinstance(node, ast.ClassDef) and SCORING_SERVICE_HINT in node.name.lower():
            return True
    return False


def _belongs_to_scoring_domain(path: pathlib.Path, tree: ast.Module) -> bool:
    if SCORING_SERVICE_HINT in path.name:
        return True
    if _imports_scoring_chain(_imported_modules(tree)):
        return True
    return _has_scoring_named_test(tree)


def _has_scoring_marker(tree: ast.Module) -> bool:
    """检测模块级 pytestmark 是否含 scoring marker（标量或列表形式均可）。"""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "pytestmark"
            for target in node.targets
        ):
            continue
        if f"mark.{MARKER_NAME}" in ast.unparse(node.value):
            return True
    return False


def _collect_domain_files() -> list[pathlib.Path]:
    files = []
    for path in _iter_test_files():
        tree = _parse(path)
        if tree is None:
            continue
        if _belongs_to_scoring_domain(path, tree):
            files.append(path)
    return files


class TestScoringMarkerIntegrity:
    """机械保证评分域测试全部纳入 `-m scoring` 入口。"""

    def test_every_scoring_domain_file_carries_marker(self) -> None:
        """凡消费评分链或文件名含 scoring 的测试文件，必须携带 scoring marker。

 新增评分测试忘记贴标时本测试失败——这正是 缺失的机械护栏。
 """
        missing: list[str] = []
        for path in _collect_domain_files():
            tree = _parse(path)
            if tree is None:
                continue
            if not _has_scoring_marker(tree):
                missing.append(str(path.relative_to(TESTS_ROOT)))

        assert not missing, (
            "以下测试文件属评分域但未贴 scoring marker，"
            "将被 `-m scoring` 入口漏掉（同类）:\n "
            + "\n  ".join(missing)
        )

    def test_domain_detection_covers_known_name_miss_files(self) -> None:
        """判据必须覆盖 中被词面过滤漏掉的代表文件。

 这些文件名不含 scoring，是 `-k "scoring"` 漏选的直接证据；
 若判据退化导致它们不再被识别为评分域，护栏即失效。
 """
        sentinels = (
            "test_table_scope_resolution_bug798.py",
            "test_score_totals_shared_report.py",
            "test_volume_parser.py",
            "test_row_group_attribution.py",
            "test_group_column.py",
            "test_scheme_segmentation.py",
        )
        domain_names = {p.name for p in _collect_domain_files()}
        for name in sentinels:
            if not (TESTS_ROOT / name).exists():
                continue
            assert name in domain_names, (
                f"{name} 未被判定为评分域，归属判据已退化（复发风险）"
            )

    def test_marker_domain_superset_of_keyword_match(self) -> None:
        """`-m scoring` 域必须覆盖旧 `-k "scoring"` 会命中的每个文件。

 换入口只允许多跑、不允许少跑；否则本次治理会把 从
 「漏选」变形为「换了个方式漏选」。
 """
        marked = {p for p in _collect_domain_files()}
        keyword_hits: list[str] = []
        for path in _iter_test_files():
            tree = _parse(path)
            if tree is None:
                continue
            if SCORING_SERVICE_HINT in path.name or _has_scoring_named_test(tree):
                if path not in marked:
                    keyword_hits.append(str(path.relative_to(TESTS_ROOT)))

        assert not keyword_hits, (
            "以下文件会被旧入口 -k \"scoring\" 命中但不在 -m scoring 域内:\n  "
            + "\n  ".join(keyword_hits)
        )

    def test_scoring_marker_is_registered(self) -> None:
        """scoring marker 必须在 pyproject.toml 注册，避免 PytestUnknownMarkWarning。"""
        pyproject = TESTS_ROOT.parent / "pyproject.toml"
        assert pyproject.exists(), f"未找到 {pyproject}"
        content = pyproject.read_text(encoding="utf-8")
        assert f'"{MARKER_NAME}:' in content, (
            "pyproject.toml 的 [tool.pytest.ini_options].markers 未注册 scoring marker"
        )
