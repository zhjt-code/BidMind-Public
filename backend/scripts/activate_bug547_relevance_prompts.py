"""一次性脚本：激活 证据载体轴 Prompt 新版本

职责: 把 seed_llm_registry 检测到的 relevance/tender_extract 代码新版本 activate 为当前生效版本
不做: 不创建新 node、不删除历史版本、不动其它节点

背景（P1）：
relevance_classify（证据载体轴重构）与 tender_extract（混合条款拆内核/占位符指令）
的代码 Prompt 已更新。seed_llm_registry 的 _sync_code_versions 会为差异内容创建
draft 版本但不激活，运行时 PromptRegistry 仍读旧 active 版本。本脚本按
activate_collusion_system_prompts 同一模式完成 "新版本 activate + 旧版本 deactivate"。

执行规则（幂等）：
1. 对每个目标节点的 system 与 user 两个 role 分别处理
2. 选最高 version 且 source='code' 的版本作为目标
3. 若目标已 is_active=True 且 status='published'，跳过（可重复执行）
4. 否则：先 deactivate 同节点同 role 旧版本，再 activate 目标版本

用法：
 cd backend
 python -m scripts.seed_llm_registry
 python -m scripts.activate_bug547_relevance_prompts

回滚：
 LLM Center 对应节点执行版本回滚，或在 DB 把旧版本 is_active 改回 True。
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_backend_root = Path(__file__).resolve().parent.parent
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))


# (node_key, roles) —— secondary_merge_decision 与 tender_extract_page 共享 system 常量,
# 其 system 亦会被 _sync_code_versions 检出新版本, 一并激活保持一致。
TARGET_NODES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bid_review.relevance_classify", ("system", "user")),
    ("bid_review.tender_extract_page", ("system", "user")),
    ("bid_review.secondary_merge_decision", ("system",)),
)

CHANGE_NOTE = " 激活：证据载体轴判定 + 混合条款拆内核/占位符指令"


async def _activate_role(
    db: AsyncSession, node_id: int, node_key: str, role: str, stats: dict,
) -> None:
    """对单个节点的单个 role 完成 "最新 code 版本激活" 迁移。"""
    from app.models.llm_template_version import LlmTemplateVersion

    target = (await db.execute(
        select(LlmTemplateVersion)
        .where(
            LlmTemplateVersion.node_id == node_id,
            LlmTemplateVersion.template_role == role,
            LlmTemplateVersion.source == "code",
        )
        .order_by(LlmTemplateVersion.version.desc())
        .limit(1)
    )).scalar_one_or_none()

    if not target:
        logger.warning(
            f"[Activate] {node_key}/{role}: 未找到 source='code' 版本, "
            f"请先执行 seed_llm_registry"
        )
        stats["skipped_no_target"] += 1
        return

    if target.is_active and target.status == "published":
        logger.info(f"[Activate] {node_key}/{role}: v{target.version} 已生效, 跳过")
        stats["already_active"] += 1
        return

    old_actives = (await db.execute(
        select(LlmTemplateVersion).where(
            LlmTemplateVersion.node_id == node_id,
            LlmTemplateVersion.template_role == role,
            LlmTemplateVersion.is_active.is_(True),
            LlmTemplateVersion.id != target.id,
        )
    )).scalars().all()

    for old in old_actives:
        old.is_active = False
        logger.info(f"[Activate] {node_key}/{role}: v{old.version} (旧 active) -> False")

    target.is_active = True
    target.status = "published"
    target.published_at = datetime.now(timezone.utc)
    if not target.change_note or target.change_note == "代码变更自动检测":
        target.change_note = CHANGE_NOTE

    logger.info(f"[Activate] {node_key}/{role}: v{target.version} -> active+published")
    stats["activated"] += 1


async def run() -> None:
    """主入口：遍历目标节点与 role 完成激活，并广播缓存失效。"""
    from app.core.database import async_session_factory
    from app.models.llm_node import LlmNode

    stats = {
        "activated": 0,
        "already_active": 0,
        "skipped_no_target": 0,
        "skipped_no_node": 0,
    }

    async with async_session_factory() as db:
        for node_key, roles in TARGET_NODES:
            node = (await db.execute(
                select(LlmNode).where(LlmNode.node_key == node_key)
            )).scalar_one_or_none()

            if not node:
                logger.warning(f"[Activate] {node_key}: 节点不存在, 跳过")
                stats["skipped_no_node"] += 1
                continue

            for role in roles:
                await _activate_role(db, node.id, node_key, role, stats)

        await db.commit()

    # 广播其它进程刷新 PromptRegistry 缓存（防御；失败不阻断）
    try:
        from app.core.ai.prompt_registry import get_registry
        from app.core.redis import get_redis_pool

        redis_client = await get_redis_pool()
        for node_key, _ in TARGET_NODES:
            await get_registry().publish_invalidate(redis_client, node_key)
        logger.info("[Activate] 已广播 Prompt 缓存失效")
    except Exception as exc:
        logger.warning(f"[Activate] 缓存失效广播失败(需重启 worker 生效): {exc}")

    logger.info("=" * 60)
    logger.info("[Activate] Prompt 迁移完成")
    logger.info(f"  激活新版本: {stats['activated']}")
    logger.info(f"  已激活跳过: {stats['already_active']}")
    logger.info(f"  无目标版本: {stats['skipped_no_target']}（请先执行 seed_llm_registry）")
    logger.info(f"  无节点跳过: {stats['skipped_no_node']}")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(run())
