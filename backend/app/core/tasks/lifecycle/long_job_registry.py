"""长耗时 LLM job 族调度契约（Wave1-A）

职责: 定义长耗时 job 族及其调度契约（防呆墙钟、心跳标识解析）
不做: 不判定卡死、不接线执行、不写任何状态
"""

from __future__ import annotations

from app.core.config import settings

# 图片 VL 子任务不入族；仅覆盖会长时间占用 LLM worker 的主 job。
LONG_LLM_JOBS: frozenset[str] = frozenset(
    {
        "writing_extract_job",
        "writing_outline_job",
        "writing_match_job",
        "writing_compose_job",
        "writing_compose_single_job",
        "extract_requirements_job",
        "review_project_job",
        "review_bid_extract_job",
        "collusion_analyze_job",
    }
)

# function_name -> 心跳任务名模板；与各 get_*_task_name 源码格式对齐。
# extract / collusion 源码分别为 extract_project_{id} / collusion_project_{id}。
_TASK_NAME_BY_FUNCTION: dict[str, str] = {
    "writing_extract_job": "writing_extract_{project_id}",
    "writing_outline_job": "writing_outline_{project_id}",
    "writing_match_job": "writing_match_{project_id}",
    "writing_compose_job": "writing_compose_{project_id}",
    "writing_compose_single_job": "writing_compose_single_{project_id}_{node_id}",
    "extract_requirements_job": "extract_project_{project_id}",
    "review_project_job": "review_project_{project_id}",
    "review_bid_extract_job": "review_bid_extract_{project_id}",
    "collusion_analyze_job": "collusion_project_{project_id}",
}


def resolve_job_timeout(function_name: str) -> int:
    """解析 ARQ 防呆墙钟秒数。

    LONG 族统一读 ``WORKER_LONG_LLM_JOB_TIMEOUT``（缺省 7200）；
    其余回退 ``WORKER_LLM_JOB_TIMEOUT``（缺省 1800）。
    """
    if function_name in LONG_LLM_JOBS:
        return int(getattr(settings, "WORKER_LONG_LLM_JOB_TIMEOUT", 7200))
    return int(getattr(settings, "WORKER_LLM_JOB_TIMEOUT", 1800))


def resolve_task_name(
    function_name: str,
    project_id: int,
    *,
    node_id: int | None = None,
) -> str | None:
    """将 ARQ function 名解析为心跳/进度用的 task_name。

    未知 function 返回 None；``writing_compose_single_job`` 缺 ``node_id`` 时返回 None。
    """
    template = _TASK_NAME_BY_FUNCTION.get(function_name)
    if template is None:
        return None
    if function_name == "writing_compose_single_job":
        if node_id is None:
            return None
        return template.format(project_id=project_id, node_id=node_id)
    return template.format(project_id=project_id)
