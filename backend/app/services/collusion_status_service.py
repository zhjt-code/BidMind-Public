"""串标审查 Status / 进度计算与 SQL 聚合服务（R2-6 拆分自 collusion router）

承载从 router endpoint 迁出的状态机推断、心跳超时检测、自动恢复链路、
SQL 聚合，以及对外提供高阶编排方法供 router thin wrapper 调用。

核心职责：
- 项目权限校验 + 终态二次确认（防御）
- 心跳进度推断（heartbeat_stage_pct / fractional_progress / group_fractional_progress）
- 项目级失败恢复 payload 构造
- 高阶方法 compute_collusion_status / build_collusion_results
 （Step 6 / Step 7 引入，含从 endpoint 迁出的 SQL select + 6 步进度计算 + 心跳自动恢复编排）

设计原则：
- **依赖方向**：本模块可依赖 collusion_view_service + collusion_summary_service；
 视图与摘要模块不得反向 import 本模块（防循环依赖）；
- **事务边界**：高阶方法接受 db / project 注入，由调用方持有事务，service 内只读 + commit 同 endpoint 行为。

参考：
- v2 §八 R2-6 卡片 §6.2 单向依赖图
- v2 §八 R2-6 卡片 §6.3 service 公共接口设计
- （恢复重投前终态二次确认）
"""

from __future__ import annotations

import time

from fastapi import HTTPException, status
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.collusion.dimension_keys import COLLUSION_DIM_KEYS
from app.core.config import settings
from app.core.exceptions import ForbiddenError
from app.core.permissions import check_project_permission
from app.core.tasks import (
    HEARTBEAT_TIMEOUT,
    MAX_RECOVERY,
    cancel_task,
    dispatch_background_job,
    get_collusion_task_name,
    get_recovery_count,
    get_task_heartbeat,
    increment_recovery_count,
    inspect_task_liveness,
    is_task_active,
    read_collusion_stall_snapshot,
    reset_recovery_count,
    run_collusion_task,
    write_collusion_stall_snapshot,
)
from app.models.collusion_extraction import CollusionExtraction
from app.models.collusion_group import CollusionGroup
from app.models.collusion_result import CollusionResult
from app.models.document import Document, DocumentType
from app.models.project import Project, ProjectStatus
from app.models.user import User
from app.schemas.collusion import (
    AttentionItem,
    CollusionGroupResult,
    CollusionPairResult,
    CollusionResults,
    CollusionStatus,
    CollusionStepStatus,
    CoverageStats,
    EvidenceChunk,
    FindingItem,
    GlobalFindingItem,
    GroupRoleItem,
    build_pair_id,
)
from app.services.collusion_summary_service import (
    infer_decision_state,
)
from app.services.collusion_summary_service import (
    load_or_build_project_summary as summary_load_or_build_project_summary,
)
from app.services.collusion_view_service import (
    build_finding_items as view_build_finding_items,
)
from app.services.collusion_view_service import (
    sanitize_collusion_results as view_sanitize_collusion_results,
)
from app.services.collusion_view_service import (
    step_status,
)
from app.services.flow_retry_service import (
    COLLUSION_RETRY_SPECS,
    build_collusion_retry_state,
    build_retry_payload,
    mark_collusion_task_run_by_task_name,
)


def get_collusion_liveness_timeout() -> int:
    """串标任务心跳失活阈值，覆盖云端长 LLM 调用窗口。"""
    return max(
        int(getattr(settings, "ARQ_JOB_HEARTBEAT_TIMEOUT", HEARTBEAT_TIMEOUT)),
        int(getattr(settings, "COLLUSION_COMPARE_TIMEOUT_CLOUD", 300)) + 120,
        int(getattr(settings, "COLLUSION_SYNTHESIS_TIMEOUT_CLOUD", 180)) + 120,
        int(HEARTBEAT_TIMEOUT),
    )


# ---------------------------------------------------------------------------
# 项目权限校验 + 终态二次确认（防御）
# ---------------------------------------------------------------------------


async def get_project_or_404(
    project_id: int,
    db: AsyncSession,
    current_user: User | None = None,
    *,
    require_owner: bool = False,
) -> Project:
    """加载项目，缺失时抛 404；可选权限校验。"""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目不存在: id={project_id}",
        )
    if current_user is not None:
        try:
            check_project_permission(project, current_user, require_owner=require_owner)
        except ForbiddenError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=exc.detail,
            ) from exc
    return project


async def recheck_project_terminal(
    db: AsyncSession,
    project_id: int,
) -> tuple[ProjectStatus, str | None] | None:
    """恢复写入前重读项目终态, 避免 worker 收尾与 status 轮询竞态重投。"""
    result = await db.execute(
        select(Project.status, Project.error_message).where(Project.id == project_id)
    )
    row = result.one_or_none()
    if row is None:
        return None
    latest_status, error_message = row
    if latest_status in (ProjectStatus.COMPLETED, ProjectStatus.FAILED):
        return latest_status, error_message
    return None


# ---------------------------------------------------------------------------
# 心跳进度推断
# ---------------------------------------------------------------------------


def heartbeat_stage_pct(heartbeat: dict | None) -> int:
    """从心跳中读取 stage_progress_pct，钳制到 [0, 100]。"""
    if not heartbeat:
        return 0
    try:
        return max(0, min(int(heartbeat.get("stage_progress_pct", 0)), 100))
    except (TypeError, ValueError):
        return 0


def heartbeat_message(heartbeat: dict | None, fallback: str | None = None) -> str | None:
    """从心跳中读取 message 或 progress，空时返回 fallback。"""
    if not heartbeat:
        return fallback
    message = heartbeat.get("message") or heartbeat.get("progress")
    if isinstance(message, str) and message.strip():
        return message
    return fallback


def fractional_progress_from_heartbeat(
    completed_count: int,
    total_count: int,
    heartbeat: dict | None,
) -> int:
    """单步细粒度进度：completed/total 基线与心跳 stage_progress_pct 中取大者。"""
    if total_count <= 0:
        return 0
    if completed_count >= total_count:
        return 100

    hb_pct = heartbeat_stage_pct(heartbeat)
    approx = min(completed_count + hb_pct / 100, total_count)
    return max(
        int(completed_count / total_count * 100),
        int(approx / total_count * 100),
    )


def group_fractional_progress_from_heartbeat(
    heartbeat: dict | None,
    fallback_progress: int,
) -> int:
    """群组研判进度：心跳含 current/total 时按 current 比例估算。"""
    if not heartbeat:
        return fallback_progress
    total = heartbeat.get("total")
    current = heartbeat.get("current")
    if not isinstance(total, int) or total <= 0:
        return max(fallback_progress, heartbeat_stage_pct(heartbeat))
    if not isinstance(current, int):
        current = 0
    approx = min(current + heartbeat_stage_pct(heartbeat) / 100, total)
    return max(fallback_progress, int(approx / total * 100))


# ---------------------------------------------------------------------------
# 6 步进度公式（纯函数，方便单测，避免脆弱 mock）
# ---------------------------------------------------------------------------


def compute_step_progresses(
    *,
    total_docs: int,
    extracted_count: int,
    total_pairs: int,
    compared_count: int,
    llm_compared_count: int,
    algorithm_only_count: int,
    skipped_count: int,
    group_count: int,
    project_status: ProjectStatus,
    synthesis_saved: bool,
) -> dict:
    """串标 6 步进度公式纯函数

 修复要点：
 - step3_pct 不再用 `max(..., 1)` 把"无 LLM 工作"误算为 0%
 - step4/step5 解耦严格链式依赖，引入 synthesis_done 这一可靠完成信号
 - step6 仍只看 project.status 终态

 Args:
 synthesis_saved: collusion_summary 字段是否已落库
 （analyzer._save_project_summary 写入 -> 视为 S4 已完成）

 Returns:
 {
 "step1_pct": int, ..., "step6_pct": int,
 "total_llm_pairs": int, # 业务上需要 LLM 处理的对数
 "project_terminal": bool,
 "synthesis_done": bool,
 }
 """
    project_terminal = project_status in (
        ProjectStatus.COMPLETED, ProjectStatus.FAILED,
    )
    synthesis_done = synthesis_saved or project_terminal

    step1_pct = int(extracted_count / total_docs * 100) if total_docs > 0 else 0
    step2_pct = 100 if step1_pct >= 100 else 0

    # step3: 重点文件对比
    # 当所有 pair 都被智能筛对/降级为非 LLM 路径（needs_llm <= 0），
    # 不能用 max(..., 1) 把分母强行抬到 1 导致 step3_pct 恒为 0；
    # 此时按"所有 pair 是否都已入库"算完成度。
    # 当 synthesis_done 时强制视为完成（后端流水线已经走过 S3，UI 不能再显示等待）。
    if total_pairs == 0:
        total_llm_pairs = 0
        step3_pct = 100
    else:
        needs_llm = total_pairs - skipped_count - algorithm_only_count
        if needs_llm <= 0:
            total_llm_pairs = 0
            step3_pct = (
                100 if compared_count >= total_pairs
                else int(compared_count / total_pairs * 100)
            )
        else:
            total_llm_pairs = needs_llm
            if synthesis_done:
                step3_pct = 100
            else:
                step3_pct = int(llm_compared_count / total_llm_pairs * 100)

    # step4: 嫌疑群组识别
    # 完成条件（任一即可）：识别到群组 / 综合研判已完成 / 所有 pair 处理完且 step3 到 100%
    step4_pct = 100 if (
        group_count > 0
        or synthesis_done
        or (step3_pct >= 100 and total_pairs > 0 and compared_count >= total_pairs)
    ) else 0

    # step5: 群组深度研判
    step5_pct = 100 if synthesis_done else (50 if step4_pct >= 100 else 0)

    # step6: 生成审查报告
    step6_pct = 100 if project_status == ProjectStatus.COMPLETED else 0

    return {
        "step1_pct": step1_pct,
        "step2_pct": step2_pct,
        "step3_pct": step3_pct,
        "step4_pct": step4_pct,
        "step5_pct": step5_pct,
        "step6_pct": step6_pct,
        "total_llm_pairs": total_llm_pairs,
        "project_terminal": project_terminal,
        "synthesis_done": synthesis_done,
    }


# ---------------------------------------------------------------------------
# 项目级失败恢复 payload 构造
# ---------------------------------------------------------------------------


def collusion_retry_payload(project_id: int, error_message: str | None = None) -> dict:
    """项目级失败恢复入口的 payload 构造（与 reviews._review_retry_payload 对称）。"""
    return build_retry_payload(
        COLLUSION_RETRY_SPECS["collusion_analyze"],
        retryable=True,
        last_error=error_message,
    )


# ---------------------------------------------------------------------------
# 八维度读取工具（llm_analysis 结构化字段优先，回退 ORM 旧列）
# ---------------------------------------------------------------------------

_DIM_COL_MAP = {
    "d1_personnel": "d1_personnel_overlap",
    "d2_company": "d2_company_relation",
    "d3_text": "d3_text_similarity",
    "d4_price": "d4_price_pattern",
    "d5_qualification": "d5_qualification_overlap",
    "d6_format": "d6_format_similarity",
    "d7_abnormal": "d7_abnormal_consistency",
    "d8_metadata": "d8_metadata_similarity",
}


def _dim_score(r: CollusionResult, key: str) -> float | None:
    """维度得分：优先 llm_analysis 结构化字段，回退 ORM 旧列"""
    llm = r.llm_analysis or {}
    dim = llm.get(key)
    if isinstance(dim, dict):
        return dim.get("score")
    return getattr(r, _DIM_COL_MAP.get(key, ""), None)


def _dim_certainty(r: CollusionResult, key: str) -> float | None:
    """维度确定性：仅 llm_analysis 结构化字段有值，旧列无对应字段"""
    llm = r.llm_analysis or {}
    dim = llm.get(key)
    if isinstance(dim, dict):
        return dim.get("certainty")
    return None


# ---------------------------------------------------------------------------
# 高阶方法：从 status endpoint 迁出的完整编排（SQL 聚合 + 6 步进度 + 心跳恢复）
# ---------------------------------------------------------------------------


async def compute_collusion_status(
    project_id: int,
    current_user: User,
    db: AsyncSession,
) -> CollusionStatus:
    """获取串标分析进度（含 SQL 聚合 + 心跳超时检测 + 进度停滞检测 + 自动恢复链路）

 进度来源:
 - 步骤1: collusion_extractions 表的记录数 / 文档总数
 - 步骤2: collusion_results 表的记录数 / C(n_bid,2)
 - 步骤3: 步骤2完成后算作完成（综合研判是单次LLM）
 - 步骤4: project.status == COMPLETED

 关键防御链路（不可移除）:
 - recheck_project_terminal: 恢复重投前重读项目终态，避免与 worker 收尾竞态
 - increment_recovery_count + MAX_RECOVERY: 上限保护，防止无限重投
 - HEARTBEAT_TIMEOUT: 心跳超时判定
 - COLLUSION_STALL_TIMEOUT: 进度停滞判定
 """
    project = await get_project_or_404(project_id, db, current_user)

    docs_result = await db.execute(
        select(Document).where(Document.project_id == project_id)
    )
    all_docs = docs_result.scalars().all()
    bid_docs = [d for d in all_docs if d.doc_type == DocumentType.BID]
    has_tender = any(d.doc_type == DocumentType.TENDER for d in all_docs)

    total_docs = len(all_docs)
    total_bid_docs = len(bid_docs)
    total_pairs = total_bid_docs * (total_bid_docs - 1) // 2 if total_bid_docs >= 2 else 0

    extracted_count_result = await db.execute(
        select(func.count()).where(CollusionExtraction.project_id == project_id)
    )
    extracted_count = extracted_count_result.scalar() or 0
    extracted_count = min(extracted_count, total_docs)
    failed_extract_result = await db.execute(
        select(func.count()).where(
            CollusionExtraction.project_id == project_id,
            CollusionExtraction.extraction_status == "failed",
        )
    )
    failed_extract_count = failed_extract_result.scalar() or 0

    compared_count_result = await db.execute(
        select(func.count()).where(CollusionResult.project_id == project_id)
    )
    compared_count = compared_count_result.scalar() or 0
    compared_count = min(compared_count, total_pairs)

    group_count_result = await db.execute(
        select(func.count()).where(CollusionGroup.project_id == project_id)
    )
    group_count = group_count_result.scalar() or 0

    llm_compared_result = await db.execute(
        select(func.count()).where(
            CollusionResult.project_id == project_id,
            CollusionResult.analysis_mode.notin_(["skipped", "algorithm_only"]),
        )
    )
    llm_compared_count = llm_compared_result.scalar() or 0

    algorithm_only_result = await db.execute(
        select(func.count()).where(
            CollusionResult.project_id == project_id,
            CollusionResult.analysis_mode == "algorithm_only",
        )
    )
    algorithm_only_count = algorithm_only_result.scalar() or 0

    skipped_count = compared_count - llm_compared_count - algorithm_only_count
    failed_count_result = await db.execute(
        select(func.count()).where(
            CollusionResult.project_id == project_id,
            CollusionResult.analysis_mode == "failed",
        )
    )
    failed_count = failed_count_result.scalar() or 0

    # 6 步进度公式集中在 compute_step_progresses 纯函数中（修复 + 可单测）
    _pct = compute_step_progresses(
        total_docs=total_docs,
        extracted_count=extracted_count,
        total_pairs=total_pairs,
        compared_count=compared_count,
        llm_compared_count=llm_compared_count,
        algorithm_only_count=algorithm_only_count,
        skipped_count=skipped_count,
        group_count=group_count,
        project_status=project.status,
        synthesis_saved=bool(project.collusion_summary),
    )
    step1_pct = _pct["step1_pct"]
    step2_pct = _pct["step2_pct"]
    step3_pct = _pct["step3_pct"]
    step4_pct = _pct["step4_pct"]
    step5_pct = _pct["step5_pct"]
    step6_pct = _pct["step6_pct"]
    total_llm_pairs = _pct["total_llm_pairs"]

    is_completed = project.status == ProjectStatus.COMPLETED
    is_failed = project.status == ProjectStatus.FAILED

    if is_completed:
        overall_status = "completed"
        reset_recovery_count(get_collusion_task_name(project_id))
    elif is_failed:
        overall_status = "failed"
    elif project.status == ProjectStatus.REVIEWING:
        overall_status = "running"
    else:
        overall_status = "idle"

    status_message: str | None = None
    is_recovering = False
    current_recovery_count = 0

    if overall_status == "running":
        task_name = get_collusion_task_name(project_id)
        liveness_timeout = get_collusion_liveness_timeout()
        task_liveness = inspect_task_liveness(
            task_name,
            heartbeat_timeout=liveness_timeout,
            arq_function="collusion_analyze_job",
        )
        heartbeat = task_liveness.heartbeat
        current_recovery_count = get_recovery_count(task_name)

        stall_timeout = settings.COLLUSION_STALL_TIMEOUT
        should_restart = False

        in_enqueue_grace = task_liveness.state == "enqueue_grace"

        if task_liveness.state in {"stale", "missing"} and not in_enqueue_grace:
            should_restart = True
            status_message = (
                "检测到串标分析任务异常终止，正在尝试自动恢复"
                if task_liveness.state == "missing"
                else "检测到串标分析任务心跳超时，正在尝试自动恢复"
            )
            logger.warning(
                "[串标] 任务失活, 进入自动恢复链: project={}, task={}, state={}, reason={}, age={}",
                project_id,
                task_name,
                task_liveness.state,
                task_liveness.reason,
                task_liveness.age_seconds,
            )

        if overall_status == "running" and heartbeat and not in_enqueue_grace:
            elapsed = time.time() - heartbeat.get("last_heartbeat", 0)
            if elapsed > liveness_timeout:
                logger.warning(f"[串标] 任务心跳超时 project={project_id} ({elapsed:.0f}s)")
                should_restart = True
        elif overall_status == "running" and not heartbeat and not in_enqueue_grace and not is_task_active(task_name):
            should_restart = True

        if overall_status == "running" and not should_restart:
            snapshot = read_collusion_stall_snapshot(project_id)
            now = time.time()
            if snapshot and snapshot.get("compared_count") == compared_count:
                if now - float(snapshot.get("timestamp", now)) > stall_timeout:
                    logger.warning(f"[串标] 进度停滞 project={project_id}")
                    should_restart = True
            else:
                write_collusion_stall_snapshot(
                    project_id, timestamp=now, compared_count=compared_count,
                )

        if should_restart:
            terminal_snapshot = await recheck_project_terminal(db, project_id)
            if terminal_snapshot and terminal_snapshot[0] == ProjectStatus.COMPLETED:
                overall_status = "completed"
                is_completed = True
                reset_recovery_count(task_name)
                should_restart = False
            elif terminal_snapshot and terminal_snapshot[0] == ProjectStatus.FAILED:
                overall_status = "failed"
                is_failed = True
                status_message = terminal_snapshot[1] or "处理失败"
                should_restart = False
            elif current_recovery_count >= MAX_RECOVERY:
                logger.error(
                    f"[串标] 恢复次数已达上限({MAX_RECOVERY}), "
                    f"放弃恢复: project={project_id}"
                )
                project.status = ProjectStatus.FAILED
                project.error_message = (
                    f"任务恢复失败(已重试{MAX_RECOVERY}次)，"
                    f"请检查服务器环境后重新发起分析"
                )
                await db.commit()
                # 同根因: 恢复上限置败也是失败终态，必须幂等释放预占
                try:
                    from app.billing.services.billing_guard import BillingGuard
                    await BillingGuard.on_failed(db, project)
                    await db.commit()
                except Exception as billing_err:
                    logger.warning(
                        f"[串标] 恢复上限置败后计费释放异常(忽略): {billing_err}"
                    )
                try:
                    from app.models.task_run import TaskRunStatus

                    await mark_collusion_task_run_by_task_name(
                        db,
                        project_id=project_id,
                        task_name=task_name,
                        status=TaskRunStatus.failed.value,
                        stage=(heartbeat or {}).get("stage") or "failed",
                        last_error=project.error_message,
                    )
                except Exception as mark_err:
                    logger.warning(f"[串标] 回写显式重试恢复失败状态失败(忽略): {mark_err}")
                overall_status = "failed"
                is_failed = True
            else:
                # cancel_task 对 ARQ worker 进程内运行的 job 无效，旧 job
                # 在途时重投会导致同项目双跑。在途则本轮放弃，不消耗恢复配额。
                from app.core.tasks.lifecycle.liveness_evidence import (
                    is_arq_job_possibly_inflight,
                )

                if await is_arq_job_possibly_inflight(
                    heartbeat,
                    task_name=task_name,
                    arq_function="collusion_analyze_job",
                ):
                    logger.info(
                        f"[串标] 恢复前检测到 ARQ job 仍在途，跳过本轮重投: "
                        f"project={project_id}, task={task_name}"
                    )
                    is_recovering = True
                    status_message = "上一次分析任务仍在退出中，稍后自动重试"
                else:
                    # hold 可能已被 sweeper 超24h强制释放，自动恢复前必须
                    # 确保预占存在（有 HOLDING 则幂等复用），否则无持仓续跑完成后
                    # on_completed 幂等跳过、零结算。余额不足按 教训截断
                    # 自动推进并提示充值，不置败。
                    from app.billing.services.billing_guard import BillingGuard
                    from app.billing.services.wallet_service import (
                        InsufficientBalanceError,
                        format_insufficient_balance,
                    )
                    balance_blocked = False
                    try:
                        await BillingGuard.pre_check(db, project, project.owner_id)
                        await db.commit()
                    except InsufficientBalanceError as balance_err:
                        balance_blocked = True
                        is_recovering = True
                        status_message = (
                            "余额不足，自动恢复已暂停："
                            f"{format_insufficient_balance(balance_err)}，"
                            "请充值后在结果页重试"
                        )
                        logger.warning(
                            f"[串标] 自动恢复因余额不足暂停: "
                            f"project={project_id}, {balance_err}"
                        )

                    if not balance_blocked:
                        new_count = increment_recovery_count(task_name)
                        current_recovery_count = new_count
                        is_recovering = True
                        status_message = (
                            f"检测到处理异常，正在自动恢复"
                            f"（第{new_count}/{MAX_RECOVERY}次），已完成的部分将被跳过"
                        )
                        logger.info(
                            f"[串标] 自动恢复任务 project={project_id} "
                            f"({new_count}/{MAX_RECOVERY})"
                        )
                        cancel_task(task_name)
                        from app.core.ai import release_project_binding
                        await release_project_binding(project_id)
                        await dispatch_background_job(
                            task_name=task_name,
                            arq_function="collusion_analyze_job",
                            fallback_factory=lambda: run_collusion_task(project_id),
                            task_type="collusion",
                            project_id=project_id,
                            arq_args=(project_id,),
                        )
                        write_collusion_stall_snapshot(
                            project_id,
                            timestamp=time.time(),
                            compared_count=compared_count,
                        )

        if not is_recovering and current_recovery_count > 0:
            heartbeat = get_task_heartbeat(task_name)
            if heartbeat and heartbeat.get("stage") == "启动":
                is_recovering = True
                status_message = (
                    f"任务恢复中，正在跳过已完成的步骤..."
                    f"（第{current_recovery_count}次恢复）"
                )

    steps = [
        CollusionStepStatus(
            label="文档信息提取",
            status=step_status(step1_pct, overall_status),
            progress=step1_pct,
            detail=(
                f"{extracted_count}/{total_docs} 份"
                + (f"（失败 {failed_extract_count} 份）" if failed_extract_count > 0 else "")
            ) if total_docs > 0 else None,
        ),
        CollusionStepStatus(
            label="全局特征分析",
            status=step_status(step2_pct, overall_status),
            progress=step2_pct,
            detail="完成" if step2_pct == 100 else "等待中",
        ),
        CollusionStepStatus(
            label="重点文件对比",
            status=step_status(step3_pct, overall_status),
            progress=step3_pct,
            detail=(
                (f"{llm_compared_count}/{total_llm_pairs} 对"
                 + (f"（仅算法 {algorithm_only_count} 对）" if algorithm_only_count > 0 else ""))
                if total_llm_pairs > 0 else
                (f"仅算法判定 {algorithm_only_count} 对，未进入深度分析" if algorithm_only_count > 0 else None)
            ),
        ),
        CollusionStepStatus(
            label="嫌疑群组识别",
            status=step_status(step4_pct, overall_status),
            progress=step4_pct,
            detail=f"识别到{group_count}个群体" if group_count > 0 else ("等待中" if step3_pct < 100 else "处理中"),
        ),
        CollusionStepStatus(
            label="群组深度研判",
            status=step_status(step5_pct, overall_status),
            progress=step5_pct,
            detail="完成" if step5_pct == 100 else ("处理中" if step4_pct == 100 else "等待中"),
        ),
        CollusionStepStatus(
            label="生成审查报告",
            status="completed" if step6_pct == 100 else ("waiting" if step5_pct < 100 else "running"),
            progress=step6_pct,
            detail="就绪" if step6_pct == 100 else None,
        ),
    ]

    if overall_status == "running":
        task_name = get_collusion_task_name(project_id)
        heartbeat = get_task_heartbeat(task_name)
        if heartbeat:
            hb_stage = heartbeat.get("stage", "")
            hb_message = heartbeat_message(heartbeat)

            if hb_stage == "S2" and hb_message:
                steps[1].detail = hb_message
                if steps[1].status == "waiting":
                    steps[1].status = "running"
                steps[1].progress = max(steps[1].progress, min(95, max(heartbeat_stage_pct(heartbeat), 5)))
            elif hb_stage == "S3" and hb_message:
                steps[2].detail = hb_message
                if steps[2].status == "waiting":
                    steps[2].status = "running"
                steps[2].progress = max(
                    steps[2].progress,
                    fractional_progress_from_heartbeat(
                        llm_compared_count,
                        total_llm_pairs,
                        heartbeat,
                    ),
                )
            elif hb_stage == "S3.5" and hb_message:
                steps[3].detail = hb_message
                if steps[3].status == "waiting":
                    steps[3].status = "running"
                steps[3].progress = max(
                    steps[3].progress,
                    max(heartbeat_stage_pct(heartbeat), 5),
                )
            elif hb_stage == "S4" and hb_message:
                steps[4].detail = hb_message
                if steps[4].status == "waiting":
                    steps[4].status = "running"
                steps[4].progress = group_fractional_progress_from_heartbeat(
                    heartbeat,
                    steps[4].progress,
                )
            elif hb_message:
                for step in steps:
                    if step.status == "running":
                        step.detail = hb_message
                        break

    overall_pct = round(sum(s.progress for s in steps) / len(steps)) if steps else 0
    retry_payload = await build_collusion_retry_state(db, project)

    return CollusionStatus(
        project_id=project_id,
        overall_status=overall_status,
        overall_progress=overall_pct,
        steps=steps,
        total_documents=total_docs,
        total_bid_docs=total_bid_docs,
        total_pairs=total_pairs,
        extracted_count=extracted_count,
        compared_count=compared_count,
        total_llm_pairs=total_llm_pairs if total_pairs > 0 else 0,
        skipped_pairs=skipped_count,
        failed_pairs=failed_count,
        total_groups=group_count,
        global_features_done=step2_pct >= 100,
        cluster_done=step4_pct >= 100,
        has_tender_doc=has_tender,
        error_message=project.error_message if is_failed else None,
        message=status_message,
        is_recovering=is_recovering,
        recovery_count=current_recovery_count,
        retryable=bool(retry_payload.get("retryable")),
        failed_node=retry_payload.get("failed_node"),
        target_id=retry_payload.get("target_id"),
        failed_stage=retry_payload.get("failed_stage"),
        retry_action_label=retry_payload.get("retry_action_label") or "",
        preserved_outputs=list(retry_payload.get("preserved_outputs") or []),
        reset_scope=list(retry_payload.get("reset_scope") or []),
        last_attempt=int(retry_payload.get("last_attempt") or 0),
        retry_running=bool(retry_payload.get("retry_running")),
        last_error=retry_payload.get("last_error"),
    )


# ---------------------------------------------------------------------------
# 高阶方法：从 results endpoint 迁出的完整拼装（pair 矩阵 + groups + summary + sanitize）
# ---------------------------------------------------------------------------


async def build_collusion_results(
    project_id: int,
    current_user: User,
    db: AsyncSession,
) -> CollusionResults:
    """获取串标分析结果（拼装 CollusionResults 响应）

 流程:
 1. 加载所有投标文件 + Pair 对比结果 + 群组记录
 2. 构建 NxN 可疑度矩阵（certainty 加权）
 3. 构建 pair_results 列表（含 8 维度分数 + findings + decision state）
 4. 构建 groups 列表（角色 / 网络描述 / group findings）
 5. 构建 global_findings（从 group aggregated_evidence + collusion_summary.global_findings_extra）
 6. 调用 summary_load_or_build_project_summary 形成项目级 summary 单真源
 7. 调用 view_sanitize_collusion_results 出口闸门清洗 LLM 自由文本

 返回 CollusionResults，由 router 包裹为 ApiResponse 返回。
 """
    project = await get_project_or_404(project_id, db, current_user)

    docs_result = await db.execute(
        select(Document).where(Document.project_id == project_id)
    )
    all_docs = docs_result.scalars().all()
    bid_docs = sorted(
        [d for d in all_docs if d.doc_type == DocumentType.BID],
        key=lambda d: d.id,
    )
    has_tender = any(d.doc_type == DocumentType.TENDER for d in all_docs)

    if not bid_docs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目没有投标文件",
        )

    results_query = await db.execute(
        select(CollusionResult).where(
            CollusionResult.project_id == project_id
        ).order_by(CollusionResult.doc_a_id, CollusionResult.doc_b_id)
    )
    pair_results = results_query.scalars().all()

    doc_id_to_name = {d.id: d.filename for d in bid_docs}
    doc_ids = [d.id for d in bid_docs]
    n = len(doc_ids)
    total_pairs = n * (n - 1) // 2 if n >= 2 else 0

    # 构建可疑度矩阵（certainty 加权: max(score_i * certainty_i)）
    sim_matrix: list[list] = [[None] * n for _ in range(n)]

    result_by_pair: dict[tuple[int, int], CollusionResult] = {}
    for r in pair_results:
        result_by_pair[(r.doc_a_id, r.doc_b_id)] = r
        result_by_pair[(r.doc_b_id, r.doc_a_id)] = r

    # 八维度 key 引用真相源 dimension_keys.COLLUSION_DIM_KEYS（顺序影响矩阵渲染）
    for i, id_a in enumerate(doc_ids):
        for j, id_b in enumerate(doc_ids):
            if i == j:
                continue
            key = (min(id_a, id_b), max(id_a, id_b))
            r = result_by_pair.get(key) or result_by_pair.get((id_a, id_b))
            if r:
                llm = r.llm_analysis or {}
                effective = []
                for dk in COLLUSION_DIM_KEYS:
                    dim = llm.get(dk)
                    if isinstance(dim, dict):
                        s = dim.get("score", 0) or 0
                        c = dim.get("certainty", 0) or 0
                        effective.append(s * c)
                sim_matrix[i][j] = round(
                    max(effective) if effective else 0.0, 3
                )

    # 构建每对详细结果
    pair_result_list: list[CollusionPairResult] = []
    for r in pair_results:
        name_a = doc_id_to_name.get(r.doc_a_id, f"文件{r.doc_a_id}")
        name_b = doc_id_to_name.get(r.doc_b_id, f"文件{r.doc_b_id}")

        dim_certainties = {}
        for dk in _DIM_COL_MAP:
            c = _dim_certainty(r, dk)
            if c is not None:
                dim_certainties[dk] = c

        pair_id = build_pair_id(r.doc_a_id, r.doc_b_id)
        findings = view_build_finding_items(
            r, r.doc_a_id, r.doc_b_id, name_a, name_b, pair_id,
        )
        llm_analysis = r.llm_analysis or {}
        review_state = llm_analysis.get("review_state")
        if not review_state:
            if r.analysis_mode == "skipped":
                review_state = "unreviewed"
            elif r.analysis_mode == "failed":
                review_state = "failed"
            else:
                review_state = "completed"
        analysis_state = llm_analysis.get("analysis_state")
        if not analysis_state:
            if r.analysis_mode == "skipped":
                analysis_state = "skipped"
            elif r.analysis_mode == "failed":
                analysis_state = "failed"
            else:
                analysis_state = "completed"
        # 存值直通（不做小写规范化，保持 API 出口原样），缺失时用共享回填规则
        decision_state = llm_analysis.get("decision_state") or infer_decision_state(r)
        risk_level = llm_analysis.get("risk_level")
        if risk_level is None and r.overall_risk != "inconclusive":
            risk_level = r.overall_risk

        raw_attention = (r.llm_analysis or {}).get("_attention_items") or []
        attention_items = []
        for ai in raw_attention:
            if isinstance(ai, dict):
                try:
                    attention_items.append(AttentionItem(**ai))
                except Exception:
                    pass

        # 重合片段（向量预筛选的高相似 chunk 对，含疑似通用模板标注）
        evidence_chunks = []
        for ec in (r.evidence_chunks or []):
            if isinstance(ec, dict):
                try:
                    evidence_chunks.append(EvidenceChunk(**ec))
                except Exception:
                    pass

        pair_result_list.append(
            CollusionPairResult(
                pair_id=pair_id,
                doc_a_id=r.doc_a_id,
                doc_b_id=r.doc_b_id,
                doc_a_name=name_a,
                doc_b_name=name_b,
                overall_risk=r.overall_risk,
                similarity_score=r.similarity_score,
                d1_personnel_overlap=_dim_score(r, "d1_personnel"),
                d2_company_relation=_dim_score(r, "d2_company"),
                d3_text_similarity=_dim_score(r, "d3_text"),
                d4_price_pattern=_dim_score(r, "d4_price"),
                d5_qualification_overlap=_dim_score(r, "d5_qualification"),
                d6_format_similarity=_dim_score(r, "d6_format"),
                d7_abnormal_consistency=_dim_score(r, "d7_abnormal"),
                d8_metadata_similarity=_dim_score(r, "d8_metadata"),
                findings=findings,
                evidence_chunks=evidence_chunks,
                analysis_mode=r.analysis_mode or "algorithm_only",
                review_state=review_state,
                analysis_state=analysis_state,
                decision_state=decision_state,
                risk_level=risk_level,
                quality_flags=(r.llm_analysis or {}).get("quality_flags") or [],
                consistency_flags=(r.llm_analysis or {}).get("consistency_flags") or [],
                risk_explanation=(r.llm_analysis or {}).get("risk_explanation"),
                attention_items=attention_items,
                dimension_certainties=dim_certainties if dim_certainties else None,
            )
        )

    # 查询嫌疑群组
    groups_query = await db.execute(
        select(CollusionGroup).where(
            CollusionGroup.project_id == project_id
        ).order_by(CollusionGroup.group_index)
    )
    group_records = groups_query.scalars().all()

    group_result_list: list[CollusionGroupResult] = []
    for g in group_records:
        roles = []
        for r in (g.role_analysis or []):
            if isinstance(r, dict):
                roles.append(GroupRoleItem(
                    doc_id=r.get("doc_id", 0),
                    company_name=r.get("company_name", ""),
                    role=r.get("role", "角色不明"),
                    role_evidence=r.get("role_evidence", ""),
                ))
        group_findings: list[FindingItem] = []
        from app.core.collusion.analyzer_parts.dimension_signals import (
            is_hollow_dimension_signal,
        )
        for gf in (g.group_findings or []):
            if isinstance(gf, dict):
                # 历史空壳维度信号无可核对路径，不进入群组证据列表
                if is_hollow_dimension_signal(gf):
                    continue
                try:
                    group_findings.append(FindingItem(**gf))
                except Exception as exc:
                    logger.warning(
                        f"[CollusionResults] group {g.group_index} finding "
                        f"反序列化失败，已跳过: {type(exc).__name__}: {exc}"
                    )

        group_result_list.append(CollusionGroupResult(
            group_index=g.group_index,
            member_doc_ids=g.member_doc_ids or [],
            member_names=g.member_names or [],
            risk_level=g.risk_level or "medium",
            roles=roles,
            collusion_strategy=g.collusion_strategy,
            key_evidence_summary=g.network_description,
            investigation_focus=g.investigation_focus or [],
            related_pair_ids=g.related_pair_ids or [],
            network_description=g.network_description,
            findings=group_findings,
            group_confidence=(g.llm_group_analysis or {}).get("group_confidence"),
            uncertain_edge_count=int((g.llm_group_analysis or {}).get("uncertain_edge_count") or 0),
        ))

    # 构建全局特征发现(从group的aggregated_evidence中提取global_features来源的条目)
    # P0-3: dimension 映射加 D4/D8 覆盖，保证 pricing_pattern 与 metadata_cluster 穿透到 /results
    _DIM_TO_FINDING_TYPE = {
        "D1": "personnel_overlap",
        "D2": "company_relation",
        "D4": "pricing_pattern",
        "D8": "metadata_cluster",
    }
    global_finding_list: list[GlobalFindingItem] = []
    seen_descriptions: set[str] = set()
    for g in group_records:
        for ev in (g.aggregated_evidence or []):
            if isinstance(ev, dict) and ev.get("source") == "global_features":
                desc = ev.get("description", "")
                if desc and desc not in seen_descriptions:
                    seen_descriptions.add(desc)
                    dim = ev.get("dimension", "")
                    finding_type = (
                        str(ev.get("finding_type") or "").strip()
                        or _DIM_TO_FINDING_TYPE.get(dim, "unknown")
                    )
                    involved_ids = ev.get("involved_doc_ids") or g.member_doc_ids or []
                    involved_names = ev.get("involved_doc_names") or g.member_names or []
                    global_finding_list.append(GlobalFindingItem(
                        type=finding_type,
                        involved_doc_ids=list(involved_ids),
                        involved_names=list(involved_names),
                        description=desc,
                    ))

    # P0-3: 追加 Project.collusion_summary.global_findings_extra 里"未归入任何群组"的全局发现
    summary_blob = project.collusion_summary or {}
    for extra in summary_blob.get("global_findings_extra", []) or []:
        if not isinstance(extra, dict):
            continue
        desc = extra.get("description", "")
        if not desc or desc in seen_descriptions:
            continue
        seen_descriptions.add(desc)
        global_finding_list.append(GlobalFindingItem(
            type=str(extra.get("type") or "unknown"),
            involved_doc_ids=list(extra.get("involved_doc_ids") or []),
            involved_names=list(extra.get("involved_names") or []),
            description=desc,
        ))

    failed_pairs = sum(1 for r in pair_results if r.analysis_mode == "failed")
    skipped_pairs_count = sum(1 for r in pair_results if r.analysis_mode == "skipped")
    algorithm_only_pairs = sum(1 for r in pair_results if r.analysis_mode == "algorithm_only")
    review_required_pairs = 0
    clear_pairs = 0
    suspicious_pairs = 0
    for r in pair_results:
        llm = r.llm_analysis or {}
        decision_state = infer_decision_state(r)
        risk_level = str(llm.get("risk_level") or r.overall_risk or "").lower()
        if decision_state in {"needs_review", "skipped"}:
            review_required_pairs += 1
        elif decision_state == "decidable" and risk_level == "none":
            clear_pairs += 1
        elif decision_state == "decidable" and risk_level in {"high", "medium", "low"}:
            suspicious_pairs += 1
    llm_analyzed_pairs = sum(
        1 for r in pair_results
        if r.analysis_mode not in {"skipped", "algorithm_only"}
    )
    coverage_stats = CoverageStats(
        total_pairs=total_pairs,
        analyzed_pairs=len(pair_results),
        llm_analyzed_pairs=llm_analyzed_pairs,
        algorithm_only_pairs=algorithm_only_pairs,
        skipped_pairs=skipped_pairs_count,
        failed_pairs=failed_pairs,
        inconclusive_pairs=sum(1 for r in pair_results if r.overall_risk == "inconclusive"),
        review_required_pairs=review_required_pairs,
        clear_pairs=clear_pairs,
        suspicious_pairs=suspicious_pairs,
        coverage_ratio=round((len(pair_results) / total_pairs), 4) if total_pairs > 0 else 1.0,
    )
    summary_model = summary_load_or_build_project_summary(
        project=project,
        pair_results=pair_results,
        group_records=group_records,
        coverage_stats=coverage_stats,
        has_tender_doc=has_tender,
        doc_id_to_name=doc_id_to_name,
    )

    # 查询提取失败的文档，生成警告信息
    failed_q = await db.execute(
        select(CollusionExtraction.error_message, Document.filename)
        .join(Document, CollusionExtraction.document_id == Document.id)
        .where(
            CollusionExtraction.project_id == project_id,
            CollusionExtraction.extraction_status == "failed",
        )
    )
    extraction_warnings = [
        f"文件 [{fname}] 信息提取失败（{err or '未知原因'}），相关对比结果可能不准确"
        for err, fname in failed_q.all()
    ]

    results_payload = CollusionResults(
        project_id=project_id,
        overall_risk=(summary_model.project_risk_level or "inconclusive"),
        project_decision=summary_model.project_decision,
        project_risk_level=summary_model.project_risk_level,
        decision_confidence=summary_model.decision_confidence,
        review_required=summary_model.review_required,
        decision_basis=summary_model.decision_basis,
        decision_engine_version=summary_model.decision_engine_version,
        overall_risk_basis=summary_model.overall_risk_basis,
        risk_distribution_basis=summary_model.risk_distribution_basis,
        risk_consistency_status=summary_model.risk_consistency_status,
        risk_consistency_note=summary_model.risk_consistency_note,
        has_tender_doc=has_tender,
        summary_source_version=summary_model.summary_source_version,
        data_quality_flags=summary_model.data_quality_flags,
        profile_quality_flags=summary_model.profile_quality_flags,
        coverage_stats=summary_model.coverage_stats,
        industry_profile_snapshot=summary_model.industry_profile_snapshot,
        industry_profile_confidence=summary_model.industry_profile_confidence,
        industry_profile_status=getattr(summary_model, "industry_profile_status", None),
        industry_profile_signals=summary_model.industry_profile_signals,
        factual_explanation=getattr(summary_model, "factual_explanation", None),
        factual_explanation_items=getattr(summary_model, "factual_explanation_items", []),
        similarity_matrix=sim_matrix,
        document_labels=[doc_id_to_name.get(d, f"文件{d}") for d in doc_ids],
        pair_results=pair_result_list,
        suspected_groups=summary_model.suspected_groups,
        synthesis_summary=summary_model.summary,
        narrative_summary=summary_model.narrative_summary,
        investigation_suggestions=summary_model.investigation_suggestions,
        groups=group_result_list,
        global_findings=global_finding_list,
        extraction_warnings=extraction_warnings,
    )
    # A2 出口闸门: 调用 view_sanitize_collusion_results 完成
    # _load_or_build_project_summary → _sanitize_collusion_results 关键调用顺序（F4 契约锚点）
    view_sanitize_collusion_results(results_payload)
    return results_payload
