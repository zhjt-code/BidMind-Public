"""ARQ Worker 注册合同测试

目的: 防止将来新增业务任务时漏注册, 或 WORKER_FUNCTION_NAMES 与 functions 不一致。
关键约束:
  - WORKER_FUNCTION_NAMES 与 WorkerSettings.functions 的函数名必须一一对应
  - 三大业务流程 + 写标系列的关键 job 都在列
  - 每个 job 都有 ctx 作为第一参数 (ARQ 约定)

注: WorkerSettings.functions 列表可同时包含裸 async 函数和被 arq.worker.func(...) 包装的
    arq.worker.Function 对象。后者没有 `__name__`, 只有 `.name` 属性, 且真实签名在 `.coroutine`
    属性上。本文件的 _fn_name / _underlying 辅助统一兼容这两种形态, 避免测试因包装与否而失败。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.worker import (
    LLM_WORKER_FUNCTION_NAMES,
    MAINTENANCE_WORKER_FUNCTION_NAMES,
    PROCESS_WORKER_FUNCTION_NAMES,
    WORKER_FUNCTION_NAMES,
    LlmWorkerSettings,
    MaintenanceWorkerSettings,
    ProcessWorkerSettings,
    WorkerSettings,
)
from app.core.config import settings


# ARQ 合同: 每个 worker function 的第一个参数必须是 ctx (dict)
_EXPECTED_FIRST_PARAM = "ctx"


def _fn_name(fn: Any) -> str:
    """兼容裸函数 (`__name__`) 与 arq.worker.Function (`.name`) 两种形态取名。"""
    name = getattr(fn, "__name__", None)
    if name:
        return name
    name = getattr(fn, "name", None)
    if name:
        return name
    raise AttributeError(f"无法从 {fn!r} 取到函数名")


def _underlying(fn: Any) -> Any:
    """取真实可签名的 async 函数体: 若是 arq Function 包装则返回其 .coroutine。"""
    return getattr(fn, "coroutine", fn)


def _find_registered_function(functions: list[Any], name: str) -> Any:
    """按 ARQ 注册名定位 Function，避免直接依赖列表顺序。"""
    for fn in functions:
        if _fn_name(fn) == name:
            return fn
    raise AssertionError(f"未找到 worker function: {name}")


def test_worker_function_names_match_settings_functions() -> None:
    """WORKER_FUNCTION_NAMES 必须与 WorkerSettings.functions 完全对齐, 避免 alive 心跳声称
    支持某任务但实际未注册而导致入队后无人消费。"""
    settings_fn_names = [_fn_name(fn) for fn in WorkerSettings.functions]
    assert sorted(WORKER_FUNCTION_NAMES) == sorted(settings_fn_names)


def test_resource_worker_function_names_match_settings_functions() -> None:
    """资源型 WorkerSettings 的函数列表必须与各自心跳列表一致。"""

    assert sorted(PROCESS_WORKER_FUNCTION_NAMES) == sorted(
        _fn_name(fn) for fn in ProcessWorkerSettings.functions
    )
    assert sorted(LLM_WORKER_FUNCTION_NAMES) == sorted(
        _fn_name(fn) for fn in LlmWorkerSettings.functions
    )
    assert sorted(MAINTENANCE_WORKER_FUNCTION_NAMES) == sorted(
        _fn_name(fn) for fn in MaintenanceWorkerSettings.functions
    )


def test_extract_requirements_job_uses_dedicated_timeout() -> None:
    """: 招标要求提取必须使用独立防呆超时，避免被 LLM 队列 1800s 误杀。"""
    expected_timeout = getattr(settings, "WORKER_EXTRACT_JOB_TIMEOUT", 7200)

    default_job = _find_registered_function(
        WorkerSettings.functions,
        "extract_requirements_job",
    )
    llm_job = _find_registered_function(
        LlmWorkerSettings.functions,
        "extract_requirements_job",
    )

    assert getattr(default_job, "timeout_s", None) == expected_timeout
    assert getattr(llm_job, "timeout_s", None) == expected_timeout


def test_long_llm_jobs_never_inherit_default_1800_timeout() -> None:
    """: 长 LLM job 族必须显式挂 timeout，禁止回落默认 job_timeout=1800。"""
    from app.core.tasks.lifecycle.long_job_registry import LONG_LLM_JOBS

    long_timeout = int(getattr(settings, "WORKER_LONG_LLM_JOB_TIMEOUT", 7200))
    extract_timeout = int(getattr(settings, "WORKER_EXTRACT_JOB_TIMEOUT", 7200))
    default_queue_timeout = int(WorkerSettings.job_timeout)
    llm_queue_timeout = int(LlmWorkerSettings.job_timeout)

    assert default_queue_timeout == 1800
    assert llm_queue_timeout == int(getattr(settings, "WORKER_LLM_JOB_TIMEOUT", 1800))

    for settings_cls in (WorkerSettings, LlmWorkerSettings):
        for job_name in sorted(LONG_LLM_JOBS):
            registered = _find_registered_function(settings_cls.functions, job_name)
            assert registered is not None, f"{settings_cls.__name__} 缺少 {job_name}"
            timeout_s = getattr(registered, "timeout_s", None)
            assert timeout_s is not None, (
                f"{settings_cls.__name__}.{job_name} 未设 timeout_s，会继承 {llm_queue_timeout}s"
            )
            expected = (
                extract_timeout
                if job_name == "extract_requirements_job"
                else long_timeout
            )
            assert timeout_s == expected, (
                f"{settings_cls.__name__}.{job_name} timeout_s={timeout_s}, 期望 {expected}"
            )
            assert timeout_s > default_queue_timeout


def test_document_image_vl_job_only_registered_on_llm_worker() -> None:
    """投标/串标图片 VL 子任务必须只由 LLM worker 消费。"""

    process_names = {_fn_name(fn) for fn in ProcessWorkerSettings.functions}
    llm_names = {_fn_name(fn) for fn in LlmWorkerSettings.functions}

    assert "document_image_vl_job" not in process_names
    assert "document_image_vl_job" in llm_names


def test_worker_covers_three_flows_plus_writing() -> None:
    """三大核心业务 + 写标系列的关键 job 必须都被注册:
    - 文档处理: process_document_job
    - 投标审查: extract_requirements_job, review_project_job
    - 串标审查: collusion_analyze_job
    - 图片 LLM 子任务: document_image_vl_job
    - 标书编写: writing_extract/outline/material_process/image_vl/image_classify/match/compose/compose_single/clause_vectorize
    - 一步到位推进: express_advance_job
    """
    required = {
        # 共享文档处理底座 (三个流程共用)
        "process_document_job",
        "knowledge_base_process_job",
        # 投标审查
        "extract_requirements_job",
        "review_project_job",
        # 串标审查
        "collusion_analyze_job",
        # 投标审查/串标审查通用文档图片 VL 子任务
        "document_image_vl_job",
        # 标书编写
        "writing_extract_job",
        "writing_outline_job",
        "writing_material_process_job",
        "writing_image_vl_job",
        "writing_image_classify_job",
        "writing_match_job",
        "writing_compose_job",
        "writing_compose_single_job",
        "writing_clause_vectorize_job",
        "express_advance_job",
    }
    missing = required - set(WORKER_FUNCTION_NAMES)
    assert not missing, f"worker 未注册任务: {missing}"


def test_each_job_has_ctx_first_parameter() -> None:
    """ARQ 约定: ctx 必须是第一参数, 否则 enqueue_job 调用签名会错位。"""
    for fn in WorkerSettings.functions:
        sig = inspect.signature(_underlying(fn))
        params = list(sig.parameters.keys())
        assert params and params[0] == _EXPECTED_FIRST_PARAM, (
            f"{_fn_name(fn)} 第一参数应为 ctx, 实际为 {params}"
        )


def test_writing_jobs_signature_takes_project_id() -> None:
    """写标系列 job 前两个参数统一为 (ctx, project_id), 确保 enqueue_job_safe 调用一致.

    当前注册列表:
      - writing_extract_job (ctx, project_id)
      - writing_outline_job (ctx, project_id)
      - writing_material_process_job (ctx, project_id)
      - writing_image_vl_job (ctx, project_id, batch_id, items)
      - writing_image_classify_job (ctx, project_id, batch_id, items)
      - writing_match_job (ctx, project_id)
      - writing_compose_job (ctx, project_id)
      - writing_compose_single_job (ctx, project_id, node_id)
      - writing_clause_vectorize_job (ctx, project_id)
    """
    writing_jobs = [
        fn for fn in WorkerSettings.functions
        if _fn_name(fn).startswith("writing_")
    ]
    assert len(writing_jobs) == 9, f"写标系列应注册 9 个 job, 实际 {len(writing_jobs)}"
    for fn in writing_jobs:
        sig = inspect.signature(_underlying(fn))
        param_names = list(sig.parameters.keys())
        assert param_names[:2] == ["ctx", "project_id"], (
            f"{_fn_name(fn)} 前两参数应为 (ctx, project_id), 实际为 {param_names}"
        )
