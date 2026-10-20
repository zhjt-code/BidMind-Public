""" 中断契约防御测试

覆盖场景一览:
 A. record_task_interrupted 单元测试（4 个用例）
 A1 - 项目已删除时不写心跳
 A2 - 项目正常时以正确参数写心跳
 A3 - is_project_cancelled 抛异常时被吞且仍写心跳
 A4 - update_heartbeat 抛异常时不向外传播（best-effort）
 B. review/collusion/writing 族六个 task 的 CancelledError 分支结构静态断言
 B1 - run_review_task 分支含 record_task_interrupted 且 re-raise
 B2 - run_collusion_task 分支含 record_task_interrupted 且 re-raise
 B3 - run_writing_compose_task 分支含 record_task_interrupted 且 re-raise
 B5 - run_writing_extract_task 分支含 record_task_interrupted 且 re-raise
 B6 - run_writing_outline_task 分支含 record_task_interrupted 且 re-raise
 B7 - run_writing_match_task 分支含 record_task_interrupted 且 re-raise
 B4 - 六个 task 中 CancelledError 均出现在 Exception 之前（参数化）
 C. asyncio.CancelledError 继承关系护栏（1 个用例）
 C1 - CancelledError 继承 BaseException 而非 Exception

patch 目标说明:
 is_project_cancelled → app.core.arq_runtime.is_project_cancelled
 update_heartbeat → app.core.tasks.update_heartbeat

 两者均在 record_task_interrupted 函数体内 import，故需 patch 源模块属性而非
 调用方模块属性（否则已执行的 import 缓存会使 patch 失效）。
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import re
from unittest.mock import patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# A. record_task_interrupted 单元测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestRecordTaskInterrupted:
    """record_task_interrupted 的单元测试。

    每个用例均使用 patch 隔离 is_project_cancelled 与 update_heartbeat，
    无需数据库或 Redis 连接。
    """

    async def test_project_cancelled_skips_heartbeat(self) -> None:
        """A1: 项目已删除时，update_heartbeat 必须未被调用（沿用 守卫）。

 is_project_cancelled 返回 True 时函数提前 return，
 任何心跳写入都会触发外键失效，必须跳过。
 """
        from app.core.tasks.lifecycle.interrupt_contract import record_task_interrupted

        with (
            patch("app.core.arq_runtime.is_project_cancelled", return_value=True),
            patch("app.core.tasks.update_heartbeat") as mock_heartbeat,
        ):
            await record_task_interrupted(
                project_id=1,
                task_name="review_project_1",
                task_label="后台任务",
            )

        mock_heartbeat.assert_not_called(), (
            "项目已删除时禁止写心跳，update_heartbeat 被调用说明守卫逻辑失效"
        )

    async def test_project_active_calls_heartbeat_with_correct_args(self) -> None:
        """A2: 项目正常时，update_heartbeat 被调用一次且参数符合中断契约。

        断言:
          - 调用恰好一次
          - 第一个位置参数 == task_name
          - stage == "interrupted"
          - extra["interrupted"] is True
          - progress 含 "等待自动恢复"
        """
        from app.core.tasks.lifecycle.interrupt_contract import record_task_interrupted

        with (
            patch("app.core.arq_runtime.is_project_cancelled", return_value=False),
            patch("app.core.tasks.update_heartbeat") as mock_heartbeat,
        ):
            await record_task_interrupted(
                project_id=42,
                task_name="collusion_project_42",
                task_label="串标任务",
            )

        mock_heartbeat.assert_called_once()
        call = mock_heartbeat.call_args

        assert call.args[0] == "collusion_project_42", (
            f"第一个位置参数应为 task_name='collusion_project_42'，实际: {call.args}"
        )
        assert call.kwargs.get("stage") == "interrupted", (
            f"stage 应为 'interrupted'，实际: {call.kwargs.get('stage')}"
        )
        assert call.kwargs.get("extra", {}).get("interrupted") is True, (
            f"extra['interrupted'] 应为 True，实际: {call.kwargs.get('extra')}"
        )
        assert "等待自动恢复" in (call.kwargs.get("progress") or ""), (
            f"progress 应含 '等待自动恢复'，实际: {call.kwargs.get('progress')}"
        )

    async def test_is_project_cancelled_exception_swallowed_heartbeat_still_called(
        self,
    ) -> None:
        """A3: is_project_cancelled 抛异常时，异常被吞且 update_heartbeat 仍被调用。

        interrupt_contract 用 try/except Exception: pass 包裹了取消检查，
        任何读取失败都不应阻止心跳写入（best-effort 原则）。
        """
        from app.core.tasks.lifecycle.interrupt_contract import record_task_interrupted

        with (
            patch(
                "app.core.arq_runtime.is_project_cancelled",
                side_effect=RuntimeError("db 连接失败"),
            ),
            patch("app.core.tasks.update_heartbeat") as mock_heartbeat,
        ):
            # 不应向外抛任何异常
            await record_task_interrupted(
                project_id=99,
                task_name="review_project_99",
                task_label="后台任务",
            )

        mock_heartbeat.assert_called_once(), (
            "is_project_cancelled 异常被吞后，update_heartbeat 仍应被调用"
        )

    async def test_update_heartbeat_exception_does_not_propagate(self) -> None:
        """A4: update_heartbeat 抛异常时，record_task_interrupted 不得向外传播（best-effort）。

        中断痕迹写入本身是尽力而为；取消窗口内 Redis/DB 均可能不可用，
        本函数失败不得影响调用方的 re-raise 流程。
        """
        from app.core.tasks.lifecycle.interrupt_contract import record_task_interrupted

        with (
            patch("app.core.arq_runtime.is_project_cancelled", return_value=False),
            patch("app.core.tasks.update_heartbeat", side_effect=Exception("redis 宕机")),
        ):
            # 直接 await，若函数向外抛则 pytest 捕获并报告失败
            await record_task_interrupted(
                project_id=7,
                task_name="writing_compose_7",
                task_label="标书编写",
            )
        # 执行到此处即代表无异常向外传播


# ═══════════════════════════════════════════════════════════════════════════════
# B. review/collusion/writing 族六个 task 的 CancelledError 分支结构静态断言
# ═══════════════════════════════════════════════════════════════════════════════

def _get_task_source(func: object) -> str:
    """获取 task 函数的原始源码（自动穿透装饰器包裹）。

    inspect.getsource 内部调用 inspect.unwrap，可跟随 __wrapped__ 链找到
    原始函数源码，即使函数被 @milestone / @observe_task 等装饰器包裹。
    """
    return inspect.getsource(inspect.unwrap(func))  # type: ignore[arg-type]


def _find_cancelled_indent(lines: list[str]) -> int | None:
    """找到顶层 except asyncio.CancelledError 的缩进列数（取最小缩进），未找到返回 None。

    writing_extract / writing_outline 内层 keepalive 也有 CancelledError（缩进更深）；
    契约断言必须锚定与 ProjectCancelledError / Exception 同级的顶层分支。
    """
    indents: list[int] = []
    for line in lines:
        stripped = line.lstrip()
        # 只匹配代码行（排除注释）
        if stripped.startswith("except asyncio.CancelledError") and not stripped.startswith("#"):
            indents.append(len(line) - len(stripped))
    return min(indents) if indents else None


def _extract_cancelled_block(source: str) -> str:
    """从源码中提取顶层 except asyncio.CancelledError 块的完整内容。

 通过缩进级别判断块的结束位置，正确处理块内嵌套的 try/except Exception。
 不能用文本搜索 "except Exception" 作为边界，原因：
 1. CancelledError 块内的注释（如 说明）本身含 "except Exception" 字符串
 2. collusion/writing task 的 CancelledError 块内有 `except Exception: pass`
 """
    lines = source.splitlines()
    start_indent = _find_cancelled_indent(lines)
    if start_indent is None:
        return ""

    in_block = False
    block_lines: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        current_indent = len(line) - len(stripped)

        if not in_block:
            if (
                stripped.startswith("except asyncio.CancelledError")
                and not stripped.startswith("#")
                and current_indent == start_indent
            ):
                in_block = True
                block_lines.append(line)
            continue

        # 已进入块：遇到同级或更高层的 except/finally/else 时结束
        if stripped and current_indent <= start_indent:
            if stripped.startswith(("except ", "finally:", "else:")):
                break
        block_lines.append(line)

    return "\n".join(block_lines)


def _check_cancelled_before_exception_at_same_indent(source: str) -> bool:
    """验证 except asyncio.CancelledError 在 except Exception 之前（同缩进级）。

    仅对比与 except asyncio.CancelledError 相同缩进列数的 except 语句，
    忽略嵌套的 except Exception，避免误报。
    返回 True 表示顺序正确，False 表示存在同级 except Exception 在其之前。
    """
    lines = source.splitlines()
    ref_indent = _find_cancelled_indent(lines)
    if ref_indent is None:
        return False

    saw_cancelled = False
    for line in lines:
        stripped = line.lstrip()
        current_indent = len(line) - len(stripped)
        # 只检查同缩进级别的代码行（非注释）
        if current_indent != ref_indent or stripped.startswith("#"):
            continue
        if stripped.startswith("except asyncio.CancelledError"):
            saw_cancelled = True
        elif stripped.startswith("except Exception") and not saw_cancelled:
            return False  # 在同级发现了先于 CancelledError 出现的 except Exception

    return saw_cancelled


def _assert_cancelled_branch_contract(func: object, func_name: str) -> None:
    """断言 task 顶层 CancelledError 分支含 record_task_interrupted 且裸 raise。"""
    source = _get_task_source(func)
    assert "except asyncio.CancelledError" in source, (
        f"{func_name} 必须包含 except asyncio.CancelledError 分支（修复）"
    )

    block = _extract_cancelled_block(source)
    assert "record_task_interrupted" in block, (
        "CancelledError 分支必须调用 record_task_interrupted 写入中断痕迹"
    )
    assert re.search(r"^\s*raise\s*$", block, re.MULTILINE), (
        "CancelledError 分支必须含裸 raise 语句，否则 ARQ 无法完成 job 取消语义"
    )


class TestCancelledErrorBranchStructure:
    """静态检查六个 task 源码中 CancelledError 分支的结构契约。

    采用静态断言而非行为断言的理由:
      1. 六个 task 均依赖完整的 DB session、ARQ 上下文和多个业务 service，
         构造真实环境并触发 asyncio.CancelledError 的代价极高（需 mock 数十个依赖）。
      2. "必须 re-raise" 是纯结构性契约（不重新抛出则 ARQ 将被取消的 job
         当作正常完成，导致 worker 无法响应 graceful shutdown），
         inspect.getsource 静态检查足以锁定该契约。
      3. 内部实现细节调整（如心跳写入方式、rollback 顺序）不影响静态断言结果，
         因此不会产生误报。
    """

    def test_review_task_cancelled_error_branch(self) -> None:
        """B1: run_review_task 的 CancelledError 分支必须含 record_task_interrupted 且 re-raise。"""
        from app.core.tasks.review_task import run_review_task

        _assert_cancelled_branch_contract(run_review_task, "run_review_task")

    def test_collusion_task_cancelled_error_branch(self) -> None:
        """B2: run_collusion_task 的 CancelledError 分支必须含 record_task_interrupted 且 re-raise。"""
        from app.core.tasks.collusion_task import run_collusion_task

        _assert_cancelled_branch_contract(run_collusion_task, "run_collusion_task")

    def test_writing_compose_task_cancelled_error_branch(self) -> None:
        """B3: run_writing_compose_task 的 CancelledError 分支必须含 record_task_interrupted 且 re-raise。"""
        from app.core.tasks.writing_compose_task import run_writing_compose_task

        _assert_cancelled_branch_contract(
            run_writing_compose_task, "run_writing_compose_task"
        )

    def test_writing_extract_task_cancelled_error_branch(self) -> None:
        """B5: run_writing_extract_task 的 CancelledError 分支必须含 record_task_interrupted 且 re-raise。"""
        from app.core.tasks.writing_extract_task import run_writing_extract_task

        _assert_cancelled_branch_contract(
            run_writing_extract_task, "run_writing_extract_task"
        )

    def test_writing_outline_task_cancelled_error_branch(self) -> None:
        """B6: run_writing_outline_task 的 CancelledError 分支必须含 record_task_interrupted 且 re-raise。"""
        from app.core.tasks.writing_outline_task import run_writing_outline_task

        _assert_cancelled_branch_contract(
            run_writing_outline_task, "run_writing_outline_task"
        )

    def test_writing_match_task_cancelled_error_branch(self) -> None:
        """B7: run_writing_match_task 的 CancelledError 分支必须含 record_task_interrupted 且 re-raise。"""
        from app.core.tasks.writing_match_task import run_writing_match_task

        _assert_cancelled_branch_contract(
            run_writing_match_task, "run_writing_match_task"
        )

    @pytest.mark.parametrize(
        "module_name,func_name",
        [
            ("app.core.tasks.review_task", "run_review_task"),
            ("app.core.tasks.collusion_task", "run_collusion_task"),
            ("app.core.tasks.writing_compose_task", "run_writing_compose_task"),
            ("app.core.tasks.writing_extract_task", "run_writing_extract_task"),
            ("app.core.tasks.writing_outline_task", "run_writing_outline_task"),
            ("app.core.tasks.writing_match_task", "run_writing_match_task"),
        ],
    )
    def test_cancelled_error_appears_before_exception(
        self, module_name: str, func_name: str
    ) -> None:
        """B4: 六个 task 中 except asyncio.CancelledError 必须出现在 except Exception 之前（同缩进级）。

        顺序颠倒不影响功能（两者处于不同继承链，CancelledError 不会被 Exception 捕获），
        但正确排列有助于代码阅读者直观理解取消信号不会被 Exception 拦截的原因，
        避免未来维护者误删 CancelledError 分支。

        实现说明：仅比较与顶层 except asyncio.CancelledError 相同缩进级别的 except 语句，
        忽略函数体内嵌套 try/except 中的 except Exception，避免误报。
        """
        module = importlib.import_module(module_name)
        func = getattr(module, func_name)
        source = _get_task_source(func)

        assert _check_cancelled_before_exception_at_same_indent(source), (
            f"{func_name} 中，与 except asyncio.CancelledError 同缩进级的 except Exception "
            f"出现在 except asyncio.CancelledError 之前，顺序应调换以保持代码可读性"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# C. asyncio.CancelledError 继承关系护栏
# ═══════════════════════════════════════════════════════════════════════════════

class TestCancelledErrorInheritanceGuard:
    def test_cancelled_error_inherits_base_exception_not_exception(self) -> None:
        """C1: 锁定 修复的核心前提——CancelledError 继承 BaseException 而非 Exception。

 这条断言的意义:
 - asyncio.CancelledError 自 Python 3.8 起从 Exception 改为继承 BaseException，
 正是这一变更导致 review/collusion/writing task 的 except Exception 无法捕获
 它，从而产生了"任务静默消失"的。
 - 若未来 Python 版本再次将其改回继承 Exception，则 except Exception 即可兜住
 取消信号， 新增的 CancelledError 分支即可安全删除
 或合并，本测试在 CI 中充当该前提的"变更预警断言"。
 """
        assert issubclass(asyncio.CancelledError, BaseException), (
            "asyncio.CancelledError 应继承 BaseException（Python 3.8+ 设计， 修复前提）"
        )
        assert not issubclass(asyncio.CancelledError, Exception), (
            "asyncio.CancelledError 不应继承 Exception；"
            "若此断言失败，说明 Python 版本已改变该继承关系，"
            " CancelledError 分支可重新评估是否仍需保留"
        )
