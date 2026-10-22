"""投标审查 / 串标审查项目级显式重试编排。

通过 `FlowRetryConfig` + 私有泛型 helper（`_latest_task_run` / `_is_retry_running`
/ `_next_attempt` / `_begin_retry_attempt` / `_attach_dispatch_result` /
`_mark_task_run_by_task_name` / `_build_retry_state`）把两侧孪生函数收敛为单套实现，
review/collusion 公共函数仅保留 thin forwarder，保证 18+ 处调用方契约零变化
（R2-1，docs/refactor_execution_plan_round2.md §八）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.review_contracts import reviewable_requirement_conditions
from app.models.bid_response import BidResponse
from app.models.collusion_extraction import CollusionExtraction
from app.models.collusion_group import CollusionGroup
from app.models.collusion_result import CollusionResult
from app.models.document import Document, DocumentType
from app.models.project import Project, ProjectStatus
from app.models.requirement import Requirement
from app.models.review_result import ReviewResult
from app.models.task_run import TaskRun, TaskRunStatus, freeze_completed_duration
from app.models.vector_job_state import VectorJobState, VectorJobStatus


@dataclass(frozen=True)
class FlowRetrySpec:
    node_key: str
    failed_stage: str
    action_label: str
    preserved_outputs: tuple[str, ...]
    reset_scope: tuple[str, ...]


@dataclass(frozen=True)
class ReviewRetryContext:
    """投标审查项目级重试推断结果。"""

    node_key: str
    target_id: int | None = None


@dataclass(frozen=True)
class CollusionRetryContext:
    """串标审查项目级重试推断结果。"""

    node_key: str
    target_id: int | None = None


@dataclass(frozen=True)
class FlowRetryConfig:
    """单条业务流显式重试的差异化配置（review / collusion 各注入一份）。

    `extra_last_error_fn` 不为 None 时驱动 `_build_retry_state` 预读 `latest_any` 既用于
    retry_running 二级判定也用于 last_error fallback（与原 collusion 行为对齐；review 不需要）。
    """

    flow_constant: str
    retry_specs: dict[str, FlowRetrySpec]
    arq_function_map: dict[str, str | None]
    default_node_key: str
    extra_last_error_fn: Callable[[TaskRun | None], str | None] | None = None


FLOW_BID_REVIEW = "bid_review"
FLOW_COLLUSION_CHECK = "collusion_check"
RETRY_RUNNING_STATUSES = {
    TaskRunStatus.queued.value,
    TaskRunStatus.running.value,
}


REVIEW_RETRY_SPECS: dict[str, FlowRetrySpec] = {
    "document_process": FlowRetrySpec(
        node_key="document_process",
        failed_stage="processing",
        action_label="从文档解析继续重试",
        preserved_outputs=("已上传文件", "已完成解析和向量化的文档", "项目基础信息"),
        reset_scope=("失败文档的处理中状态", "失败的 VectorJobState 运行态"),
    ),
    "requirement_extract": FlowRetrySpec(
        node_key="requirement_extract",
        failed_stage="extracting",
        action_label="从招标要求提取继续重试",
        preserved_outputs=("已上传和已解析招标文件", "已有人工补录要求"),
        reset_scope=("未确认且由本次失败提取产生的不完整要求", "提取任务心跳"),
    ),
    "bid_response_extract": FlowRetrySpec(
        node_key="bid_response_extract",
        failed_stage="extracting",
        action_label="从投标响应提取继续重试",
        preserved_outputs=("已确认招标要求", "已成功提取的投标响应"),
        reset_scope=("失败或不完整的投标响应中间结果", "缺失或失败的审查结果", "review_project heartbeat"),
    ),
    "review_compare": FlowRetrySpec(
        node_key="review_compare",
        failed_stage="reviewing",
        action_label="从AI对比审查继续重试",
        preserved_outputs=("已上传文件", "已确认招标要求", "已成功投标响应"),
        reset_scope=("缺失或失败的审查结果", "报告草稿", "review_project heartbeat"),
    ),
    "review_report": FlowRetrySpec(
        node_key="review_report",
        failed_stage="reporting",
        action_label="重新生成审查报告",
        preserved_outputs=("已确认招标要求", "全量审查结果"),
        reset_scope=("失败报告文件", "报告生成缓存"),
    ),
    "review_project": FlowRetrySpec(
        node_key="review_project",
        failed_stage="reviewing",
        action_label="重新发起投标审查",
        preserved_outputs=("已上传文件", "已确认招标要求", "已有审查中间结果"),
        reset_scope=("失败的审查任务状态", "review_project heartbeat", "缺失或失败的审查结果"),
    ),
}

REVIEW_ARQ_FUNCTION_BY_NODE: dict[str, str | None] = {
    "document_process": "process_document_job",
    "requirement_extract": "extract_requirements_job",
    "bid_response_extract": "review_project_job",
    "review_compare": "review_project_job",
    "review_report": None,
    "review_project": "review_project_job",
}

COLLUSION_RETRY_SPECS: dict[str, FlowRetrySpec] = {
    "document_process": FlowRetrySpec(
        node_key="document_process",
        failed_stage="processing",
        action_label="从文档解析继续重试",
        preserved_outputs=("已上传文件", "已完成解析和向量化的文档"),
        reset_scope=("失败文档的处理中状态", "失败的 VectorJobState 运行态"),
    ),
    "collusion_extract": FlowRetrySpec(
        node_key="collusion_extract",
        failed_stage="extracting",
        action_label="从文档信息提取继续重试",
        preserved_outputs=("已上传文件", "已完成文档解析", "已成功提取的文档信息"),
        reset_scope=("非成功提取记录", "依赖失败提取的下游结果", "collusion heartbeat"),
    ),
    "collusion_global": FlowRetrySpec(
        node_key="collusion_global",
        failed_stage="global_features",
        action_label="从全局特征分析继续重试",
        preserved_outputs=("成功提取的文档信息", "已完成文档解析"),
        reset_scope=("失败的全局特征阶段状态", "项目综合研判摘要"),
    ),
    "collusion_compare": FlowRetrySpec(
        node_key="collusion_compare",
        failed_stage="comparing",
        action_label="从重点文件对比继续重试",
        preserved_outputs=("已上传文件", "成功提取的文档信息", "已成功的文件对结果"),
        reset_scope=("失败或缺失的文件对结果", "群组与综合研判结果"),
    ),
    "collusion_cluster": FlowRetrySpec(
        node_key="collusion_cluster",
        failed_stage="clustering",
        action_label="从嫌疑群组识别继续重试",
        preserved_outputs=("成功提取的文档信息", "已完成文件对结果"),
        reset_scope=("旧群组识别结果", "项目综合研判摘要"),
    ),
    "collusion_synthesis": FlowRetrySpec(
        node_key="collusion_synthesis",
        failed_stage="synthesizing",
        action_label="从综合研判继续重试",
        preserved_outputs=("成功提取的文档信息", "已完成文件对结果", "群组识别结果"),
        reset_scope=("项目综合研判摘要", "报告草稿"),
    ),
    "collusion_report": FlowRetrySpec(
        node_key="collusion_report",
        failed_stage="reporting",
        action_label="重新生成串标报告",
        preserved_outputs=("全部分析结果", "证据链"),
        reset_scope=("失败报告文件", "报告生成缓存"),
    ),
    "collusion_analyze": FlowRetrySpec(
        node_key="collusion_analyze",
        failed_stage="analyzing",
        action_label="重新发起串标分析",
        preserved_outputs=("已上传文件", "已完成文档解析", "已有提取和对比结果"),
        reset_scope=("失败的串标分析任务状态", "collusion heartbeat", "缺失或失败的分析结果"),
    ),
}

COLLUSION_ARQ_FUNCTION_BY_NODE: dict[str, str | None] = {
    "document_process": "process_document_job",
    "collusion_extract": "collusion_analyze_job",
    "collusion_global": "collusion_analyze_job",
    "collusion_compare": "collusion_analyze_job",
    "collusion_cluster": "collusion_analyze_job",
    "collusion_synthesis": "collusion_analyze_job",
    "collusion_report": None,
    "collusion_analyze": "collusion_analyze_job",
}


def _collusion_extra_last_error(latest_any: TaskRun | None) -> str | None:
    """串标审查二级 last_error fallback：直接读取已查询的最近一次 TaskRun。"""

    if latest_any is not None and latest_any.last_error:
        return latest_any.last_error
    return None


REVIEW_FLOW_CONFIG: FlowRetryConfig = FlowRetryConfig(
    flow_constant=FLOW_BID_REVIEW,
    retry_specs=REVIEW_RETRY_SPECS,
    arq_function_map=REVIEW_ARQ_FUNCTION_BY_NODE,
    default_node_key="review_project",
    extra_last_error_fn=None,
)

COLLUSION_FLOW_CONFIG: FlowRetryConfig = FlowRetryConfig(
    flow_constant=FLOW_COLLUSION_CHECK,
    retry_specs=COLLUSION_RETRY_SPECS,
    arq_function_map=COLLUSION_ARQ_FUNCTION_BY_NODE,
    default_node_key="collusion_analyze",
    extra_last_error_fn=_collusion_extra_last_error,
)


def build_retry_payload(
    spec: FlowRetrySpec,
    *,
    retryable: bool,
    last_error: str | None = None,
    retry_running: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "retryable": retryable,
        "failed_node": spec.node_key if (retryable or retry_running) else None,
        "target_id": None,
        "failed_stage": spec.failed_stage if (retryable or retry_running) else None,
        "retry_action_label": spec.action_label,
        "preserved_outputs": list(spec.preserved_outputs),
        "reset_scope": list(spec.reset_scope),
        "last_attempt": 0,
        "retry_running": retry_running,
        "last_error": last_error,
    }
    if extra:
        payload.update(extra)
    return payload


_UNRECOVERABLE_DOCUMENT_ERROR_KEYWORDS = (
    "文件不存在",
    "no such file",
    "not found",
    "unexpected eof",
    "eof marker not found",
)


def is_unrecoverable_document_error(error_message: str | None) -> bool:
    """判断文档失败是否属于重跑无法修复的源文件不可读问题。"""
    if not error_message:
        return False
    normalized = str(error_message).strip().lower()
    return any(keyword in normalized for keyword in _UNRECOVERABLE_DOCUMENT_ERROR_KEYWORDS)


def build_unrecoverable_document_retry_payload(
    spec: FlowRetrySpec,
    *,
    target_id: int | None,
    last_error: str | None,
) -> dict[str, Any]:
    """构造源文件不可读时的串标重试提示: 阻断重跑并引导重新上传。"""
    payload = build_retry_payload(
        spec,
        retryable=False,
        retry_running=False,
        last_error=last_error,
        extra={"target_id": target_id},
    )
    payload.update({
        "retry_action_label": "重新上传文件",
        "reset_scope": ["源文件不可读", "需要重新上传后重新解析"],
        "preserved_outputs": ["项目基础信息", "其他可用文件"],
    })
    return payload


# ============================================================
# 泛型实现层（私有）：被 review / collusion forwarder 调用
# ============================================================


async def _latest_task_run(
    db: AsyncSession,
    project_id: int,
    *,
    config: FlowRetryConfig,
    node_key: str | None = None,
) -> TaskRun | None:
    """按 flow / 可选 node_key 取最近一次 TaskRun（同 (attempt, id) DESC 排序）。"""

    stmt = select(TaskRun).where(
        TaskRun.project_id == project_id,
        TaskRun.flow == config.flow_constant,
    )
    if node_key:
        stmt = stmt.where(TaskRun.node_key == node_key)
    stmt = stmt.order_by(TaskRun.attempt.desc(), TaskRun.id.desc()).limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def _is_retry_running(latest: TaskRun | None, *, config: FlowRetryConfig) -> bool:
    """用 TaskRun + liveness 双证据判断显式重试是否仍在运行。"""

    if latest is None:
        return False
    if latest.retry_requested_at is None or latest.status not in RETRY_RUNNING_STATUSES:
        return False
    arq_function = config.arq_function_map.get(latest.node_key)
    if arq_function is None:
        return latest.status in RETRY_RUNNING_STATUSES
    try:
        from app.core.config import settings
        from app.core.tasks import inspect_task_liveness

        liveness = inspect_task_liveness(
            latest.task_name,
            heartbeat_timeout=int(getattr(settings, "ARQ_JOB_HEARTBEAT_TIMEOUT", 180)),
            arq_function=arq_function,
        )
        return bool(liveness.is_live)
    except Exception:
        return latest.status in RETRY_RUNNING_STATUSES


async def _next_attempt(
    db: AsyncSession,
    project_id: int,
    *,
    config: FlowRetryConfig,
    node_key: str,
) -> int:
    """计算指定 (flow, node_key) 下一次 attempt 序号（基于已有最大 attempt + 1）。"""

    result = await db.execute(
        select(func.max(TaskRun.attempt)).where(
            TaskRun.project_id == project_id,
            TaskRun.flow == config.flow_constant,
            TaskRun.node_key == node_key,
        )
    )
    return int(result.scalar_one_or_none() or 0) + 1


async def _begin_retry_attempt(
    db: AsyncSession,
    *,
    project_id: int,
    config: FlowRetryConfig,
    node_key: str,
    task_name: str,
    user_id: int | None,
    target_id: int | None = None,
    last_stage: str | None = "retry_requested",
) -> TaskRun:
    """创建显式重试 attempt 记录（review / collusion 共用实现）。

    - 未识别的 node_key 落回 `config.default_node_key` 对应 spec
    - 自动写入 preserved_outputs / reset_scope JSON 以满足审计要求
    - 调用方负责后续 `_attach_dispatch_result` 回填 queue_mode/arq_job_id
    """

    spec = config.retry_specs.get(node_key, config.retry_specs[config.default_node_key])
    attempt = await _next_attempt(
        db, project_id, config=config, node_key=spec.node_key
    )
    reset_summary = {
        "scope": list(spec.reset_scope),
        "target_id": target_id,
    }
    run = TaskRun(
        project_id=project_id,
        flow=config.flow_constant,
        node_key=spec.node_key,
        task_name=task_name,
        status=TaskRunStatus.queued.value,
        attempt=attempt,
        last_stage=last_stage,
        retry_requested_by=user_id,
        retry_requested_at=datetime.now(timezone.utc),
        preserved_summary_json=json.dumps(list(spec.preserved_outputs), ensure_ascii=False),
        reset_scope_json=json.dumps(reset_summary, ensure_ascii=False),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def _attach_dispatch_result(
    db: AsyncSession,
    run: TaskRun,
    *,
    queue_mode: str,
    arq_job_id: str | None,
    last_stage: str | None = "queued",
) -> None:
    """显式重试 dispatch 回填（review / collusion 共用，函数体当前完全一致）。

    - 容忍 `db.refresh` 失败（任务并发收尾的常见场景）
    - 仅在 status 仍为 queued/running 时切换至 dispatch 结果对应状态
    - `last_stage` 由调用方决定，默认 `queued`
    """

    try:
        await db.refresh(run)
    except Exception:
        pass
    run.current_arq_job_id = arq_job_id
    if run.status in RETRY_RUNNING_STATUSES:
        run.status = (
            TaskRunStatus.running.value
            if queue_mode == "asyncio_fallback"
            else TaskRunStatus.queued.value
        )
    if last_stage:
        run.last_stage = last_stage
    await db.commit()


async def _mark_task_run_by_task_name(
    db: AsyncSession,
    *,
    project_id: int,
    config: FlowRetryConfig,
    task_name: str,
    status: str,
    stage: str,
    last_error: str | None = None,
) -> None:
    """按 task_name 标记最近一次显式重试 attempt。普通任务没有 TaskRun 时跳过。"""

    result = await db.execute(
        select(TaskRun)
        .where(
            TaskRun.project_id == project_id,
            TaskRun.flow == config.flow_constant,
            TaskRun.task_name == task_name,
        )
        .order_by(TaskRun.attempt.desc(), TaskRun.id.desc())
        .limit(1)
    )
    latest = result.scalar_one_or_none()
    if latest is None:
        return
    latest.status = status
    latest.last_stage = stage
    if last_error is not None:
        latest.last_error = str(last_error).strip().splitlines()[0][:500]
    elif status == TaskRunStatus.completed.value:
        latest.last_error = None
    if status == TaskRunStatus.completed.value:
        freeze_completed_duration(latest)
    await db.commit()


async def _build_retry_state(
    db: AsyncSession,
    project: Project,
    *,
    config: FlowRetryConfig,
    context: ReviewRetryContext | CollusionRetryContext | None,
    context_cls: type[ReviewRetryContext] | type[CollusionRetryContext],
    infer_context_fn: Callable[
        [AsyncSession, Project],
        Awaitable[ReviewRetryContext | CollusionRetryContext],
    ],
) -> dict[str, Any]:
    """retry-state 公共编排（review / collusion 共用，与原 build_*_retry_state 行为对齐）。

    - `config.extra_last_error_fn` 不为 None（串标）时预读 latest_any，既用于 retry_running
      二级判定也用于 last_error fallback；为 None（投标）时按需读取 latest_any，避免引入
      额外 DB 查询。
    - 最终 `last_error` 优先级：当前 spec node 的 `latest.last_error` → 业务专属 fallback
      （由 `extra_last_error_fn` 提供）→ `project.error_message`
      （retryable 或 retry_running 时回退，避免重试中弹窗空原因）。
    - 返回字段与 `build_retry_payload` 输出 byte-equal，被前端 / 测试断言强约束。
    """

    retryable = project.status == ProjectStatus.FAILED
    latest_any: TaskRun | None = None
    if config.extra_last_error_fn is not None:
        latest_any = await _latest_task_run(db, project.id, config=config)

    if context is None:
        if retryable:
            context = await infer_context_fn(db, project)
        else:
            if latest_any is None:
                latest_any = await _latest_task_run(db, project.id, config=config)
            if _is_retry_running(latest_any, config=config):
                target_id: int | None = None
                try:
                    reset_summary = json.loads(latest_any.reset_scope_json or "{}")
                    if isinstance(reset_summary, dict):
                        raw_target = reset_summary.get("target_id")
                        target_id = int(raw_target) if raw_target is not None else None
                except (TypeError, ValueError, json.JSONDecodeError):
                    target_id = None
                context = context_cls(latest_any.node_key, target_id=target_id)
            else:
                context = context_cls(config.default_node_key)

    spec = config.retry_specs.get(
        context.node_key, config.retry_specs[config.default_node_key]
    )
    latest = await _latest_task_run(db, project.id, config=config, node_key=spec.node_key)
    retry_running = _is_retry_running(latest, config=config)

    failed_stage = None
    if retryable or retry_running:
        failed_stage = latest.last_stage if latest and latest.last_stage else spec.failed_stage

    last_error: str | None = None
    if latest and latest.last_error:
        last_error = latest.last_error
    if not last_error and config.extra_last_error_fn is not None:
        last_error = config.extra_last_error_fn(latest_any)
    if not last_error and (retryable or retry_running):
        last_error = project.error_message

    # 源文件不可读属不可恢复输入问题, 阻断无效重跑并引导重传;
    # 串标与投标审查共用 document_process 节点语义, 两个流程一并拦截。
    if (
        config in (COLLUSION_FLOW_CONFIG, REVIEW_FLOW_CONFIG)
        and spec.node_key == "document_process"
        and is_unrecoverable_document_error(last_error)
    ):
        return build_unrecoverable_document_retry_payload(
            spec,
            target_id=context.target_id,
            last_error=last_error,
        )

    return build_retry_payload(
        spec,
        retryable=retryable,
        retry_running=retry_running,
        last_error=last_error,
        extra={
            "target_id": context.target_id,
            "failed_stage": failed_stage,
            "last_attempt": int(latest.attempt) if latest else 0,
        },
    )


# ============================================================
# 投标审查公共 API（thin forwarder + 业务专属逻辑）
# ============================================================


async def latest_review_task_run(
    db: AsyncSession,
    project_id: int,
    node_key: str | None = None,
) -> TaskRun | None:
    return await _latest_task_run(
        db, project_id, config=REVIEW_FLOW_CONFIG, node_key=node_key
    )


def is_review_retry_running(latest: TaskRun | None) -> bool:
    """用 TaskRun + liveness 双证据判断显式重试是否仍在运行。"""

    return _is_retry_running(latest, config=REVIEW_FLOW_CONFIG)


async def _next_review_attempt(
    db: AsyncSession,
    project_id: int,
    node_key: str,
) -> int:
    return await _next_attempt(
        db, project_id, config=REVIEW_FLOW_CONFIG, node_key=node_key
    )


async def begin_review_retry_attempt(
    db: AsyncSession,
    *,
    project_id: int,
    node_key: str,
    task_name: str,
    user_id: int | None,
    target_id: int | None = None,
    last_stage: str | None = "retry_requested",
) -> TaskRun:
    """创建投标审查显式重试 attempt 记录。"""

    return await _begin_retry_attempt(
        db,
        project_id=project_id,
        config=REVIEW_FLOW_CONFIG,
        node_key=node_key,
        task_name=task_name,
        user_id=user_id,
        target_id=target_id,
        last_stage=last_stage,
    )


async def attach_review_dispatch_result(
    db: AsyncSession,
    run: TaskRun,
    *,
    queue_mode: str,
    arq_job_id: str | None,
    last_stage: str | None = "queued",
) -> None:
    """投标审查 dispatch 回填（forwarder）。"""

    await _attach_dispatch_result(
        db, run, queue_mode=queue_mode, arq_job_id=arq_job_id, last_stage=last_stage
    )


async def mark_review_task_run_by_task_name(
    db: AsyncSession,
    *,
    project_id: int,
    task_name: str,
    status: str,
    stage: str,
    last_error: str | None = None,
) -> None:
    """按 task_name 标记最近一次显式重试 attempt。普通任务没有 TaskRun 时跳过。"""

    await _mark_task_run_by_task_name(
        db,
        project_id=project_id,
        config=REVIEW_FLOW_CONFIG,
        task_name=task_name,
        status=status,
        stage=stage,
        last_error=last_error,
    )


async def _infer_review_context_for_build(
    db: AsyncSession, project: Project
) -> ReviewRetryContext:
    """`_build_retry_state` 期望 (db, project) 签名，此处适配 review 的 (db, project_id)。"""

    return await infer_review_retry_context(db, project.id)


async def build_review_retry_state(
    db: AsyncSession,
    project: Project,
    *,
    context: ReviewRetryContext | None = None,
) -> dict[str, Any]:
    """投标审查项目级 retry-state 编排（forwarder）。"""

    return await _build_retry_state(
        db,
        project,
        config=REVIEW_FLOW_CONFIG,
        context=context,
        context_cls=ReviewRetryContext,
        infer_context_fn=_infer_review_context_for_build,
    )


async def infer_review_retry_context(db: AsyncSession, project_id: int) -> ReviewRetryContext:
    documents = list(
        (
            await db.execute(
                select(Document).where(
                    Document.project_id == project_id,
                    Document.doc_type.in_((DocumentType.TENDER, DocumentType.BID)),
                )
            )
        ).scalars().all()
    )
    if not documents:
        return ReviewRetryContext("document_process")

    doc_ids = [doc.id for doc in documents]
    vector_states = {
        state.document_id: state
        for state in (
            await db.execute(
                select(VectorJobState).where(VectorJobState.document_id.in_(doc_ids))
            )
        ).scalars().all()
    }
    for doc in documents:
        state = vector_states.get(doc.id)
        if doc.parsed != 1 or (
            state is not None
            and state.status in {VectorJobStatus.FAILED, VectorJobStatus.LOST}
        ):
            return ReviewRetryContext("document_process", target_id=doc.id)

    # 重试推断的"应有响应/结果"分母与审查消费口径一致（已确认且可评审）
    req_ids = list(
        (
            await db.execute(
                select(Requirement.id).where(
                    Requirement.project_id == project_id,
                    *reviewable_requirement_conditions(),
                )
            )
        ).scalars().all()
    )
    if not req_ids:
        return ReviewRetryContext("requirement_extract")

    response_count = int(
        (
            await db.execute(
                select(func.count(func.distinct(BidResponse.requirement_id))).where(
                    BidResponse.requirement_id.in_(req_ids)
                )
            )
        ).scalar()
        or 0
    )
    if response_count < len(req_ids):
        return ReviewRetryContext("bid_response_extract")

    result_count = int(
        (
            await db.execute(
                select(func.count(func.distinct(ReviewResult.requirement_id))).where(
                    ReviewResult.project_id == project_id,
                    ReviewResult.requirement_id.in_(req_ids),
                )
            )
        ).scalar()
        or 0
    )
    if result_count < len(req_ids):
        return ReviewRetryContext("review_compare")

    return ReviewRetryContext("review_report")


async def infer_review_retry_node(db: AsyncSession, project_id: int) -> str:
    return (await infer_review_retry_context(db, project_id)).node_key


async def infer_review_retry_target_id(db: AsyncSession, project_id: int) -> int | None:
    return (await infer_review_retry_context(db, project_id)).target_id


async def _confirmed_review_requirement_ids(db: AsyncSession, project_id: int) -> list[int]:
    """已确认且可评审的要求 ID（: 与审查消费口径一致）。"""
    req_ids = list(
        (
            await db.execute(
                select(Requirement.id).where(
                    Requirement.project_id == project_id,
                    *reviewable_requirement_conditions(),
                )
            )
        ).scalars().all()
    )
    return req_ids


async def cleanup_review_retry_scope(
    db: AsyncSession,
    project_id: int,
    node_key: str,
    target_id: int | None = None,
) -> None:
    if node_key == "document_process":
        doc_filter = [Document.project_id == project_id]
        if target_id is not None:
            doc_filter.append(Document.id == target_id)
        doc_ids = list(
            (await db.execute(select(Document.id).where(*doc_filter))).scalars().all()
        )
        if not doc_ids:
            return
        await db.execute(
            update(Document)
            .where(Document.id.in_(doc_ids))
            .values(parsed=0)
        )
        await db.execute(
            update(VectorJobState)
            .where(VectorJobState.document_id.in_(doc_ids))
            .values(
                status=VectorJobStatus.PENDING,
                stage="retry_requested",
                progress="0/0",
                last_error=None,
                arq_job_id=None,
                worker_id=None,
            )
        )
        return

    if node_key == "requirement_extract":
        # 当前 Requirement 缺少 is_manual 来源标识；为避免误删人工补录，只有在没有已确认
        # 要求时清理未确认条目，使重试不会破坏用户已确认成果。
        confirmed_count = int(
            (
                await db.execute(
                    select(func.count(Requirement.id)).where(
                        Requirement.project_id == project_id,
                        Requirement.confirmed == 1,
                    )
                )
            ).scalar()
            or 0
        )
        if confirmed_count == 0:
            await db.execute(
                delete(Requirement).where(
                    Requirement.project_id == project_id,
                    Requirement.confirmed == 0,
                )
            )
        return

    req_ids = await _confirmed_review_requirement_ids(db, project_id)
    if not req_ids:
        return

    if node_key in {"bid_response_extract", "review_project"}:
        incomplete_response_ids = list(
            (
                await db.execute(
                    select(BidResponse.id).where(
                        BidResponse.requirement_id.in_(req_ids),
                        or_(
                            BidResponse.completeness.in_(("partial", "form_only")),
                            BidResponse.completeness.is_(None),
                        ),
                    )
                )
            ).scalars().all()
        )
        if incomplete_response_ids:
            await db.execute(
                delete(ReviewResult).where(
                    ReviewResult.project_id == project_id,
                    ReviewResult.bid_response_id.in_(incomplete_response_ids),
                )
            )
        await db.execute(
            delete(ReviewResult).where(
                ReviewResult.project_id == project_id,
                ReviewResult.requirement_id.in_(req_ids),
                ReviewResult.failure_state.isnot(None),
                ReviewResult.failure_state != "completed",
            )
        )
        await db.execute(
            delete(BidResponse).where(
                BidResponse.requirement_id.in_(req_ids),
                or_(
                    BidResponse.completeness.in_(("partial", "form_only")),
                    BidResponse.completeness.is_(None),
                ),
            )
        )
    elif node_key == "review_compare":
        await db.execute(
            delete(ReviewResult).where(
                ReviewResult.project_id == project_id,
                ReviewResult.failure_state != "completed",
            )
        )
    elif node_key == "review_report":
        return


def collusion_node_from_stage(stage: str | None) -> str | None:
    """将运行时 heartbeat/TaskRun 阶段映射为第五章串标失败节点。"""

    normalized = str(stage or "").strip().lower()
    if not normalized:
        return None
    if normalized in {"s1a", "s1b", "extracting", "extract_bid_docs"}:
        return "collusion_extract"
    if normalized in {"s2", "global_features", "global_features_done"}:
        return "collusion_global"
    if normalized in {"s3", "comparing", "prepare_pairs", "pair_completed"}:
        return "collusion_compare"
    if normalized in {"s3.5", "clustering", "build_graph", "groups_identified", "fuse_evidence"}:
        return "collusion_cluster"
    if normalized in {"s4", "synthesizing", "group_analysis", "overall_synthesis"}:
        return "collusion_synthesis"
    if normalized in {"步骤6", "reporting", "completed", "report"}:
        return "collusion_report"
    return None


async def latest_collusion_task_run(
    db: AsyncSession,
    project_id: int,
    node_key: str | None = None,
) -> TaskRun | None:
    return await _latest_task_run(
        db, project_id, config=COLLUSION_FLOW_CONFIG, node_key=node_key
    )


def is_collusion_retry_running(latest: TaskRun | None) -> bool:
    """用 TaskRun + liveness 双证据判断串标显式重试是否仍在运行。"""

    return _is_retry_running(latest, config=COLLUSION_FLOW_CONFIG)


async def _next_collusion_attempt(
    db: AsyncSession,
    project_id: int,
    node_key: str,
) -> int:
    return await _next_attempt(
        db, project_id, config=COLLUSION_FLOW_CONFIG, node_key=node_key
    )


async def begin_collusion_retry_attempt(
    db: AsyncSession,
    *,
    project_id: int,
    node_key: str,
    task_name: str,
    user_id: int | None,
    target_id: int | None = None,
    last_stage: str | None = "retry_requested",
) -> TaskRun:
    """创建串标审查显式重试 attempt 记录。"""

    return await _begin_retry_attempt(
        db,
        project_id=project_id,
        config=COLLUSION_FLOW_CONFIG,
        node_key=node_key,
        task_name=task_name,
        user_id=user_id,
        target_id=target_id,
        last_stage=last_stage,
    )


async def attach_collusion_dispatch_result(
    db: AsyncSession,
    run: TaskRun,
    *,
    queue_mode: str,
    arq_job_id: str | None,
    last_stage: str | None = "queued",
) -> None:
    """串标审查 dispatch 回填（forwarder）。"""

    await _attach_dispatch_result(
        db, run, queue_mode=queue_mode, arq_job_id=arq_job_id, last_stage=last_stage
    )


async def mark_collusion_task_run_by_task_name(
    db: AsyncSession,
    *,
    project_id: int,
    task_name: str,
    status: str,
    stage: str,
    last_error: str | None = None,
) -> None:
    """按 task_name 标记最近一次串标显式重试 attempt。普通任务没有 TaskRun 时跳过。"""

    await _mark_task_run_by_task_name(
        db,
        project_id=project_id,
        config=COLLUSION_FLOW_CONFIG,
        task_name=task_name,
        status=status,
        stage=stage,
        last_error=last_error,
    )


async def build_collusion_retry_state(
    db: AsyncSession,
    project: Project,
    *,
    context: CollusionRetryContext | None = None,
) -> dict[str, Any]:
    """串标审查项目级 retry-state 编排（forwarder）。"""

    return await _build_retry_state(
        db,
        project,
        config=COLLUSION_FLOW_CONFIG,
        context=context,
        context_cls=CollusionRetryContext,
        infer_context_fn=infer_collusion_retry_context,
    )


async def infer_collusion_retry_context(
    db: AsyncSession,
    project: Project,
) -> CollusionRetryContext:
    bid_doc_ids = list(
        (
            await db.execute(
                select(Document.id).where(
                    Document.project_id == project.id,
                    Document.doc_type == DocumentType.BID,
                )
            )
        ).scalars().all()
    )
    if not bid_doc_ids:
        return CollusionRetryContext("collusion_analyze")

    doc_ids = list(
        (
            await db.execute(
                select(Document.id).where(
                    Document.project_id == project.id,
                    Document.doc_type.in_((DocumentType.TENDER, DocumentType.BID)),
                )
            )
        ).scalars().all()
    )
    documents = list(
        (
            await db.execute(
                select(Document).where(Document.id.in_(doc_ids))
            )
        ).scalars().all()
    ) if doc_ids else []
    vector_states = {
        state.document_id: state
        for state in (
            await db.execute(
                select(VectorJobState).where(VectorJobState.document_id.in_(doc_ids))
            )
        ).scalars().all()
    } if doc_ids else {}
    for doc in documents:
        state = vector_states.get(doc.id)
        if doc.parsed != 1 or (
            state is not None
            and state.status in {VectorJobStatus.FAILED, VectorJobStatus.LOST}
        ):
            return CollusionRetryContext("document_process", target_id=doc.id)

    latest = await latest_collusion_task_run(db, project.id)
    latest_stage_node = collusion_node_from_stage(latest.last_stage if latest else None)
    heartbeat_stage_node = None
    try:
        from app.core.tasks import get_collusion_task_name, get_task_heartbeat

        heartbeat = get_task_heartbeat(get_collusion_task_name(project.id))
        heartbeat_stage_node = collusion_node_from_stage(
            heartbeat.get("stage") if isinstance(heartbeat, dict) else None
        )
    except Exception:
        heartbeat_stage_node = None
    if (
        latest is not None
        and latest.status == TaskRunStatus.failed.value
        and latest_stage_node in COLLUSION_RETRY_SPECS
    ):
        return CollusionRetryContext(latest_stage_node)

    success_extract_count = int(
        (
            await db.execute(
                select(func.count(CollusionExtraction.id)).where(
                    CollusionExtraction.project_id == project.id,
                    CollusionExtraction.document_id.in_(bid_doc_ids),
                    CollusionExtraction.extraction_status == "success",
                )
            )
        ).scalar()
        or 0
    )
    failed_extract_count = int(
        (
            await db.execute(
                select(func.count(CollusionExtraction.id)).where(
                    CollusionExtraction.project_id == project.id,
                    CollusionExtraction.document_id.in_(bid_doc_ids),
                    CollusionExtraction.extraction_status != "success",
                )
            )
        ).scalar()
        or 0
    )
    if success_extract_count < len(bid_doc_ids) or failed_extract_count > 0:
        return CollusionRetryContext("collusion_extract")

    if latest_stage_node == "collusion_global" or heartbeat_stage_node == "collusion_global":
        return CollusionRetryContext("collusion_global")

    total_pairs = len(bid_doc_ids) * (len(bid_doc_ids) - 1) // 2
    pair_count = int(
        (
            await db.execute(
                select(func.count(CollusionResult.id)).where(
                    CollusionResult.project_id == project.id
                )
            )
        ).scalar()
        or 0
    )
    failed_pair_count = int(
        (
            await db.execute(
                select(func.count(CollusionResult.id)).where(
                    CollusionResult.project_id == project.id,
                    CollusionResult.analysis_mode == "failed",
                )
            )
        ).scalar()
        or 0
    )
    if pair_count < total_pairs or failed_pair_count > 0:
        return CollusionRetryContext("collusion_compare")

    if not project.collusion_summary:
        if latest_stage_node == "collusion_cluster" or heartbeat_stage_node == "collusion_cluster":
            return CollusionRetryContext("collusion_cluster")
        return CollusionRetryContext("collusion_synthesis")
    return CollusionRetryContext("collusion_report")


async def infer_collusion_retry_node(db: AsyncSession, project: Project) -> str:
    return (await infer_collusion_retry_context(db, project)).node_key


async def infer_collusion_retry_target_id(db: AsyncSession, project: Project) -> int | None:
    return (await infer_collusion_retry_context(db, project)).target_id


async def cleanup_collusion_retry_scope(
    db: AsyncSession,
    project: Project,
    node_key: str,
    target_id: int | None = None,
) -> None:
    if node_key == "document_process":
        doc_filter = [Document.project_id == project.id]
        if target_id is not None:
            doc_filter.append(Document.id == target_id)
        doc_ids = list(
            (await db.execute(select(Document.id).where(*doc_filter))).scalars().all()
        )
        if not doc_ids:
            return
        await db.execute(
            update(Document)
            .where(Document.id.in_(doc_ids))
            .values(parsed=0)
        )
        await db.execute(
            update(VectorJobState)
            .where(VectorJobState.document_id.in_(doc_ids))
            .values(
                status=VectorJobStatus.PENDING,
                stage="retry_requested",
                progress="0/0",
                last_error=None,
                arq_job_id=None,
                worker_id=None,
            )
        )
        return

    if node_key in {"collusion_extract", "collusion_analyze"}:
        await db.execute(
            delete(CollusionExtraction).where(
                CollusionExtraction.project_id == project.id,
                CollusionExtraction.extraction_status != "success",
            )
        )
        if node_key == "collusion_analyze":
            await db.execute(
                delete(CollusionResult).where(
                    CollusionResult.project_id == project.id,
                    CollusionResult.analysis_mode == "failed",
                )
            )
            await db.execute(delete(CollusionGroup).where(CollusionGroup.project_id == project.id))
            project.collusion_summary = None
        project.collusion_summary = None
    elif node_key == "collusion_global":
        project.collusion_summary = None
    elif node_key == "collusion_compare":
        await db.execute(
            delete(CollusionResult).where(
                CollusionResult.project_id == project.id,
                CollusionResult.analysis_mode == "failed",
            )
        )
        await db.execute(delete(CollusionGroup).where(CollusionGroup.project_id == project.id))
        project.collusion_summary = None
    elif node_key == "collusion_cluster":
        await db.execute(delete(CollusionGroup).where(CollusionGroup.project_id == project.id))
        project.collusion_summary = None
    elif node_key == "collusion_synthesis":
        project.collusion_summary = None
    elif node_key == "collusion_report":
        return
