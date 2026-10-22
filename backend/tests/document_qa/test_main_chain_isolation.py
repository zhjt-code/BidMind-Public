"""主链路对质检代码的引用白名单。

质检是与审查主链路并行、独立的结果域。主链路只能经下列入口碰质检代码;
新增引用必须先评估对审查的影响(是否会被 await、是否共享资源、出错是否兜底), 再更新白名单。
"""

from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[2] / "app"

# 主链路文件 -> 允许引用的质检模块
_ALLOWED: dict[str, set[str]] = {
    "main.py": {"app.api.routers.document_qa"},
    "worker.py": {"app.core.tasks.document_qa_job", "app.core.tasks.document_qa_verify_task"},
    "models/__init__.py": {"app.models.document_qa"},
    # 处理完成: 信封收敛(内部整体兜底) + 投递质检任务(只投递, 出错只记日志)
    "core/tasks/process_task.py": {
        "app.core.tasks.process.document_qa_finalize",
        "app.core.tasks.document_qa_job",
    },
    # 审查开始: 当前性保障挂起不等待
    "core/tasks/review_task.py": {"app.services.document_qa.ensure_current"},
    # 审查报告: 读取质检载荷(读取失败兜底) + 质检章节(构建失败本章节降级)
    "services/review_service.py": {"app.services.document_qa.read_service"},
    "core/report/generator.py": {"app.core.report.document_qa_section"},
    # 审查检索复用槽位取值归一化: 改动 normalizers 会改变审查的材料匹配
    "core/retriever/material_key.py": {"app.core.document_qa.normalizers"},
    "core/retriever/material_inventory.py": {"app.core.document_qa.normalizers"},
}


def _is_qa_owned(path: Path) -> bool:
    relative = path.relative_to(_APP)
    return "document_qa" in relative.parts[:-1] or relative.stem.startswith("document_qa")


def qa_imports(source: str) -> set[str]:
    """源码里引用到的质检模块(含 `from pkg import document_qa` 这种写法)。"""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            if "document_qa" in node.module:
                found.add(node.module)
                continue
            found.update(
                f"{node.module}.{alias.name}"
                for alias in node.names
                if alias.name.startswith("document_qa")
            )
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if "document_qa" in alias.name)
    return found


def test_detector_sees_submodule_and_package_imports() -> None:
    source = (
        "from app.core.document_qa.model_builder import build_document_model\n"
        "from app.core.tasks.document_qa_job import document_qa_job\n"
        "from app.api.routers import projects, document_qa\n"
        "import app.services.document_qa.read_service as reader\n"
        "from app.core.parser import pdf_parser\n"
    )
    assert qa_imports(source) == {
        "app.core.document_qa.model_builder",
        "app.core.tasks.document_qa_job",
        "app.api.routers.document_qa",
        "app.services.document_qa.read_service",
    }


def test_main_chain_references_to_document_qa_stay_in_whitelist() -> None:
    actual: dict[str, set[str]] = {}
    for path in sorted(_APP.rglob("*.py")):
        if _is_qa_owned(path):
            continue
        modules = qa_imports(path.read_text(encoding="utf-8"))
        if modules:
            actual[path.relative_to(_APP).as_posix()] = modules
    assert actual == _ALLOWED
