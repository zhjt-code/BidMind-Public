"""一次性脚本：激活 P1-1 新增的 4 个串标节点专用 system prompt

职责: 一次性把 P1-1 新增的 4 个 system prompt code 版本 activate 到 DB 当前生效版本
不做: 不修改 user prompt 激活状态、不删除历史版本、不创建新 node

背景（P1-1）：
seed_llm_registry.py 的 _sync_code_versions 在检测到代码 prompt 变化时，会创建
LlmTemplateVersion(role='system', source='code', is_active=False, status='draft')
但不会自动激活。因此即便 seed 已经写入新 system prompt 版本，运行时
PromptRegistry 仍读取旧 active 版本（COLLUSION_SYSTEM_PROMPT）。

本脚本对以下 4 个节点完成 "新版本 activate + 旧版本 deactivate" 的迁移：
  - collusion.tender_format_extract
  - collusion.route_extract
  - collusion.industry_signal
  - collusion.industry_profile_gen

执行规则（幂等）：
1. 仅处理 role='system' 版本，user 完全不动
2. 选最高 version 且 source='code' 的版本作为目标
3. 若目标版本已经 is_active=True 且 status='published'，跳过（脚本可重复执行）
4. 否则：先 is_active=False 所有同节点同 role 旧版本，再 is_active=True 目标版本
5. 输出操作摘要

用法：
    cd backend
    # 必须先执行 seed_llm_registry 让新 prompt 版本入库（draft）：
    python -m scripts.seed_llm_registry
    # 然后激活：
    python -m scripts.activate_collusion_system_prompts

回滚：
    在 DB 中手动把旧版本（version 较小者）的 is_active 改回 True 即可，
    或写反向脚本调用本脚本同样的链路。
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

_backend_root = Path(__file__).resolve().parent.parent
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))


TARGET_NODE_KEYS: tuple[str, ...] = (
    "collusion.tender_format_extract",
    "collusion.route_extract",
    "collusion.industry_signal",
    "collusion.industry_profile_gen",
)


async def _activate_for_node(
    db: AsyncSession, node_id: int, node_key: str, stats: dict,
) -> None:
    """对单个节点的 role='system' 完成迁移。"""
    from app.models.llm_template_version import LlmTemplateVersion

    target = (await db.execute(
        select(LlmTemplateVersion)
        .where(
            LlmTemplateVersion.node_id == node_id,
            LlmTemplateVersion.template_role == "system",
            LlmTemplateVersion.source == "code",
        )
        .order_by(LlmTemplateVersion.version.desc())
        .limit(1)
    )).scalar_one_or_none()

    if not target:
        logger.warning(
            f"[Activate] {node_key}: 未找到 role='system' source='code' 版本, "
            f"请先执行 seed_llm_registry"
        )
        stats["skipped_no_target"] += 1
        return

    if target.is_active and target.status == "published":
        logger.info(
            f"[Activate] {node_key}: v{target.version} 已是 active+published, 跳过"
        )
        stats["already_active"] += 1
        return

    old_actives = (await db.execute(
        select(LlmTemplateVersion).where(
            LlmTemplateVersion.node_id == node_id,
            LlmTemplateVersion.template_role == "system",
            LlmTemplateVersion.is_active.is_(True),
            LlmTemplateVersion.id != target.id,
        )
    )).scalars().all()

    for old in old_actives:
        old.is_active = False
        logger.info(
            f"[Activate] {node_key}: v{old.version} (旧 active) -> is_active=False"
        )

    target.is_active = True
    target.status = "published"
    target.published_at = datetime.now(timezone.utc)
    if not target.change_note:
        target.change_note = "P1-1 激活：4 个节点专用 system prompt"

    logger.info(
        f"[Activate] {node_key}: v{target.version} -> is_active=True, status='published'"
    )
    stats["activated"] += 1


async def run() -> None:
    """主入口：遍历目标节点并完成激活。"""
    from app.core.database import async_session_factory
    from app.models.llm_node import LlmNode

    stats = {
        "activated": 0,
        "already_active": 0,
        "skipped_no_target": 0,
        "skipped_no_node": 0,
    }

    async with async_session_factory() as db:
        for node_key in TARGET_NODE_KEYS:
            node = (await db.execute(
                select(LlmNode).where(LlmNode.node_key == node_key)
            )).scalar_one_or_none()

            if not node:
                logger.warning(
                    f"[Activate] {node_key}: 节点不存在, 跳过"
                )
                stats["skipped_no_node"] += 1
                continue

            await _activate_for_node(db, node.id, node_key, stats)

        await db.commit()

    logger.info("=" * 60)
    logger.info("[Activate] 迁移完成")
    logger.info(f"  激活新版本: {stats['activated']}")
    logger.info(f"  已激活跳过: {stats['already_active']}")
    logger.info(f"  无目标版本: {stats['skipped_no_target']}（请先执行 seed_llm_registry）")
    logger.info(f"  无节点跳过: {stats['skipped_no_node']}")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(run())
