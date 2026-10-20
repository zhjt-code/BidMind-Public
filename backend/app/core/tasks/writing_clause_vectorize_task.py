"""标书编写 -- 约束向量化后台任务。

触发条件: 用户确认约束后异步补齐 embedding
流程: 扫描已确认且未向量化约束 -> 批量向量化 -> 保存结果
进度阶段: starting -> vectorizing -> completed / failed
"""

import asyncio

from loguru import logger

from app.core.database import async_session_factory


def get_writing_clause_vectorize_task_name(project_id: int) -> str:
    """获取任务名称。"""
    return f"writing_clause_vectorize_{project_id}"


async def run_writing_clause_vectorize_task(project_id: int) -> None:
    """执行约束向量化后台任务。

    _vectorize_confirmed_clauses 成功返回正整数，无待处理记录返回 0，
    失败抛出异常。本函数在异常时写 stage=failed 心跳，只有无异常且
    返回 0 时才视为完成。
    """
    from app.core.tasks import update_heartbeat
    from app.services.writing_service import WritingService
    from app.core.arq_runtime import is_project_cancelled

    task_name = get_writing_clause_vectorize_task_name(project_id)
    update_heartbeat(task_name, stage="starting", progress="0%|初始化")

    if is_project_cancelled(project_id):
        logger.info(f"[写标约束向量化] 项目已取消/删除, 跳过执行: project_id={project_id}")
        update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
        return

    async with async_session_factory() as db:
        try:
            service = WritingService(db)
            total_vectorized = 0

            for round_idx in range(1, 4):
                # 循环每一轮都检查一次, 避免删除后继续批量写向量
                if is_project_cancelled(project_id):
                    logger.info(
                        f"[写标约束向量化] 轮次中项目被取消, 停止: "
                        f"project_id={project_id}, round={round_idx}"
                    )
                    update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
                    return

                update_heartbeat(
                    task_name,
                    stage="vectorizing",
                    progress=f"{min(90, 10 + round_idx * 20)}%|第 {round_idx} 轮向量化",
                )
                vectorized = await service._vectorize_confirmed_clauses(project_id)
                total_vectorized += vectorized
                if vectorized == 0:
                    break
                await asyncio.sleep(0)

            update_heartbeat(
                task_name,
                stage="completed",
                progress=f"100%|完成，共向量化 {total_vectorized} 条约束",
            )
            logger.info(
                f"约束向量化后台任务完成: project={project_id}, count={total_vectorized}"
            )
        except Exception as e:
            logger.error(
                f"约束向量化后台任务失败: project={project_id}, err={e}",
                exc_info=True,
            )
            update_heartbeat(
                task_name,
                stage="failed",
                progress=f"失败|{str(e)[:200]}",
            )
