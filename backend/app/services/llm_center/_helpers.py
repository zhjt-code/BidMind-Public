"""LLM Center 共享 helpers（R2-14 拆分产物）

跨多个 sub-service 共享的纯异步 helper 函数，避免主模块与 sub-service
之间循环 import。
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_node import LlmNode


async def _get_node_or_raise(db: AsyncSession, node_key: str) -> LlmNode:
    """根据 node_key 查找节点，不存在则抛 NotFoundError。

    被 NodeDetailService / DraftService / ValidateService / VersionService /
    TraceService 共用，因此提升到 helpers 作为 module-level async 函数，
    避免在每个 sub-service 内部重复实现。
    """
    node = (await db.execute(
        select(LlmNode).where(LlmNode.node_key == node_key)
    )).scalar_one_or_none()
    if not node:
        from app.core.exceptions import NotFoundError
        raise NotFoundError(f"节点 '{node_key}' 不存在")
    return node


async def _broadcast_invalidate(node_key: str | None) -> None:
    """跨进程广播 Prompt 失效事件 (非阻塞, 失败不影响业务返回)。

 - node_key=None: 全量 reload
 - node_key=str: 单节点 refresh

 Redis 不可用或广播异常时仅打 WARNING (见 PromptRegistry.publish_invalidate)。

 防御：publish / rollback / update_node_config / resync_registry
 四个写入路径必须在 DB 提交后调用本函数，确保跨进程（API uvicorn 多 worker
 + arq worker）的 PromptRegistry 缓存一致性。
 """
    try:
        from app.core.redis import get_redis_pool
        from app.core.ai.prompt_registry import get_registry
        redis_client = await get_redis_pool()
        await get_registry().publish_invalidate(redis_client, node_key)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"[LLM Center] 跨进程广播 invalidate 失败 (不影响业务): node_key={node_key}, err={e}"
        )
