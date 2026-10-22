"""ARQ Worker 定义

独立进程消费 Redis 任务队列, 执行文档处理/提取/审查/串标分析。
启动方式: cd backend && arq app.worker.WorkerSettings

alive 心跳:
- 启动后立即写入 Redis: bidmind:arq_worker:alive:{worker_id}
- 启动一个后台协程按 ARQ_WORKER_HEARTBEAT_INTERVAL 周期刷新
- 关闭时主动删除键, 避免 API 侧把已下线 worker 误判为存活
"""

import asyncio
import sys
import uuid
from collections.abc import Awaitable, Callable

from arq.cron import cron
from arq.worker import func as arq_func
from loguru import logger

from app.core.arq_runtime import (
    clear_worker_heartbeat_async,
    generate_worker_id,
    heartbeat_refresh_loop,
    is_arq_job_cancelled_async,
    is_project_cancelled_async,
    record_worker_heartbeat_async,
)
from app.core.config import settings
from app.core.middleware import get_request_id
from app.core.redis import REDIS_SETTINGS


def _new_incident_id() -> str:
    """为每个 worker job 生成独立 incident_id, 与 HTTP 请求级链路区分。

    16 位 hex 与 middleware.new_incident_id 保持一致, 便于跨进程串联。
    """
    return uuid.uuid4().hex[:16]


# ── Worker 独立 Loguru 初始化 ────────────────────────────────────
# ARQ 不经过 main.py,需要在此模块顶层配置日志

def _log_patcher(record: dict) -> None:
    record["extra"]["request_id"] = get_request_id()


logger.remove()
if settings.LOG_FORMAT == "json":
    logger.add(sys.stderr, level=settings.LOG_LEVEL, serialize=True, backtrace=False)
else:
    logger.add(
        sys.stderr,
        level=settings.LOG_LEVEL,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
            "<dim>{extra[request_id]}</dim> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )
logger.configure(patcher=_log_patcher)

from app.core.log_sink import db_log_sink  # noqa: E402

logger.add(db_log_sink, level="WARNING", format="{message}")


# ── CPU 预算前置应用 ────────────────────────────────────────────
# 必须在任何业务模块 (会触发 import torch / sentence_transformers) 之前完成,
# 让 OMP/MKL/ORT 线程池在首次创建前就拿到正确的上限。
# 失败仅记录警告, 不阻塞 worker 启动 (退化为系统默认线程数)。
try:
    from app.core.runtime.cpu_budget import apply_env_thread_limits  # noqa: E402

    apply_env_thread_limits()
except Exception as _cpu_budget_exc:  # noqa: BLE001
    logger.warning(
        f"[Worker] CPU 预算环境变量应用失败 (退化为系统默认): {_cpu_budget_exc}"
    )


def _worker_heartbeat_extra(ctx: dict) -> dict:
    """Build best-effort worker heartbeat fields for operations and self-drain."""

    extra = {
        "state": ctx.get("bidmind_worker_state", "idle"),
        "current_function": ctx.get("bidmind_current_function"),
        "current_resource": ctx.get("bidmind_current_resource"),
    }
    try:
        from app.core.gpu.admission import get_gpu_snapshot
        from app.core.tasks.resource_permit import get_resource_inflight_sync
        from app.core.vectorizer.encode_gate import embedding_runtime_stats

        snapshot = get_gpu_snapshot()
        extra.update({
            "gpu_admission_enabled": bool(getattr(settings, "GPU_ADMISSION_ENABLED", True)),
            "gpu_inflight": get_resource_inflight_sync("gpu"),
            "gpu_free_mb": snapshot.free_mb if snapshot else None,
            "gpu_snapshot_source": snapshot.source if snapshot else None,
            **embedding_runtime_stats(),
        })
    except Exception:
        # Heartbeat must remain best-effort; diagnostics must never stop workers.
        extra.update({
            "gpu_admission_enabled": bool(getattr(settings, "GPU_ADMISSION_ENABLED", True)),
            "gpu_inflight": None,
            "gpu_free_mb": None,
            "cuda_model_loaded": None,
            "cpu_fallback_model_loaded": None,
        })
    return extra


PROCESS_WORKER_FUNCTION_NAMES: list[str] = [
    "process_document_job",
    "document_qa_job",
    "knowledge_base_process_job",
    "writing_material_process_job",
    "writing_clause_vectorize_job",
]

LLM_WORKER_FUNCTION_NAMES: list[str] = [
    "extract_requirements_job",
    "review_bid_extract_job",
    "review_project_job",
    "collusion_analyze_job",
    "document_image_vl_job",
    "writing_image_vl_job",
    "writing_image_classify_job",
    "writing_extract_job",
    "writing_outline_job",
    "writing_match_job",
    "writing_compose_job",
    "writing_compose_single_job",
    "express_advance_job",
    "document_qa_verify_job",
]

REPORT_WORKER_FUNCTION_NAMES: list[str] = []

MAINTENANCE_WORKER_FUNCTION_NAMES: list[str] = [
    "flush_metrics_job",
    "regression_runner_job",
    "liveness_sweep_job",
    "prune_observability_logs_job",
    "weekly_llm_advice_job",
]

# 当前 worker 实例支持的任务函数名列表, API 侧用 has_alive_worker_for(name) 精确匹配
WORKER_FUNCTION_NAMES: list[str] = [
    *PROCESS_WORKER_FUNCTION_NAMES,
    *LLM_WORKER_FUNCTION_NAMES,
    *MAINTENANCE_WORKER_FUNCTION_NAMES,
]


async def flush_metrics_job(ctx: dict) -> dict:
    """Phase 3: 定期把 Redis 指标热层 flush 到 metric_snapshots 温层."""
    try:
        from app.core.observability.storage import flush_to_snapshots
        result = await flush_to_snapshots()
        logger.debug(f"[flush_metrics] 完成: {result}")
        return result
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[flush_metrics] 执行失败 (忽略): {e}")
        return {"flushed": 0, "skipped": 0, "error": str(e)}


async def liveness_sweep_job(ctx: dict) -> dict:
    """主动失活巡检: 由 maintenance cron 每分钟触发。

 复用既有惰性自愈链(get_review_status / compute_collusion_status /
 get_project_status / reconcile_document_processing), 把"任务死亡只在
 用户查询时被发现"的惰性架构补为主动触发, 失败/异常一律静默吞掉, 不影响下一轮。
 """
    if not bool(getattr(settings, "LIVENESS_SWEEP_ENABLED", True)):
        return {"enabled": False}

    try:
        from app.core.database import AsyncSessionLocal
        from app.services.liveness.liveness_reaper_service import sweep_stale_projects

        async with AsyncSessionLocal() as db:
            stats = await sweep_stale_projects(db)
        return {
            "scanned": stats.scanned,
            "review": stats.review_reconciled,
            "collusion": stats.collusion_reconciled,
            "writing": stats.writing_reconciled,
            "docproc": stats.docproc_reconciled,
            "skipped_no_owner": stats.skipped_no_owner,
            "skipped_error": stats.skipped_error,
            "docproc_outcomes": dict(stats.docproc_outcomes),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[liveness_sweep] 执行失败 (忽略): {e}")
        return {"error": str(e)}


async def prune_observability_logs_job(ctx: dict) -> dict:
    """差异化 TTL 清理可观测日志 (maintenance cron 每日触发)。

    错误链路久存(默认30天)、正常事件短存(默认7天); 失败静默吞掉不影响下一轮。
    保留天数可经 settings 覆盖 (OBSERVABILITY_ERROR/NORMAL_RETENTION_DAYS)。
    """
    if not bool(getattr(settings, "LOG_RETENTION_PRUNE_ENABLED", True)):
        return {"enabled": False}
    try:
        from app.core.database import AsyncSessionLocal
        from app.services.log_retention_service import (
            DEFAULT_ERROR_RETENTION_DAYS,
            DEFAULT_NORMAL_RETENTION_DAYS,
            prune_observability_logs,
        )

        error_days = int(getattr(
            settings, "OBSERVABILITY_ERROR_RETENTION_DAYS", DEFAULT_ERROR_RETENTION_DAYS
        ))
        normal_days = int(getattr(
            settings, "OBSERVABILITY_NORMAL_RETENTION_DAYS", DEFAULT_NORMAL_RETENTION_DAYS
        ))
        async with AsyncSessionLocal() as db:
            result = await prune_observability_logs(
                db,
                error_retention_days=error_days,
                normal_retention_days=normal_days,
            )
        logger.info(f"[log_retention] 清理完成: {result}")
        return result
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[log_retention] 执行失败 (忽略): {e}")
        return {"error": str(e)}


async def weekly_llm_advice_job(ctx: dict) -> dict:
    """周度 LLM 调度建议分析: 周一 00:00（北京时间，即周日 24:00）触发。

    决策真源为确定性算法; 本 job 只生成建议落库, 不自动写回 priority。
    运行环境全局时区为 Asia/Shanghai（北京/重庆/上海），cron 按本地时区解释。
    """
    if not bool(getattr(settings, "WEEKLY_LLM_ADVICE_ENABLED", True)):
        return {"enabled": False}
    try:
        from app.core.database import AsyncSessionLocal
        from app.services.ops_advisor.weekly_reliability_report import (
            generate_and_persist_weekly_advice,
        )

        async with AsyncSessionLocal() as db:
            result = await generate_and_persist_weekly_advice(db)
        logger.info(f"[weekly_llm_advice] 完成: {result}")
        return result
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[weekly_llm_advice] 执行失败 (忽略): {e}")
        return {"error": str(e)}


async def regression_runner_job(ctx: dict, flow: str = "all") -> dict:
    """Phase 5: 一键触发的回归 runner (占位异步包装).

    后续 Phase 6 会把 scripts/regression/runner.py 入口接入这里, 并在结尾把稳定率
    指标直接写入 metric_snapshots. 当前仅打 log + 标记任务存在.
    """
    logger.info(f"[regression_runner] 触发: flow={flow}")
    try:
        # 占位: 尝试导入脚本模块, 存在则简单调用入口函数
        # (实际脚本签名未知, 这里不真正执行业务, 避免误触发大规模回归)
        result = {"flow": flow, "status": "queued", "message": "runner 占位实现 (Phase 5)"}
        return result
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[regression_runner] 失败 (忽略): {e}")
        return {"flow": flow, "status": "error", "error": str(e)}


# ── 入口级取消检查 (防止领到过期任务时跑业务) ──────────────────────

async def _should_abort_before_run(
    ctx: dict,
    *,
    project_id: int,
    task_type: str,
) -> bool:
    """worker 每个 job 入口统一调用: 项目墓碑命中或 job_id 在黑名单即直接退出。

    返回 True 表示"已决定跳过执行", 调用方应立即 return。
    """
    redis = ctx.get("redis")
    job_id = ctx.get("job_id")

    if await is_arq_job_cancelled_async(redis, job_id):
        logger.info(
            f"[Worker] 跳过已被取消的 ARQ 任务: task_type={task_type}, "
            f"project_id={project_id}, job_id={job_id}"
        )
        return True

    if await is_project_cancelled_async(redis, project_id):
        logger.info(
            f"[Worker] 项目已删除/取消, 跳过任务: task_type={task_type}, "
            f"project_id={project_id}, job_id={job_id}"
        )
        return True

    return False


async def _run_with_resource_permit(
    ctx: dict,
    function_name: str,
    operation: Callable[[], Awaitable[None]],
    *,
    project_id: int | None = None,
    task_type: str | None = None,
    node_id: int | None = None,
) -> None:
    """按 job 函数申请资源许可后执行任务。

 资源满时仅等待, 不跳过业务, 保证三大流程原有顺序仍由各 run_*_task 内部维护。
 若项目在等待资源期间被删除, 获取许可后必须二次检查取消墓碑并跳过业务。

 所有 ARQ job 经此函数都会被套入 ``task_cpu_sticky_scope`` 任务作用域:
 一旦本 job 内任一 encode 触发 hard CPU fallback, 同 job 后续所有 encode
 自动跳 GPU; job 结束自动清理, 不污染其它 job。: 长 LLM job 在获取许可后由无进展看门狗包裹；排队等待不计入无进展时长。: 长 LLM job 同时套任务心跳保活，避免条目边界之间的合法长 await
 把 last_heartbeat 墙钟写成失活。
 """
    from app.core.tasks.fairness.project_admission import claim_project_share_or_defer
    from app.core.tasks.lifecycle.long_job_registry import (
        LONG_LLM_JOBS,
        resolve_task_name,
    )
    from app.core.tasks.lifecycle.progress_watchdog import (
        TaskProgressStalledError,
        await_with_progress_guard,
    )
    from app.core.tasks.lifecycle.stalled_task_finalizer import finalize_stalled_task
    from app.core.tasks.resource_permit import build_permit_owner, permit_context
    from app.core.tasks.resource_policy import policy_for_function
    from app.core.vectorizer.encode_gate import task_cpu_sticky_scope

    policy = policy_for_function(function_name)
    redis = ctx.get("redis")
    ctx["bidmind_worker_state"] = "waiting_permit"
    ctx["bidmind_current_function"] = function_name
    ctx["bidmind_current_resource"] = policy.resource_type
    # owner 在排队循环外只生成一次, 保证同一 job 的每轮申请与释放共用同一个 token
    permit_owner = build_permit_owner(
        worker_id=ctx.get("bidmind_worker_id"),
        function_name=function_name,
        job_id=ctx.get("job_id"),
        project_id=project_id,
    )
    # 公平准入必须在资源许可之前: 许可满时是原地等待, 会把 worker 槽位占死;
    # 份额不足时这里直接抛 Retry 退回队列, 槽位立刻让给其它项目。
    await claim_project_share_or_defer(
        ctx,
        resource_type=policy.resource_type,
        project_id=project_id,
        owner=permit_owner,
        capacity=policy.max_jobs,
    )
    while True:
        async with permit_context(
            redis,
            policy.resource_type,
            limit=policy.max_jobs,
            owner=permit_owner,
        ) as acquired:
            if acquired:
                if project_id is not None and await is_project_cancelled_async(redis, project_id):
                    logger.info(
                        f"[Worker] 项目已删除/取消, 资源许可后跳过任务: "
                        f"task_type={task_type or function_name}, "
                        f"project_id={project_id}, fn={function_name}"
                    )
                    ctx["bidmind_worker_state"] = "idle"
                    ctx["bidmind_current_function"] = None
                    ctx["bidmind_current_resource"] = None
                    return
                ctx["bidmind_worker_state"] = "running"
                logger.debug(
                    f"[Worker] 已获取资源许可: fn={function_name}, "
                    f"resource={policy.resource_type}, limit={policy.max_jobs}"
                )
                try:
                    from app.core.observability.metrics import worker_job_started_total

                    worker_job_started_total.labels(
                        function=function_name,
                        resource_type=policy.resource_type,
                        queue_mode="arq",
                    ).inc()
                except Exception:
                    pass
                try:
                    with task_cpu_sticky_scope():
                        guard_enabled = bool(
                            getattr(settings, "WORKER_TASK_NO_PROGRESS_GUARD_ENABLED", True)
                        )
                        task_name = (
                            resolve_task_name(
                                function_name, project_id, node_id=node_id
                            )
                            if project_id is not None
                            else None
                        )
                        if function_name in LONG_LLM_JOBS and task_name:
                            from app.core.tasks.heartbeat_keepalive import (
                                await_with_task_keepalive,
                                current_heartbeat_stage_progress,
                            )

                            running = operation()
                            if guard_enabled:
                                running = await_with_progress_guard(
                                    running,
                                    task_name=task_name,
                                    max_no_progress_sec=float(
                                        getattr(
                                            settings,
                                            "WORKER_TASK_NO_PROGRESS_TIMEOUT",
                                            1800,
                                        )
                                    ),
                                )
                            try:
                                await await_with_task_keepalive(
                                    running,
                                    task_name=task_name,
                                    stage=lambda: current_heartbeat_stage_progress(
                                        task_name
                                    )[0],
                                    progress=lambda: current_heartbeat_stage_progress(
                                        task_name
                                    )[1],
                                )
                            except TaskProgressStalledError as stalled:
                                if project_id is not None:
                                    await finalize_stalled_task(
                                        project_id,
                                        task_name,
                                        stalled.no_progress_sec,
                                        function_name=function_name,
                                    )
                                raise
                        else:
                            await operation()
                    return
                finally:
                    ctx["bidmind_worker_state"] = "idle"
                    ctx["bidmind_current_function"] = None
                    ctx["bidmind_current_resource"] = None
        if project_id is not None and await is_project_cancelled_async(redis, project_id):
            logger.info(
                f"[Worker] 项目已删除/取消, 资源等待后停止排队: "
                f"task_type={task_type or function_name}, "
                f"project_id={project_id}, fn={function_name}"
            )
            ctx["bidmind_worker_state"] = "idle"
            ctx["bidmind_current_function"] = None
            ctx["bidmind_current_resource"] = None
            return
        try:
            from app.core.observability.metrics import worker_job_blocked_total

            worker_job_blocked_total.labels(
                function=function_name,
                resource_type=policy.resource_type,
            ).inc()
        except Exception:
            pass
        logger.warning(
            f"[Worker] 资源许可等待超时, 继续排队: fn={function_name}, "
            f"resource={policy.resource_type}, limit={policy.max_jobs}"
        )
        await asyncio.sleep(float(getattr(settings, "WORKER_RESOURCE_PERMIT_POLL_INTERVAL", 1.0)))


async def _run_with_prompt_snapshot(
    flow: str,
    operation: Callable[[], Awaitable[None]],
) -> None:
    """在 PromptRegistry 快照上下文中运行 LLM 相关任务。"""

    from app.core.ai.prompt_registry import bind_task_snapshot, get_registry

    registry = get_registry()
    try:
        if not registry.is_loaded:
            from app.core.database import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                await registry.load(db)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"[Worker] PromptRegistry 任务前预热失败, 将使用现有缓存: flow={flow}, err={e}"
        )

    async with bind_task_snapshot(flow):
        await operation()


# ── 任务函数 ─────────────────────────────────────────────────────

async def process_document_job(
    ctx: dict,
    project_id: int,
    document_id: int,
    skip_vl: bool = False,
) -> None:
    """文档处理 (PDF解析 + 分块 + 向量化)

    skip_vl 不在 Worker 入口透传: run_process_task 内部通过
    resolve_effective_skip_vl() 按项目类型+文档类型自行裁决。
    """
    from app.core.tasks.process_task import run_process_task

    worker_id = ctx.get("bidmind_worker_id")
    with logger.contextualize(
        task_type="process", project_id=project_id, source="worker",
        worker_id=worker_id, incident_id=_new_incident_id(),
    ):
        if await _should_abort_before_run(ctx, project_id=project_id, task_type="process"):
            return
        logger.info(
            f"[Worker] process_document_job: project={project_id}, doc={document_id}, "
            f"skip_vl={skip_vl}, worker_id={worker_id}"
        )
        await _run_with_resource_permit(
            ctx,
            "process_document_job",
            lambda: run_process_task(
                project_id,
                document_id,
                skip_vl=skip_vl,
                queue_mode="arq",
                worker_id=worker_id,
            ),
            project_id=project_id,
            task_type="process",
        )


async def knowledge_base_process_job(ctx: dict, document_id: int) -> None:
    """知识库资料处理 (解析/OCR/VL/分块/向量化)。"""
    from app.core.tasks.knowledge_base_task import run_knowledge_base_process_task

    worker_id = ctx.get("bidmind_worker_id")
    with logger.contextualize(
        task_type="knowledge_base_process",
        source="worker",
        worker_id=worker_id,
        incident_id=_new_incident_id(),
    ):
        logger.info(f"[Worker] knowledge_base_process_job: document_id={document_id}")
        await _run_with_resource_permit(
            ctx,
            "knowledge_base_process_job",
            lambda: run_knowledge_base_process_task(document_id),
        )


async def extract_requirements_job(
    ctx: dict,
    project_id: int,
    run_id: str | None = None,
    generation: int | None = None,
) -> None:
    """招标要求提取"""
    from app.core.tasks.extract_task import run_extract_task

    with logger.contextualize(
        task_type="extract", project_id=project_id, source="worker",
        incident_id=_new_incident_id(),
    ):
        if await _should_abort_before_run(ctx, project_id=project_id, task_type="extract"):
            return
        logger.info(f"[Worker] extract_requirements_job: project={project_id}")
        await _run_with_resource_permit(
            ctx,
            "extract_requirements_job",
            lambda: _run_with_prompt_snapshot(
                "bid_review",
                lambda: run_extract_task(
                    project_id,
                    run_id=run_id,
                    generation_number=generation,
                ),
            ),
            project_id=project_id,
            task_type="extract",
        )


async def review_project_job(ctx: dict, project_id: int) -> None:
    """投标审查 (响应提取 + 匹配 + AI审查 + 报告)"""
    from app.core.tasks.review_task import run_review_task

    with logger.contextualize(
        task_type="review", project_id=project_id, source="worker",
        incident_id=_new_incident_id(),
    ):
        if await _should_abort_before_run(ctx, project_id=project_id, task_type="review"):
            return
        logger.info(f"[Worker] review_project_job: project={project_id}")
        await _run_with_resource_permit(
            ctx,
            "review_project_job",
            lambda: _run_with_prompt_snapshot("bid_review", lambda: run_review_task(project_id)),
            project_id=project_id,
            task_type="review",
        )


async def review_bid_extract_job(ctx: dict, project_id: int) -> None:
    """投标审查 step: 投标响应提取"""
    from app.core.tasks.review_bid_extract_task import run_review_bid_extract_task

    with logger.contextualize(
        task_type="review_bid_extract", project_id=project_id, source="worker",
        incident_id=_new_incident_id(),
    ):
        if await _should_abort_before_run(
            ctx,
            project_id=project_id,
            task_type="review_bid_extract",
        ):
            return
        logger.info(f"[Worker] review_bid_extract_job: project={project_id}")
        await _run_with_resource_permit(
            ctx,
            "review_bid_extract_job",
            lambda: _run_with_prompt_snapshot(
                "bid_review",
                lambda: run_review_bid_extract_task(project_id),
            ),
            project_id=project_id,
            task_type="review_bid_extract",
        )


async def collusion_analyze_job(ctx: dict, project_id: int) -> None:
    """串标分析 (信息提取 + 交叉对比 + 综合研判)"""
    from app.core.tasks.collusion_task import run_collusion_task

    with logger.contextualize(
        task_type="collusion", project_id=project_id, source="worker",
        incident_id=_new_incident_id(),
    ):
        if await _should_abort_before_run(ctx, project_id=project_id, task_type="collusion"):
            return
        logger.info(f"[Worker] collusion_analyze_job: project={project_id}")
        await _run_with_resource_permit(
            ctx,
            "collusion_analyze_job",
            lambda: _run_with_prompt_snapshot("collusion", lambda: run_collusion_task(project_id)),
            project_id=project_id,
            task_type="collusion",
        )


async def document_image_vl_job(
    ctx: dict,
    project_id: int,
    business_flow: str,
    batch_id: str,
    items: list[dict],
) -> None:
    """投标审查/串标审查文档图片 VL 子任务。"""
    from app.core.tasks.document_image_llm_task import run_document_image_vl_batch

    worker_id = ctx.get("bidmind_worker_id")
    flow = "collusion" if business_flow == "collusion" else "bid_review"
    with logger.contextualize(
        task_type="document_image_vl",
        project_id=project_id,
        source="worker",
        worker_id=worker_id,
        incident_id=_new_incident_id(),
    ):
        if await _should_abort_before_run(
            ctx,
            project_id=project_id,
            task_type="document_image_vl",
        ):
            return
        logger.info(
            "[Worker] document_image_vl_job: project={}, flow={}, batch={}, items={}",
            project_id,
            flow,
            batch_id,
            len(items),
        )
        await _run_with_resource_permit(
            ctx,
            "document_image_vl_job",
            lambda: _run_with_prompt_snapshot(
                flow,
                lambda: run_document_image_vl_batch(
                    ctx,
                    project_id,
                    flow,
                    batch_id,
                    items,
                ),
            ),
            project_id=project_id,
            task_type="document_image_vl",
        )


# ── 写标系列任务 ────────────────────────────────────────────────
# 沿用各自 `run_writing_*_task` 的现有 heartbeat 合同, worker 仅作为调度承载。

async def writing_extract_job(
    ctx: dict,
    project_id: int,
    explicit_retry: bool = False,
    expected_attempt: int | None = None,
) -> None:
    """写标阶段 1: 招标约束三层提取 + 目录规格分析"""
    from app.core.tasks.writing_extract_task import run_writing_extract_task

    worker_id = ctx.get("bidmind_worker_id")
    with logger.contextualize(
        task_type="writing_extract", project_id=project_id, source="worker",
        worker_id=worker_id, incident_id=_new_incident_id(),
    ):
        if await _should_abort_before_run(ctx, project_id=project_id, task_type="writing_extract"):
            return
        logger.info(f"[Worker] writing_extract_job: project={project_id}")
        await _run_with_resource_permit(
            ctx,
            "writing_extract_job",
            lambda: _run_with_prompt_snapshot(
                "writing",
                lambda: run_writing_extract_task(project_id, explicit_retry, expected_attempt),
            ),
            project_id=project_id,
            task_type="writing_extract",
        )


async def writing_outline_job(ctx: dict, project_id: int, explicit_retry: bool = False) -> None:
    """写标阶段 2: 目录树生成"""
    from app.core.tasks.writing_outline_task import run_writing_outline_task

    worker_id = ctx.get("bidmind_worker_id")
    with logger.contextualize(
        task_type="writing_outline", project_id=project_id, source="worker",
        worker_id=worker_id, incident_id=_new_incident_id(),
    ):
        if await _should_abort_before_run(ctx, project_id=project_id, task_type="writing_outline"):
            return
        logger.info(f"[Worker] writing_outline_job: project={project_id}")
        await _run_with_resource_permit(
            ctx,
            "writing_outline_job",
            lambda: _run_with_prompt_snapshot(
                "writing",
                lambda: run_writing_outline_task(project_id, explicit_retry),
            ),
            project_id=project_id,
            task_type="writing_outline",
        )


async def writing_material_process_job(ctx: dict, project_id: int) -> None:
    """写标阶段 2.5: 素材批处理(解析/VL/索引/分类/打标)"""
    from app.core.tasks.writing_material_process_task import run_writing_material_process_task

    worker_id = ctx.get("bidmind_worker_id")
    with logger.contextualize(
        task_type="writing_material_process",
        project_id=project_id,
        source="worker",
        worker_id=worker_id,
        incident_id=_new_incident_id(),
    ):
        if await _should_abort_before_run(
            ctx, project_id=project_id, task_type="writing_material_process"
        ):
            return
        logger.info(f"[Worker] writing_material_process_job: project={project_id}")
        await _run_with_resource_permit(
            ctx,
            "writing_material_process_job",
            lambda: _run_with_prompt_snapshot(
                "writing",
                lambda: run_writing_material_process_task(project_id),
            ),
            project_id=project_id,
            task_type="writing_material_process",
        )


async def writing_image_vl_job(
    ctx: dict,
    project_id: int,
    batch_id: str,
    items: list[dict],
) -> None:
    """写标素材图片 VL fallback 子任务。"""
    from app.core.tasks.writing_image_llm_task import run_image_vl_batch

    worker_id = ctx.get("bidmind_worker_id")
    with logger.contextualize(
        task_type="writing_image_vl",
        project_id=project_id,
        source="worker",
        worker_id=worker_id,
        incident_id=_new_incident_id(),
    ):
        if await _should_abort_before_run(ctx, project_id=project_id, task_type="writing_image_vl"):
            return
        logger.info(
            "[Worker] writing_image_vl_job: project={}, batch={}, items={}",
            project_id,
            batch_id,
            len(items),
        )
        await _run_with_resource_permit(
            ctx,
            "writing_image_vl_job",
            lambda: _run_with_prompt_snapshot(
                "writing",
                lambda: run_image_vl_batch(ctx, project_id, batch_id, items),
            ),
            project_id=project_id,
            task_type="writing_image_vl",
        )


async def writing_image_classify_job(
    ctx: dict,
    project_id: int,
    batch_id: str,
    items: list[dict],
) -> None:
    """写标素材图片分类与结构化子任务。"""
    from app.core.tasks.writing_image_llm_task import run_image_classify_batch

    worker_id = ctx.get("bidmind_worker_id")
    with logger.contextualize(
        task_type="writing_image_classify",
        project_id=project_id,
        source="worker",
        worker_id=worker_id,
        incident_id=_new_incident_id(),
    ):
        if await _should_abort_before_run(
            ctx,
            project_id=project_id,
            task_type="writing_image_classify",
        ):
            return
        logger.info(
            "[Worker] writing_image_classify_job: project={}, batch={}, items={}",
            project_id,
            batch_id,
            len(items),
        )
        await _run_with_resource_permit(
            ctx,
            "writing_image_classify_job",
            lambda: _run_with_prompt_snapshot(
                "writing",
                lambda: run_image_classify_batch(ctx, project_id, batch_id, items),
            ),
            project_id=project_id,
            task_type="writing_image_classify",
        )


async def writing_match_job(
    ctx: dict,
    project_id: int,
    explicit_retry: bool = False,
    reset_reviewed_bindings: bool = False,
) -> None:
    """写标阶段 3: 素材三元匹配"""
    from app.core.tasks.writing_match_task import run_writing_match_task

    worker_id = ctx.get("bidmind_worker_id")
    with logger.contextualize(
        task_type="writing_match", project_id=project_id, source="worker",
        worker_id=worker_id, incident_id=_new_incident_id(),
    ):
        if await _should_abort_before_run(ctx, project_id=project_id, task_type="writing_match"):
            return
        logger.info(f"[Worker] writing_match_job: project={project_id}")
        await _run_with_resource_permit(
            ctx,
            "writing_match_job",
            lambda: _run_with_prompt_snapshot(
                "writing",
                lambda: run_writing_match_task(
                    project_id,
                    explicit_retry,
                    reset_reviewed_bindings=reset_reviewed_bindings,
                ),
            ),
            project_id=project_id,
            task_type="writing_match",
        )


async def writing_compose_job(ctx: dict, project_id: int, explicit_retry: bool = False) -> None:
    """写标阶段 4: 全文生成"""
    from app.core.tasks.writing_compose_task import run_writing_compose_task

    worker_id = ctx.get("bidmind_worker_id")
    with logger.contextualize(
        task_type="writing_compose", project_id=project_id, source="worker",
        worker_id=worker_id, incident_id=_new_incident_id(),
    ):
        if await _should_abort_before_run(ctx, project_id=project_id, task_type="writing_compose"):
            return
        logger.info(f"[Worker] writing_compose_job: project={project_id}")
        await _run_with_resource_permit(
            ctx,
            "writing_compose_job",
            lambda: _run_with_prompt_snapshot(
                "writing",
                lambda: run_writing_compose_task(project_id, explicit_retry),
            ),
            project_id=project_id,
            task_type="writing_compose",
        )


async def writing_compose_single_job(
    ctx: dict,
    project_id: int,
    node_id: int,
    explicit_retry: bool = False,
) -> None:
    """写标阶段 4b: 单章节重新生成"""
    from app.core.tasks.writing_compose_single_task import run_writing_compose_single_task

    worker_id = ctx.get("bidmind_worker_id")
    with logger.contextualize(
        task_type="writing_compose_single",
        project_id=project_id,
        source="worker",
        worker_id=worker_id,
        incident_id=_new_incident_id(),
    ):
        if await _should_abort_before_run(
            ctx, project_id=project_id, task_type="writing_compose_single"
        ):
            return
        logger.info(
            f"[Worker] writing_compose_single_job: project={project_id}, node={node_id}"
        )
        await _run_with_resource_permit(
            ctx,
            "writing_compose_single_job",
            lambda: _run_with_prompt_snapshot(
                "writing",
                lambda: run_writing_compose_single_task(project_id, node_id, explicit_retry),
            ),
            project_id=project_id,
            task_type="writing_compose_single",
            node_id=node_id,
        )


async def writing_clause_vectorize_job(ctx: dict, project_id: int) -> None:
    """写标附带: 已确认约束的向量化 (无 LLM 业务, 不绑定快照)"""
    from app.core.tasks.writing_clause_vectorize_task import run_writing_clause_vectorize_task

    worker_id = ctx.get("bidmind_worker_id")
    with logger.contextualize(
        task_type="writing_clause_vectorize",
        project_id=project_id,
        source="worker",
        worker_id=worker_id,
        incident_id=_new_incident_id(),
    ):
        if await _should_abort_before_run(
            ctx, project_id=project_id, task_type="writing_clause_vectorize"
        ):
            return
        logger.info(f"[Worker] writing_clause_vectorize_job: project={project_id}")
        await _run_with_resource_permit(
            ctx,
            "writing_clause_vectorize_job",
            lambda: run_writing_clause_vectorize_task(project_id),
            project_id=project_id,
            task_type="writing_clause_vectorize",
        )


# ── 生命周期 ─────────────────────────────────────────────────────

async def _startup_with_functions(ctx: dict, function_names: list[str]) -> None:
    """Worker 启动时初始化 (数据库连接 + AI设置 + PromptRegistry + alive 心跳)"""
    from app.core.database import init_db

    logger.info("[Worker] 初始化数据库...")
    await init_db()

    # ARQ Worker 不经过 FastAPI lifespan, 需要主动加载 AI 设置并构建本进程 Dispatcher。
    try:
        from app.core.ai_settings import init_ai_settings
        init_ai_settings()
    except Exception as e:
        logger.warning(f"[Worker] AI设置初始化失败, 将按需使用默认配置: {e}")

    # PromptRegistry 预热: 与 app.main lifespan 保持同一语义。
    # ARQ Worker 不经过 FastAPI lifespan, 若此处缺失, worker 进程内的 _cache 将一直为空,
    # 导致审查/串标/写标等任务的 get_prompt() 持续降级到代码默认值并刷屏 WARNING。
    # 非阻塞: 失败不影响 worker 启动, 业务层 get_prompt() 仍有代码默认值 fallback。
    try:
        from app.core.ai.prompt_registry import get_registry
        from app.core.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            stats = await get_registry().registry_sync(db)
            logger.info(f"[Worker] PromptRegistry 同步完成: {stats}")
    except Exception as e:
        logger.warning(f"[Worker] PromptRegistry 同步失败, 不影响 worker 启动: {e}")

    # CPU encode 并发 AIMD 控制器: 挂载 metric handler 到 encode_gate, 决定 cpu_encode_semaphore 容量
    try:
        from app.core.vectorizer.cpu_encode_concurrency import (
            get_cpu_encode_concurrency_controller,
        )
        get_cpu_encode_concurrency_controller()
    except Exception as e:
        logger.warning(f"[Worker] CPU encode 并发控制器挂载失败 (退化为静态容量): {e}")

    from app.core.runtime.model_preload import warmup_production_models
    worker_role = (
        "process"
        if any(name in PROCESS_WORKER_FUNCTION_NAMES for name in function_names)
        else "other"
    )
    await warmup_production_models(worker_role=worker_role)

    worker_id = generate_worker_id()
    ctx["bidmind_worker_id"] = worker_id
    ctx["bidmind_worker_functions"] = function_names
    ctx["bidmind_worker_state"] = "idle"
    ctx["bidmind_current_function"] = None
    ctx["bidmind_current_resource"] = None
    ctx["bidmind_heartbeat_stop"] = asyncio.Event()

    redis = ctx.get("redis")

    # 启动 PromptRegistry 跨进程失效订阅: API 发布 prompt 后本进程缓存可秒级刷新,
    # 避免 worker 仍跑旧 prompt 的问题; 非阻塞, Redis 不可用时仅 WARNING。
    try:
        from app.core.ai.prompt_registry import get_registry
        await get_registry().start_invalidate_subscriber(redis)
    except Exception as e:
        logger.warning(
            f"[Worker] PromptRegistry invalidate subscriber 启动失败 (不影响业务): {e}"
        )

    # 启动 AI 设置跨进程失效订阅: API 设置页调整模型池/优先级后刷新 worker Dispatcher。
    try:
        from app.core.ai_settings import start_runtime_invalidate_subscriber
        await start_runtime_invalidate_subscriber(redis)
    except Exception as e:
        logger.warning(
            f"[Worker] AISettings runtime invalidate subscriber 启动失败 (不影响业务): {e}"
        )

    ok = await record_worker_heartbeat_async(redis, worker_id, function_names)
    if not ok:
        logger.warning(
            f"[Worker] alive 心跳首次写入失败, API 侧可能暂时不放行 ARQ: worker_id={worker_id}"
        )

    ctx["bidmind_heartbeat_task"] = asyncio.create_task(
        heartbeat_refresh_loop(
            redis,
            worker_id,
            function_names,
            ctx["bidmind_heartbeat_stop"],
            extra_provider=lambda: _worker_heartbeat_extra(ctx),
        )
    )
    logger.info(
        f"[Worker] 启动完成: worker_id={worker_id}, functions={function_names}"
    )


async def startup(ctx: dict) -> None:
    """默认 worker 启动初始化。"""

    await _startup_with_functions(ctx, WORKER_FUNCTION_NAMES)


async def startup_process(ctx: dict) -> None:
    """资源型 process worker 启动初始化。"""

    await _startup_with_functions(ctx, PROCESS_WORKER_FUNCTION_NAMES)


async def startup_llm(ctx: dict) -> None:
    """资源型 LLM worker 启动初始化。"""

    await _startup_with_functions(ctx, LLM_WORKER_FUNCTION_NAMES)


async def startup_report(ctx: dict) -> None:
    """资源型 report worker 启动初始化。"""

    await _startup_with_functions(ctx, REPORT_WORKER_FUNCTION_NAMES)


async def startup_maintenance(ctx: dict) -> None:
    """资源型 maintenance worker 启动初始化。"""

    await _startup_with_functions(ctx, MAINTENANCE_WORKER_FUNCTION_NAMES)


async def shutdown(ctx: dict) -> None:
    """Worker 关闭时清理 alive 心跳与数据库连接"""
    stop_event = ctx.get("bidmind_heartbeat_stop")
    task = ctx.get("bidmind_heartbeat_task")
    worker_id = ctx.get("bidmind_worker_id")
    redis = ctx.get("redis")
    ctx["bidmind_worker_state"] = "draining"

    if worker_id:
        await record_worker_heartbeat_async(
            redis,
            worker_id,
            ctx.get("bidmind_worker_functions") or [],
            extra={
                "state": "draining",
                "current_function": ctx.get("bidmind_current_function"),
                "current_resource": ctx.get("bidmind_current_resource"),
            },
        )

    if stop_event is not None:
        stop_event.set()
    if task is not None:
        try:
            await asyncio.wait_for(task, timeout=3.0)
        except asyncio.TimeoutError:
            task.cancel()
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[Worker] 心跳任务退出异常(忽略): {exc}")

    # 优雅关闭 encode 子系统: 拒绝新提交并等 in-flight 完成
    # 这一步必须在 close_db 之前完成, 否则 in-flight 业务日志会出现在
    # [Worker] 已关闭 之后 (历史现象). 超时后会强制 cancel 排队 future,
    # 但运行中的 thread 由 Python atexit 自然等 (Python 标准限制)
    try:
        from app.core.vectorizer.encode_gate import shutdown_encode_workloads

        grace_sec = float(getattr(settings, "ENCODE_SHUTDOWN_GRACE_SEC", 15.0))
        encode_stats = await shutdown_encode_workloads(timeout=grace_sec)
        if encode_stats.get("timeout"):
            logger.warning(
                f"[Worker] encode in-flight 未在 {grace_sec:.0f}s 内完成: "
                f"active_at_exit={encode_stats.get('active_count_at_exit')}, "
                f"已强制 cancel 排队 future"
            )
        else:
            logger.info(
                f"[Worker] encode 已优雅退出: "
                f"elapsed={encode_stats.get('elapsed_sec'):.1f}s, "
                f"active_at_start={encode_stats.get('active_count_at_start')}"
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[Worker] encode shutdown 异常 (忽略, 继续退出): {exc}")

    # 停止 PromptRegistry invalidate 订阅
    try:
        from app.core.ai.prompt_registry import get_registry
        await get_registry().stop_invalidate_subscriber()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[Worker] PromptRegistry subscriber 停止异常 (忽略): {e}")

    # 停止 AI 设置 invalidate 订阅
    try:
        from app.core.ai_settings import stop_runtime_invalidate_subscriber
        await stop_runtime_invalidate_subscriber()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[Worker] AISettings subscriber 停止异常 (忽略): {e}")

    if worker_id:
        await clear_worker_heartbeat_async(redis, worker_id)

    from app.core.database import close_db
    await close_db()
    logger.info(f"[Worker] 已关闭: worker_id={worker_id}")


# 用 ARQ after_job_end 钩子归还项目份额, 而不是在 _run_with_resource_permit 里包
# try/finally: 后者会给这个已超复杂度阈值的文件再加一层嵌套。钩子在 job 成功、失败、
# 被公平机制重排三种收场下都会触发 (已实测), 因此份额不会漏还。
from app.core.tasks.document_qa_job import document_qa_job  # noqa: E402
from app.core.tasks.document_qa_verify_task import document_qa_verify_job  # noqa: E402
from app.core.tasks.express_advance_task import express_advance_job  # noqa: E402
from app.core.tasks.fairness.project_admission import (  # noqa: E402
    release_project_share_after_job,
)


class WorkerSettings:
    """ARQ Worker 配置"""

    # process_document_job 使用确定性 _job_id (process_doc_{doc_id}) 做幂等入队,
    # ARQ 会在任务完成后按 keep_result 时长保留结果, 期间相同 _job_id 无法再次入队。
    # 默认 3600s 过长, 会阻塞合法的重处理 (如重新上传); 降到 60s 足以挡住瞬时重复
    # 入队 (API 并发调用 / 恢复路径误判 / 前端轮询抖动), 到期后自然解锁。
    functions = [
        arq_func(
            process_document_job,
            keep_result=60,
            timeout=int(getattr(settings, "WORKER_PROCESS_JOB_TIMEOUT", 7200)),
        ),
        arq_func(
            document_qa_job,
            keep_result=60,
            timeout=int(getattr(settings, "WORKER_PROCESS_JOB_TIMEOUT", 7200)),
        ),
        knowledge_base_process_job,
        arq_func(
            extract_requirements_job,
            timeout=int(getattr(settings, "WORKER_EXTRACT_JOB_TIMEOUT", 7200)),
        ),
        arq_func(
            review_bid_extract_job,
            timeout=int(getattr(settings, "WORKER_LONG_LLM_JOB_TIMEOUT", 7200)),
        ),
        arq_func(
            review_project_job,
            timeout=int(getattr(settings, "WORKER_LONG_LLM_JOB_TIMEOUT", 7200)),
        ),
        arq_func(
            collusion_analyze_job,
            timeout=int(getattr(settings, "WORKER_LONG_LLM_JOB_TIMEOUT", 7200)),
        ),
        document_image_vl_job,
        writing_image_vl_job,
        writing_image_classify_job,
        arq_func(
            writing_extract_job,
            timeout=int(getattr(settings, "WORKER_LONG_LLM_JOB_TIMEOUT", 7200)),
        ),
        arq_func(
            writing_outline_job,
            timeout=int(getattr(settings, "WORKER_LONG_LLM_JOB_TIMEOUT", 7200)),
        ),
        writing_material_process_job,
        arq_func(
            writing_match_job,
            timeout=int(getattr(settings, "WORKER_LONG_LLM_JOB_TIMEOUT", 7200)),
        ),
        arq_func(
            writing_compose_job,
            timeout=int(getattr(settings, "WORKER_LONG_LLM_JOB_TIMEOUT", 7200)),
        ),
        arq_func(
            writing_compose_single_job,
            timeout=int(getattr(settings, "WORKER_LONG_LLM_JOB_TIMEOUT", 7200)),
        ),
        express_advance_job,
        document_qa_verify_job,
        writing_clause_vectorize_job,
        flush_metrics_job,
        regression_runner_job,
        liveness_sweep_job,
        prune_observability_logs_job,
        weekly_llm_advice_job,
    ]
    cron_jobs = [
        # Phase 3: 每 10 分钟把 Redis 热层指标 flush 到 metric_snapshots 温层
        cron(flush_metrics_job, minute={0, 10, 20, 30, 40, 50}, run_at_startup=False),
    ]
    on_startup = startup
    on_shutdown = shutdown
    after_job_end = release_project_share_after_job
    redis_settings = REDIS_SETTINGS
    max_jobs = int(getattr(settings, "WORKER_DEFAULT_MAX_JOBS", 5))
    job_timeout = 1800
    health_check_interval = 30
    # 收到信号后等 asyncio.Task 完成的窗口, 之后 ARQ 才 task.cancel
    job_completion_wait = int(getattr(settings, "WORKER_JOB_COMPLETION_WAIT_SEC", 30))


class ProcessWorkerSettings:
    """资源型文档处理 Worker 配置。

    仅在 WORKER_RESOURCE_QUEUES_ENABLED=true 且入队端按资源队列投递时单独启动。
    """

    functions = [
        arq_func(
            process_document_job,
            keep_result=60,
            timeout=int(getattr(settings, "WORKER_PROCESS_JOB_TIMEOUT", 7200)),
        ),
        arq_func(
            document_qa_job,
            keep_result=60,
            timeout=int(getattr(settings, "WORKER_PROCESS_JOB_TIMEOUT", 7200)),
        ),
        knowledge_base_process_job,
        writing_material_process_job,
        writing_clause_vectorize_job,
    ]
    on_startup = startup_process
    on_shutdown = shutdown
    after_job_end = release_project_share_after_job
    redis_settings = REDIS_SETTINGS
    queue_name = str(getattr(settings, "WORKER_PROCESS_QUEUE_NAME", "arq:queue:process"))
    max_jobs = int(getattr(settings, "WORKER_PROCESS_MAX_JOBS", 5))
    job_timeout = int(getattr(settings, "WORKER_PROCESS_JOB_TIMEOUT", 7200))
    health_check_interval = 30
    job_completion_wait = int(getattr(settings, "WORKER_JOB_COMPLETION_WAIT_SEC", 30))


class LlmWorkerSettings:
    """资源型 LLM Worker 配置。"""

    functions = [
        arq_func(
            extract_requirements_job,
            timeout=int(getattr(settings, "WORKER_EXTRACT_JOB_TIMEOUT", 7200)),
        ),
        arq_func(
            review_bid_extract_job,
            timeout=int(getattr(settings, "WORKER_LONG_LLM_JOB_TIMEOUT", 7200)),
        ),
        arq_func(
            review_project_job,
            timeout=int(getattr(settings, "WORKER_LONG_LLM_JOB_TIMEOUT", 7200)),
        ),
        arq_func(
            collusion_analyze_job,
            timeout=int(getattr(settings, "WORKER_LONG_LLM_JOB_TIMEOUT", 7200)),
        ),
        document_image_vl_job,
        writing_image_vl_job,
        writing_image_classify_job,
        arq_func(
            writing_extract_job,
            timeout=int(getattr(settings, "WORKER_LONG_LLM_JOB_TIMEOUT", 7200)),
        ),
        arq_func(
            writing_outline_job,
            timeout=int(getattr(settings, "WORKER_LONG_LLM_JOB_TIMEOUT", 7200)),
        ),
        arq_func(
            writing_match_job,
            timeout=int(getattr(settings, "WORKER_LONG_LLM_JOB_TIMEOUT", 7200)),
        ),
        arq_func(
            writing_compose_job,
            timeout=int(getattr(settings, "WORKER_LONG_LLM_JOB_TIMEOUT", 7200)),
        ),
        arq_func(
            writing_compose_single_job,
            timeout=int(getattr(settings, "WORKER_LONG_LLM_JOB_TIMEOUT", 7200)),
        ),
        express_advance_job,
        document_qa_verify_job,
    ]
    on_startup = startup_llm
    on_shutdown = shutdown
    after_job_end = release_project_share_after_job
    redis_settings = REDIS_SETTINGS
    queue_name = str(getattr(settings, "WORKER_LLM_QUEUE_NAME", "arq:queue:llm"))
    max_jobs = int(getattr(settings, "WORKER_LLM_MAX_JOBS", 5))
    job_timeout = int(getattr(settings, "WORKER_LLM_JOB_TIMEOUT", 1800))
    health_check_interval = 30
    job_completion_wait = int(getattr(settings, "WORKER_JOB_COMPLETION_WAIT_SEC", 30))


class ReportWorkerSettings:
    """资源型报告 Worker 预留配置。

    当前报告生成仍内嵌在业务 task 中, 因此该 worker 暂不注册函数。
    后续拆出 report job 后只需补充函数列表与 resource_policy 映射。
    """

    functions: list = []
    on_startup = startup_report
    on_shutdown = shutdown
    after_job_end = release_project_share_after_job
    redis_settings = REDIS_SETTINGS
    queue_name = str(getattr(settings, "WORKER_REPORT_QUEUE_NAME", "arq:queue:report"))
    max_jobs = int(getattr(settings, "WORKER_REPORT_MAX_JOBS", 3))
    job_timeout = 1800
    health_check_interval = 30
    job_completion_wait = int(getattr(settings, "WORKER_JOB_COMPLETION_WAIT_SEC", 30))


class MaintenanceWorkerSettings:
    """资源型维护任务 Worker 配置。"""

    functions = [
        flush_metrics_job,
        regression_runner_job,
        liveness_sweep_job,
        prune_observability_logs_job,
        weekly_llm_advice_job,
    ]
    cron_jobs = [
        cron(flush_metrics_job, minute={0, 10, 20, 30, 40, 50}, run_at_startup=False),
        # 主动失活巡检: 每分钟触发, 复用既有惰性自愈链, 避免任务死亡后永久"处理中"
        cron(liveness_sweep_job, minute=set(range(60)), run_at_startup=False),
        # 可观测日志差异化 TTL 清理: 每日凌晨 3 点触发
        cron(prune_observability_logs_job, hour={3}, minute={0}, run_at_startup=False),
        # 周度 LLM 调度建议: 周一 00:00 北京时间（= 周日 24:00）；环境 TZ=Asia/Shanghai
        cron(
            weekly_llm_advice_job,
            weekday={0},
            hour={0},
            minute={0},
            run_at_startup=False,
        ),
    ]
    on_startup = startup_maintenance
    on_shutdown = shutdown
    after_job_end = release_project_share_after_job
    redis_settings = REDIS_SETTINGS
    queue_name = str(
        getattr(settings, "WORKER_MAINTENANCE_QUEUE_NAME", "arq:queue:maintenance")
    )
    max_jobs = int(getattr(settings, "WORKER_MAINTENANCE_MAX_JOBS", 1))
    job_timeout = 1800
    health_check_interval = 30
    job_completion_wait = int(getattr(settings, "WORKER_JOB_COMPLETION_WAIT_SEC", 30))
