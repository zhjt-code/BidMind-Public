"""标书编写三元绑定（约束-章节-素材）

承载 list/update/confirm bindings。

接口约定（Task 5 Step 5）：
- ``project_resolver`` 注入：lambda 包装编排器的 ``_get_project_or_raise``
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.project import Project
from app.models.user import User
from app.models.writing_binding import BindingStatus, WritingBinding
from app.services.writing.binding_invalidation_service import (
    BindingInvalidationService,
)


class BindingService:
    """三元绑定子服务"""

    def __init__(
        self,
        db: AsyncSession,
        *,
        project_resolver: Callable[..., Awaitable[Project]],
    ) -> None:
        self.db = db
        self._resolve_project = project_resolver
        self._invalidation = BindingInvalidationService(db)

    async def list_bindings(
        self, project_id: int, user: User,
    ) -> list[WritingBinding]:
        """绑定列表"""
        await self._resolve_project(project_id, user)
        result = await self.db.execute(
            select(WritingBinding).where(
                WritingBinding.project_id == project_id,
            ).order_by(WritingBinding.id)
        )
        return list(result.scalars().all())

    async def update_binding(
        self, project_id: int, binding_id: int,
        user: User, data: dict[str, Any],
    ) -> WritingBinding:
        """调整绑定"""
        await self._resolve_project(project_id, user)
        binding = await self.db.get(WritingBinding, binding_id)
        if not binding or binding.project_id != project_id:
            raise NotFoundError("绑定不存在")
        old_outline_node_id = getattr(binding, "outline_node_id", None)
        for key, value in data.items():
            if value is not None and hasattr(binding, key):
                setattr(binding, key, value)
        if data.get("outline_node_id") is not None:
            binding.scope = None
        impacted_bindings: list[Any] = [binding]
        if (
            old_outline_node_id is not None
            and old_outline_node_id != getattr(binding, "outline_node_id", None)
        ):
            impacted_bindings.append(
                type("_ImpactedBinding", (), {"outline_node_id": old_outline_node_id})()
            )
        await self._invalidation.mark_changed_bindings_stale(
            project_id, impacted_bindings,
        )
        await self.db.commit()
        await self.db.refresh(binding)
        return binding

    async def confirm_bindings(
        self, project_id: int, binding_ids: list[int], user: User,
    ) -> int:
        """批量确认绑定"""
        await self._resolve_project(project_id, user)
        result = await self.db.execute(
            select(WritingBinding).where(
                WritingBinding.project_id == project_id,
                WritingBinding.id.in_(binding_ids),
            )
        )
        bindings = list(result.scalars().all())
        for b in bindings:
            b.status = BindingStatus.confirmed
        await self._invalidation.mark_changed_bindings_stale(project_id, bindings)
        await self.db.commit()
        return len(bindings)


__all__ = ["BindingService"]
