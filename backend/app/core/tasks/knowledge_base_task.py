"""知识库资料后台处理任务。"""

from loguru import logger

from app.core.database import async_session_factory


def get_knowledge_base_process_task_name(document_id: int) -> str:
    """统一知识库处理任务名。"""
    return f"knowledge_base_process_{document_id}"


async def run_knowledge_base_process_task(document_id: int) -> None:
    """执行知识库资料解析、OCR/VL、分块和向量化。"""
    from app.core.tasks import update_heartbeat

    task_name = get_knowledge_base_process_task_name(document_id)
    update_heartbeat(task_name, stage="starting", progress="0%|初始化中")

    async with async_session_factory() as db:
        try:
            from app.services.knowledge_base_service import KnowledgeService

            logger.info(f"[知识库处理] 开始: document_id={document_id}")
            update_heartbeat(task_name, stage="processing", progress="10%|解析与识别")
            await KnowledgeService(db).execute_process_document(document_id)
            await db.commit()
            update_heartbeat(task_name, stage="completed", progress="100%|处理完成")
            logger.info(f"[知识库处理] 完成: document_id={document_id}")
        except Exception as exc:
            logger.error(
                f"[知识库处理] 失败: document_id={document_id}, err={exc}",
                exc_info=True,
            )
            update_heartbeat(
                task_name,
                stage="failed",
                progress=f"失败|{str(exc)[:200]}",
            )
            try:
                await db.rollback()
                from app.models.kb_document import KbDocument

                doc = await db.get(KbDocument, document_id)
                if doc:
                    doc.processing_status = "failed"
                    doc.error_message = str(exc)[:1000]
                    await db.commit()
            except Exception as db_exc:
                logger.error(
                    f"[知识库处理] 失败态回写异常(忽略): document_id={document_id}, err={db_exc}"
                )
