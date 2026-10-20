"""标书编写 -- 素材批处理后台任务

触发条件: 用户在素材管理页上传素材后自动触发
流程: 解析 -> PDF 拆分 -> 图片池去重 -> VL 识别 -> 组装 -> 向量化索引
      -> 图片分类 -> 标题精炼 -> 标签决议 -> 打标
心跳阶段(stage):
  queued -> parsing -> vl_recognition -> indexing -> image_classify
         -> label_resolve -> tagging -> completed / failed / cancelled
心跳 progress 字段约定: "xx%|message"; 其中 VL 阶段额外在 extra 里附 `vl_done/vl_total`。

该任务严格复用 WritingService._execute_material_batch(...), 禁止在此处再起旁路业务,
以满足任务契约: worker 与 asyncio 回退必须走同一实现。

行级状态契约（2026-04 更新，配合前端 WritingMaterials 的完成判定）：
- `processing_status` 保持为 "processing"，直到所有后置阶段（image_classify /
  label_resolve / tagging）跑完后，才在 `_execute_material_batch` 末尾统一写 "completed"；
- 如果任务中途崩溃，下面 `except Exception` 分支会把所有仍处于 "processing" 的行标记为
  "failed"，用户可重试；这是当前契约刻意牺牲"部分完成"语义换取的一致性保证：
  任务终态一旦为 failed，前端/下游不得再把任何素材当成已完成。
"""

from loguru import logger

from app.core.database import async_session_factory
from app.core.observability.task_observation import observe_task, TaskFlow


def get_writing_material_process_task_name(project_id: int) -> str:
    """统一任务名格式"""
    return f"writing_material_process_{project_id}"


@observe_task(flow=TaskFlow.WRITER, task_type="material_process")
async def run_writing_material_process_task(project_id: int) -> None:
    """执行写标素材批处理任务

    该函数同时被 ARQ worker 与 asyncio 回退路径调用,
    必须自行管理 DB session、心跳、取消墓碑与失败态。
    """
    from app.core.tasks import update_heartbeat
    from app.core.arq_runtime import is_project_cancelled

    task_name = get_writing_material_process_task_name(project_id)
    update_heartbeat(task_name, stage="starting", progress="0%|初始化中")

    if is_project_cancelled(project_id):
        logger.info(f"[素材批处理] 项目已取消/删除, 跳过执行: project_id={project_id}")
        update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
        return

    from app.core.arq_runtime import ProjectCancelledError

    async with async_session_factory() as db:
        try:
            from app.services.retry_service import RetryService
            from app.services.writing_service import WritingService

            await RetryService(db).mark_writing_material_task_running(
                project_id,
                stage="starting",
            )
            svc = WritingService(db)
            await svc._execute_material_batch(project_id, task_name=task_name)
            await RetryService(db).mark_writing_material_task_completed(project_id)
            logger.info(f"[素材批处理] 任务完成: project_id={project_id}")

        except ProjectCancelledError:
            logger.info(f"[素材批处理] 项目已取消, 静默退出: project_id={project_id}")
            update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
            try:
                await db.rollback()
            except Exception:
                pass

        except Exception as e:
            logger.error(f"[素材批处理] 失败: project_id={project_id}, err={e}", exc_info=True)
            update_heartbeat(
                task_name,
                stage="failed",
                progress=f"失败|{str(e)[:200]}",
            )
            try:
                await db.rollback()
                from app.services.retry_service import RetryService
                await RetryService(db).mark_writing_material_task_failed(
                    project_id,
                    stage="failed",
                    error=str(e),
                )
                from sqlalchemy import update
                from app.models.writing_material import WritingMaterial

                await db.execute(
                    update(WritingMaterial)
                    .where(
                        WritingMaterial.project_id == project_id,
                        WritingMaterial.processing_status == "processing",
                    )
                    .values(processing_status="failed")
                )
                await db.commit()
            except Exception as db_err:
                logger.error(
                    f"[素材批处理] 失败态回写异常(忽略): project={project_id}, err={db_err}"
                )
