"""DraftService（R2-14 拆分产物）

LLM Center 草稿生命周期管理：草稿创建/更新 + 丢弃。

公共方法：
- discard_draft: 丢弃未发布的草稿版本（含 draft + validated 双状态）
- create_or_update_draft: 创建新草稿或更新现有草稿（按 template_role 隔离）

审计：discard_draft / save_draft 写 audit_log（防御）。
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select, func, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_template_version import LlmTemplateVersion
from app.schemas.llm_center import TemplateVersionInfo, TemplateVersionCreate

from app.services.llm_center._helpers import _get_node_or_raise


class DraftService:
    """LLM Center 草稿生命周期管理服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def discard_draft(self, node_key: str, username: str = "") -> int:
        """丢弃未发布的草稿版本，返回删除数量"""
        node = await _get_node_or_raise(self.db, node_key)
        result = await self.db.execute(
            sa_delete(LlmTemplateVersion).where(
                LlmTemplateVersion.node_id == node.id,
                LlmTemplateVersion.status.in_(["draft", "validated"]),
            )
        )
        await self.db.flush()
        count = result.rowcount

        logger.info(f"[LLM Center] discard_draft: {node_key} 丢弃 {count} 个草稿 (by {username})")
        from app.services.audit_service import audit_log
        await audit_log(
            self.db, username=username, category="llm_center",
            action="discard_draft", resource_type="llm_node",
            detail=f"丢弃 {node_key} 的 {count} 个草稿版本",
        )
        return count

    async def create_or_update_draft(
        self, node_key: str, data: TemplateVersionCreate, username: str
    ) -> TemplateVersionInfo:
        """创建/更新草稿版本"""
        node = await _get_node_or_raise(self.db, node_key)

        stale_validated = (await self.db.execute(
            select(LlmTemplateVersion).where(
                LlmTemplateVersion.node_id == node.id,
                LlmTemplateVersion.template_role == data.template_role,
                LlmTemplateVersion.status == "validated",
                LlmTemplateVersion.is_active == False,
            )
        )).scalars().all()
        for stale in stale_validated:
            stale.status = "archived"

        existing_draft = (await self.db.execute(
            select(LlmTemplateVersion).where(
                LlmTemplateVersion.node_id == node.id,
                LlmTemplateVersion.template_role == data.template_role,
                LlmTemplateVersion.status == "draft",
            )
        )).scalar_one_or_none()

        if existing_draft:
            existing_draft.content = data.content
            existing_draft.change_note = data.change_note
            existing_draft.created_by = username
            await self.db.flush()
            v = existing_draft
        else:
            max_ver = (await self.db.execute(
                select(func.max(LlmTemplateVersion.version)).where(
                    LlmTemplateVersion.node_id == node.id,
                    LlmTemplateVersion.template_role == data.template_role,
                )
            )).scalar() or 0

            v = LlmTemplateVersion(
                node_id=node.id, template_role=data.template_role,
                version=max_ver + 1, content=data.content,
                is_active=False, source="db", status="draft",
                change_note=data.change_note, created_by=username,
            )
            self.db.add(v)
            await self.db.flush()

        logger.info(f"[LLM Center] save_draft: {node_key} [{data.template_role}] v{v.version} (by {username})")
        from app.services.audit_service import audit_log
        await audit_log(
            self.db, username=username, category="llm_center",
            action="save_draft", resource_type="llm_node",
            detail=f"保存草稿 {node_key} [{data.template_role}] v{v.version}",
        )

        return TemplateVersionInfo(
            id=v.id, template_role=v.template_role, version=v.version,
            content=v.content, is_active=v.is_active, source=v.source,
            status=v.status, change_note=v.change_note, created_by=v.created_by,
            validated_at=v.validated_at, published_at=v.published_at,
            created_at=v.created_at,
        )
