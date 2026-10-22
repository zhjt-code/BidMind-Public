"""契约护栏: 约束解码链路必须接入输入超长 400 兜底。

根治"修一处漏一处": pair_review/cove/judge 都曾走 chat_json(json_schema=) 约束解码
却未接 input_guard, 导致超长输入静默失败。本测试用 AST 扫描 backend/app 下所有
`.chat_json(... json_schema=...)` 调用点所在文件:
- 业务层文件必须 import retry_on_input_too_long_400(已接入输入守卫);
- 转发/基础层在 EXEMPT 白名单(仅透传上层 schema, 不构造长 prompt, 由调用方守卫)。

新增未接守卫的约束解码链路会让本测试失败, 提示补接入或显式豁免, 防止 复发。
"""

import ast
from pathlib import Path

APP_ROOT = Path(__file__).parent.parent / "app"

# 转发/基础层: chat_json(json_schema=) 仅透传上层传入的 schema, 不构造长 prompt,
# 输入体积治理由真正构造 prompt 的业务调用方负责。
EXEMPT_FILES = {
    "core/ai/cloud_client.py",   # chat_json 定义处
    "core/ai/dispatcher.py",     # 多通道转发
    "core/ai/ai_factory.py",     # 客户端工厂转发
    "core/ai/health_probe.py",   # 固定短字符串探针，无业务输入可收缩
}

GUARD_SYMBOL = "retry_on_input_too_long_400"


def _rel(path: Path) -> str:
    return path.relative_to(APP_ROOT).as_posix()


def _calls_chat_json_with_schema(tree: ast.AST) -> bool:
    """是否存在 `<obj>.chat_json(..., json_schema=...)` 调用。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "chat_json":
            if any(kw.arg == "json_schema" for kw in node.keywords):
                return True
    return False


def test_all_constrained_decoding_callsites_have_input_guard():
    offenders: list[str] = []

    for py in APP_ROOT.rglob("*.py"):
        source = py.read_text(encoding="utf-8")
        # 快速预筛, 避免 AST 解析全量文件
        if "chat_json" not in source or "json_schema" not in source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        if not _calls_chat_json_with_schema(tree):
            continue

        rel = _rel(py)
        if rel in EXEMPT_FILES:
            continue
        if GUARD_SYMBOL not in source:
            offenders.append(rel)

    assert not offenders, (
        "以下文件存在 chat_json(json_schema=) 约束解码调用但未接入 "
        f"{GUARD_SYMBOL} 输入守卫; 请接入语义收缩重试或加入 EXEMPT_FILES 白名单: "
        f"{sorted(offenders)}"
    )


def test_guard_symbol_is_importable():
    """护栏依赖的符号存在, 防止 GUARD_SYMBOL 拼写漂移导致契约空跑。"""
    from app.core.ai.input_guard import retry_on_input_too_long_400  # noqa: F401

    assert retry_on_input_too_long_400.__name__ == GUARD_SYMBOL


def test_scanner_detects_known_callsites():
    """防止 AST 检测逻辑失效导致契约空跑: 已知约束解码调用点必须被识别到。"""
    detected: set[str] = set()
    for py in APP_ROOT.rglob("*.py"):
        source = py.read_text(encoding="utf-8")
        if "chat_json" not in source or "json_schema" not in source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        if _calls_chat_json_with_schema(tree):
            detected.add(_rel(py))

    expected = {
        "core/extractor/bid_extractor.py",
        "core/ai/critic/cove.py",
        "core/ai/critic/judge.py",
    }
    missing = expected - detected
    assert not missing, (
        f"AST 检测未识别到已知约束解码调用点(检测逻辑可能失效, 契约会空跑): {sorted(missing)}"
    )
