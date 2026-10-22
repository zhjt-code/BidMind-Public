"""Worker 资源调度策略。

该模块只描述任务函数与资源类型/队列的映射, 不执行调度动作。
默认保持 ARQ 原队列兼容; 仅在 WORKER_RESOURCE_QUEUES_ENABLED=true 时按资源队列入队。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.config import settings

ResourceType = Literal["default", "process", "llm", "report", "maintenance", "gpu"]


@dataclass(frozen=True)
class ResourcePolicy:
    """单个 worker job 的资源策略。"""

    function_name: str
    resource_type: ResourceType
    queue_name: str
    max_jobs: int
    timeout_seconds: int


_PROCESS_FUNCTIONS = {
    "process_document_job",
    "document_qa_job",
    "knowledge_base_process_job",
    "writing_material_process_job",
    "writing_clause_vectorize_job",
}

_LLM_FUNCTIONS = {
    "extract_requirements_job",
    "review_bid_extract_job",
    "review_project_job",
    "collusion_analyze_job",
    "document_image_vl_job",
    "document_qa_verify_job",
    "writing_image_vl_job",
    "writing_image_classify_job",
    "writing_extract_job",
    "writing_outline_job",
    "writing_match_job",
    "writing_compose_job",
    "writing_compose_single_job",
    "express_advance_job",
}

_MAINTENANCE_FUNCTIONS = {
    "flush_metrics_job",
    "regression_runner_job",
    "liveness_sweep_job",
    "prune_observability_logs_job",
    "weekly_llm_advice_job",
}


def resource_type_for_function(function_name: str) -> ResourceType:
    """返回 job 函数主要占用的资源类型。"""

    if function_name in _PROCESS_FUNCTIONS:
        return "process"
    if function_name in _LLM_FUNCTIONS:
        return "llm"
    if function_name in _MAINTENANCE_FUNCTIONS:
        return "maintenance"
    return "default"


def queue_name_for_resource(resource_type: ResourceType) -> str:
    """按资源类型返回队列名; 未启用资源队列时始终使用默认 ARQ 队列。"""

    if not bool(getattr(settings, "WORKER_RESOURCE_QUEUES_ENABLED", False)):
        return str(getattr(settings, "WORKER_DEFAULT_QUEUE_NAME", "arq:queue"))

    return {
        "process": str(getattr(settings, "WORKER_PROCESS_QUEUE_NAME", "arq:queue:process")),
        "llm": str(getattr(settings, "WORKER_LLM_QUEUE_NAME", "arq:queue:llm")),
        "report": str(getattr(settings, "WORKER_REPORT_QUEUE_NAME", "arq:queue:report")),
        "maintenance": str(
            getattr(settings, "WORKER_MAINTENANCE_QUEUE_NAME", "arq:queue:maintenance")
        ),
        "gpu": str(getattr(settings, "WORKER_DEFAULT_QUEUE_NAME", "arq:queue")),
        "default": str(getattr(settings, "WORKER_DEFAULT_QUEUE_NAME", "arq:queue")),
    }[resource_type]


def max_jobs_for_resource(resource_type: ResourceType) -> int:
    """返回资源类型的 worker 入口并发上限。"""

    return int({
        "process": getattr(settings, "WORKER_PROCESS_MAX_JOBS", 5),
        "llm": getattr(settings, "WORKER_LLM_MAX_JOBS", 5),
        "report": getattr(settings, "WORKER_REPORT_MAX_JOBS", 3),
        "maintenance": getattr(settings, "WORKER_MAINTENANCE_MAX_JOBS", 1),
        "gpu": getattr(settings, "GPU_ADMISSION_PERMIT_LIMIT", 1),
        "default": getattr(settings, "WORKER_DEFAULT_MAX_JOBS", 5),
    }[resource_type])


def policy_for_function(function_name: str) -> ResourcePolicy:
    """返回 job 函数的资源策略。"""

    resource_type = resource_type_for_function(function_name)
    from app.core.tasks.lifecycle.long_job_registry import resolve_job_timeout

    return ResourcePolicy(
        function_name=function_name,
        resource_type=resource_type,
        queue_name=queue_name_for_resource(resource_type),
        max_jobs=max_jobs_for_resource(resource_type),
        timeout_seconds=resolve_job_timeout(function_name),
    )


def all_resource_queue_names() -> list[str]:
    """返回探针需要检查的全部队列名。"""

    names = {
        str(getattr(settings, "WORKER_DEFAULT_QUEUE_NAME", "arq:queue")),
        str(getattr(settings, "WORKER_PROCESS_QUEUE_NAME", "arq:queue:process")),
        str(getattr(settings, "WORKER_LLM_QUEUE_NAME", "arq:queue:llm")),
        str(getattr(settings, "WORKER_REPORT_QUEUE_NAME", "arq:queue:report")),
        str(getattr(settings, "WORKER_MAINTENANCE_QUEUE_NAME", "arq:queue:maintenance")),
    }
    return sorted(names)
