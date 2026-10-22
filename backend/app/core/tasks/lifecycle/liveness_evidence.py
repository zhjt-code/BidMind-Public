"""任务活性的外部证据判定

职责: 基于心跳快照判定 ARQ 队列侧是否确证仍持有该任务的 job
不做: 不给出活性最终结论、不写任何状态、不取消或重投任务、不访问 DB

背景:
 `mark_task_queued` 只在入队时写一次心跳，worker 领取前无人刷新。任务在 ARQ
 队列里正常排队时，心跳年龄会持续增长，熬过入队宽限再撞上心跳超时就被判失活
 并写 FAILED —— 把"排队等待"误当成"卡死"。本模块提供队列侧证据，让活性判定
 在心跳过期时还能区分这两种情况。

本模块含两个**保守方向相反**的判定，用途不同，切勿混用:
 - `has_confirmed_pending_arq_job`（判"是否挽救一次失活误判"）：只有能**明确证实**
 job 仍在才返回 True。否则 Redis 短暂异常就会让所有任务永久停留活跃态、永不
 失败。它只在证据充分时改变既有行为。
 - `is_arq_job_possibly_inflight`（判"恢复前是否放弃重投"）：**无法证伪即视为在途**，
 与 `arq_runtime.arq_job_present_async` 同向。因为误重投的代价远高于晚恢复一轮。: 挽救判定改以键类语义（result key = 已终局，不算在途）+ worker 存活为准，
不再以入队墙钟判死——三大流程存在合法超长任务，写死时长必然误杀。
`DEFAULT_QUEUE_PENDING_MAX_SEC` 仅在调用方未提供 ``arq_function`` 时作为最后防呆。
"""

from __future__ import annotations

import time
from typing import Any, Optional

from loguru import logger

# 未知调用方（未传 arq_function）的最后防呆: worker 永久宕机时不得无限挽救。
# 已传 arq_function 的调用方以 worker 存活为准，不受此墙钟约束。
DEFAULT_QUEUE_PENDING_MAX_SEC = 1800.0


def heartbeat_past_no_progress_window(heartbeat: Optional[dict[str, Any]]) -> bool:
    """心跳年龄是否已越过业务无进展窗。

 职责: 用 last_heartbeat（缺则 enqueued_at）对照 WORKER_TASK_NO_PROGRESS_TIMEOUT
 不做: 不探测 worker、不读 job 键、不写终态

 存活键过期不等于执行体消失。只有业务心跳也超过无进展窗，
 才把残留 in-progress 当僵尸。无法读到时间戳则不得当僵尸。
 """
    from app.core.config import settings

    window = float(getattr(settings, "WORKER_TASK_NO_PROGRESS_TIMEOUT", 1800))
    if window <= 0:
        return False
    ts = _coerce_float((heartbeat or {}).get("last_heartbeat"))
    if ts is None:
        ts = _coerce_float((heartbeat or {}).get("enqueued_at"))
    if ts is None:
        return False
    return (time.time() - ts) >= window


def _coerce_float(value: Any) -> Optional[float]:
    """Redis Hash 取回的值均为字符串，需安全转 float。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def has_confirmed_pending_arq_job(
    heartbeat: Optional[dict[str, Any]],
    *,
    arq_function: Optional[str] = None,
    max_pending_sec: float = DEFAULT_QUEUE_PENDING_MAX_SEC,
) -> bool:
    """心跳对应的 ARQ job 是否被确证仍在队列/运行/重试中。: 判定以业务事实为准，不以入队墙钟判死——三大流程存在合法的超长任务。
 返回 True 需同时满足全部条件（任一不满足即返回 False，保持既有失活判定）:
 1. 心跳标记 ``queue_mode == "arq"``（asyncio 兜底路径没有队列侧 job）
 2. 心跳携带 ``arq_job_id``
 3. Redis 中存在该 job 的**未终局**痕迹（排队/在途/重试）。result key 表示
 job 已终局（含异常终止），不得作为在途证据—— 中崩溃 job 的
 result key 曾把失活判定拖满 30 分钟
 4. 调用方给出 ``arq_function`` 时：worker 存活则挽救；存活键缺失未确认
 仍挽救。缺失已确认时，仅当心跳已越过无进展窗才放弃
 （：共环堵塞会让键过期但 job 仍在跑）。未给出 arq_function
 时回退 ``enqueued_at`` 滞留封顶 ``max_pending_sec``

 自然上界由业务锚定: in-progress 键 TTL 随 job_timeout 过期，job 键随 arq
 expires 过期，无需独立判死墙钟。
 """
    if not heartbeat:
        return False

    queue_mode = str(heartbeat.get("queue_mode") or "").strip().lower()
    if queue_mode != "arq":
        return False

    job_id = str(heartbeat.get("arq_job_id") or "").strip()
    if not job_id:
        return False

    try:
        from arq.constants import (
            in_progress_key_prefix,
            job_key_prefix,
            retry_key_prefix,
        )

        from app.core.redis import get_sync_redis

        redis_client = get_sync_redis()
        if redis_client is None:
            return False

        pending_keys = [
            f"{job_key_prefix}{job_id}",
            f"{in_progress_key_prefix}{job_id}",
            f"{retry_key_prefix}{job_id}",
        ]
        if not redis_client.exists(*pending_keys):
            # 无排队/在途/重试痕迹: job 已终局(仅剩 result key)或已消失, 不挽救
            return False
    except Exception as exc:
        # 无法证实即不挽救，行为与本模块引入前完全一致
        logger.debug(f"探测 ARQ job 在途证据失败，按未确证处理: job_id={job_id}, err={exc}")
        return False

    if arq_function:
        try:
            from app.core.arq_runtime import has_alive_worker_for
            from app.core.tasks.lifecycle import is_worker_confirmed_missing

            if has_alive_worker_for(arq_function):
                return True
            if is_worker_confirmed_missing(arq_function):
                if heartbeat_past_no_progress_window(heartbeat):
                    logger.warning(
                        f"ARQ job 有队列痕迹但 worker 缺失已确认且心跳越过无进展窗，"
                        f"按僵尸痕迹不挽救: job_id={job_id}, fn={arq_function}"
                    )
                    return False
                logger.info(
                    f"ARQ job 有队列痕迹、worker 键过期但心跳未越无进展窗，仍按在途挽救: "
                    f"job_id={job_id}, fn={arq_function}"
                )
                return True
            return True
        except Exception as exc:
            logger.debug(
                f"探测 worker 存活失败，队列痕迹仍在则按未确认缺失挽救: "
                f"job_id={job_id}, fn={arq_function}, err={exc}"
            )
            return True

    # 未知调用方(未传 arq_function): 保留入队滞留封顶作为最后防呆,
    # 防止 worker 永久宕机时任务无限停留活跃态
    enqueued_at = _coerce_float(heartbeat.get("enqueued_at"))
    if enqueued_at is not None:
        pending_for = time.time() - enqueued_at
        if pending_for > max_pending_sec:
            logger.warning(
                f"ARQ job 滞留队列过久且调用方未提供 worker 证据，不再按在途挽救: "
                f"job_id={job_id}, pending={pending_for:.0f}s > {max_pending_sec:.0f}s"
            )
            return False
    return True


async def is_arq_job_possibly_inflight(
    heartbeat: Optional[dict[str, Any]],
    *,
    task_name: str,
    arq_function: Optional[str] = None,
) -> bool:
    """恢复前守卫：旧 ARQ job 是否可能仍在途，为真则本轮不得重投。

 保守方向与 `has_confirmed_pending_arq_job` **相反**：此处无法证伪即视为在途。
 原因是 `cancel_task` 只能取消本进程的 asyncio 任务，对 ARQ worker 进程内正在
 执行的 job 无效；若此时重投，两个 job 会并发写同一项目并撞上
 `review_results` 的 `uq_review_project_requirement` 唯一约束——该前科已记录在
 `mark_task_queued` 的 docstring 中（"review / process 重复运行, 触发唯一键冲突
 和计费告警"）。误重投的代价远高于晚恢复一轮，故 Redis 异常时宁可放弃本轮恢复。

 封顶（收口）：传入 ``arq_function`` 且 worker 缺失已确认
 **并且**心跳已越过无进展窗时，残留 in-progress 视为僵尸，允许恢复（中和
 in_progress_timeout 拉长，）。仅存活键过期不得一票否决：心跳与业务
 共环，合法堵塞会让键过期但进程仍在跑。

 返回 False 的情形：
 - 心跳缺失且按 task_name 补探无痕迹（或补探不可用，回退旧「允许恢复」语义）
 - ``queue_mode`` 不是 arq
 - Redis 无 job 痕迹
 - 传入 ``arq_function`` 且 worker 缺失已确认且心跳越过无进展窗
 """
    from app.core.arq_runtime import arq_job_present_async
    from app.core.redis import get_redis_pool

    if heartbeat:
        queue_mode = str(heartbeat.get("queue_mode") or "").strip().lower()
        if queue_mode != "arq":
            return False
        # job_id 缺失时回退用 task_name 探测：项目部分入队路径以 task_name 作为 job_id
        job_id = str(heartbeat.get("arq_job_id") or "").strip() or task_name
        conservative_on_error = True
    else:
        # 同类收口: 心跳被清理/驱逐不等于队列无 job, 按确定性 task_name 补探。
        # 该分支为新增守卫, 探测不可用时必须回退旧「允许恢复」语义(返回 False),
        # 否则 Redis 故障会连 asyncio 兜底恢复一起阻断。
        job_id = task_name
        conservative_on_error = False

    try:
        pool = await get_redis_pool()
        if pool is None and not conservative_on_error:
            return False
        present = await arq_job_present_async(pool, job_id)
    except Exception as exc:
        logger.warning(
            f"恢复前探测 ARQ job 在途失败: task={task_name}, job_id={job_id}, "
            f"err={exc}, 按{'在途(放弃本轮恢复)' if conservative_on_error else '不在途(允许恢复)'}处理"
        )
        return conservative_on_error

    if not present:
        return False

    if arq_function:
        try:
            from app.core.tasks.lifecycle import is_worker_confirmed_missing

            if is_worker_confirmed_missing(arq_function):
                if heartbeat_past_no_progress_window(heartbeat):
                    logger.info(
                        f"ARQ 痕迹存在但 worker 缺失已确认且心跳越过无进展窗，"
                        f"按不在途允许恢复: task={task_name}, job_id={job_id}, "
                        f"fn={arq_function}"
                    )
                    return False
                logger.info(
                    f"ARQ 痕迹存在、worker 键过期但心跳未越无进展窗，仍视为在途: "
                    f"task={task_name}, job_id={job_id}, fn={arq_function}"
                )
                return True
        except Exception as exc:
            # worker 探测失败: 有心跳路径保持保守视为在途;
            # 心跳缺失的补探路径回退旧「允许恢复」语义(见上方分支注释)
            logger.debug(
                f"恢复前 worker 存活探测失败: task={task_name}, fn={arq_function}, "
                f"err={exc}, conservative={conservative_on_error}"
            )
            return conservative_on_error

    return True
