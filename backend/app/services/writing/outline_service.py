"""标书编写阶段 2：目录树生成与编辑

承载目录树生成触发 + 5 个 CRUD/排序方法 + depth 重算 helper。

接口约定（Task 5 Step 3）：
- ``project_resolver`` 注入：lambda 包装编排器的 ``_get_project_or_raise``
- ``render_invalidator`` 注入：lambda 包装编排器的 ``_invalidate_render_artifact``，
  保证 ``update_outline_node`` / ``delete_outline_node`` / ``reorder_outline``
  调用渲染快照失效时遵循 ``test_writer_outline_reorder`` 的
  ``service._invalidate_render_artifact = AsyncMock()`` monkey patch 契约
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ServiceError
from app.core.tasks import dispatch_background_job, is_task_active
from app.core.tasks.writing_outline_task import (
    get_writing_outline_task_name,
    run_writing_outline_task,
)
from app.core.writer.numbering import strip_existing_number
from app.models.project import Project, ProjectStatus
from app.models.user import User
from app.models.writing_outline_node import (
    OutlineNodeStatus,
    OutlineNodeType,
    WritingOutlineNode,
)
from app.services.writing.clause_readiness import WritingClauseReadinessService

_PAST_OUTLINE_STATUSES = frozenset({
    ProjectStatus.OUTLINED, ProjectStatus.MATCHING, ProjectStatus.MATCHED,
    ProjectStatus.WRITING, ProjectStatus.COMPOSED, ProjectStatus.FORMATTING,
    ProjectStatus.COMPLETED,
})

_TARGET_WORD_COUNT_LIMITS = {
    OutlineNodeType.data_fill: (100, 3000),
    OutlineNodeType.free_writing: (100, 5000),
}


class OutlineService:
    """目录树阶段子服务"""

    def __init__(
        self,
        db: AsyncSession,
        *,
        project_resolver: Callable[..., Awaitable[Project]],
        render_invalidator: Callable[[int], Awaitable[None]],
    ) -> None:
        self.db = db
        self._resolve_project = project_resolver
        self._invalidate_render = render_invalidator

    async def generate_outline(
        self, project_id: int, user: User, *, force: bool = False,
    ) -> dict[str, Any]:
        """触发目录树生成(后台任务)

        Args:
            force: 为 True 时允许清空已有目录并重建；
                   为 False 时，若目录已存在则幂等跳过。
        """
        project = await self._resolve_project(project_id, user)
        await WritingClauseReadinessService(self.db).ensure_visible_clauses_confirmed(
            project_id
        )

        task_name = get_writing_outline_task_name(project_id)
        if is_task_active(task_name):
            return {"task_name": task_name, "message": "目录生成任务进行中"}

        if not force and project.status in _PAST_OUTLINE_STATUSES:
            return {"task_name": None, "message": "目录已存在，跳过生成", "skipped": True}

        # 结算后重生成目录必须新建预占（正常流程复用提取期 HOLDING，零行为变化）
        from app.services.writing.material_billing_gate import (
            ensure_writing_billing_hold,
        )
        await ensure_writing_billing_hold(self.db, project, project.owner_id)

        project.status = ProjectStatus.OUTLINING
        project.error_message = None
        await self.db.commit()

        from app.services.retry_service import RetryService

        retry_svc = RetryService(self.db)
        run = await retry_svc.begin_attempt(
            project_id=project_id,
            node_key=RetryService.NODE_WRITING_OUTLINE,
            user_id=getattr(user, "id", None),
            task_name=task_name,
            explicit_retry=False,
            last_stage="queued",
        )
        queue_mode, arq_job_id = await dispatch_background_job(
            task_name=task_name,
            arq_function="writing_outline_job",
            fallback_factory=lambda: run_writing_outline_task(project_id),
            task_type="writing_outline",
            project_id=project_id,
            arq_args=(project_id,),
        )
        await retry_svc.attach_dispatch_result(
            run,
            queue_mode=queue_mode,
            arq_job_id=arq_job_id,
            last_stage="queued",
        )
        return {
            "task_name": task_name,
            "message": "目录生成已启动",
            "queue_mode": queue_mode,
        }

    async def get_outline(
        self, project_id: int, user: User,
    ) -> list[WritingOutlineNode]:
        """获取目录树"""
        await self._resolve_project(project_id, user)
        result = await self.db.execute(
            select(WritingOutlineNode).where(
                WritingOutlineNode.project_id == project_id,
            ).order_by(WritingOutlineNode.depth, WritingOutlineNode.sort_order)
        )
        return list(result.scalars().all())

    async def create_outline_node(
        self, project_id: int, user: User, data: dict[str, Any],
    ) -> WritingOutlineNode:
        """创建人工目录节点并立即持久化，供后续素材/匹配链路消费。"""
        await self._resolve_project(project_id, user)
        title = strip_existing_number(str(data.get("title") or "")).strip()
        if not title:
            raise ServiceError("目录标题不能为空")

        parent_id = data.get("parent_id")
        parent: WritingOutlineNode | None = None
        if parent_id is not None:
            parent = await self.db.get(WritingOutlineNode, int(parent_id))
            if not parent or parent.project_id != project_id:
                raise NotFoundError("父节点不存在")

        node_type = OutlineNodeType(str(data.get("node_type") or OutlineNodeType.free_writing.value))
        raw_target_word_count = data.get("target_word_count")
        target_word_count = (
            None if raw_target_word_count in ("", None) else int(raw_target_word_count)
        )
        if node_type == OutlineNodeType.fixed_template:
            target_word_count = None
        elif target_word_count is not None:
            min_word_count, max_word_count = _TARGET_WORD_COUNT_LIMITS.get(
                node_type, (100, 5000)
            )
            if not (min_word_count <= target_word_count <= max_word_count):
                raise ServiceError(
                    f"{node_type.value} 章节建议字数需在 {min_word_count} 到 {max_word_count} 之间"
                )

        target_parent_id = parent.id if parent else None
        insert_after_id = data.get("insert_after_id")
        insert_position = str(data.get("insert_position") or "last")
        if insert_after_id is not None:
            insert_position = "after"

        if insert_position == "first":
            next_sort_order = 0
            await self._shift_outline_siblings_from(
                project_id, target_parent_id, next_sort_order,
            )
        elif insert_position == "after":
            if insert_after_id is None:
                raise ServiceError("同级插入必须指定锚点节点")
            anchor = await self.db.get(WritingOutlineNode, int(insert_after_id))
            if not anchor or anchor.project_id != project_id:
                raise NotFoundError("插入锚点不存在")
            if anchor.parent_id != target_parent_id:
                raise ServiceError("插入锚点必须与新节点属于同一父级")
            next_sort_order = anchor.sort_order + 1
            await self._shift_outline_siblings_from(
                project_id, target_parent_id, next_sort_order,
            )
        else:
            sort_result = await self.db.execute(
                select(func.max(WritingOutlineNode.sort_order)).where(
                    WritingOutlineNode.project_id == project_id,
                    WritingOutlineNode.parent_id == target_parent_id,
                )
            )
            max_sort_order = sort_result.scalar()
            next_sort_order = (-1 if max_sort_order is None else max_sort_order) + 1

        node = WritingOutlineNode(
            project_id=project_id,
            parent_id=target_parent_id,
            title=title,
            depth=(parent.depth + 1) if parent else 0,
            sort_order=next_sort_order,
            node_type=node_type,
            is_required=int(data.get("is_required", 1)),
            score_weight=data.get("score_weight"),
            target_word_count=target_word_count,
            status=OutlineNodeStatus.user_edited,
            volume_type=parent.volume_type if parent else data.get("volume_type"),
            is_skeleton=0,
        )
        self.db.add(node)
        await self._invalidate_render(project_id)
        await self.db.commit()
        await self.db.refresh(node)
        return node

    async def _shift_outline_siblings_from(
        self, project_id: int, parent_id: Optional[int], start_sort_order: int,
    ) -> None:
        """为指定父级的插入位置腾出排序槽位。"""
        result = await self.db.execute(
            select(WritingOutlineNode).where(
                WritingOutlineNode.project_id == project_id,
                WritingOutlineNode.parent_id == parent_id,
                WritingOutlineNode.sort_order >= start_sort_order,
            ).order_by(WritingOutlineNode.sort_order.desc())
        )
        for sibling in result.scalars().all():
            sibling.sort_order += 1

    async def update_outline_node(
        self, project_id: int, node_id: int,
        user: User, data: dict[str, Any],
    ) -> WritingOutlineNode:
        """编辑目录节点"""
        await self._resolve_project(project_id, user)
        node = await self.db.get(WritingOutlineNode, node_id)
        if not node or node.project_id != project_id:
            raise NotFoundError("节点不存在")
        if "title" in data and data["title"] is not None:
            data["title"] = strip_existing_number(str(data["title"]))
        if "node_type" in data and data["node_type"] is not None:
            data["node_type"] = OutlineNodeType(str(data["node_type"]))
        effective_node_type = data.get("node_type") or getattr(
            node, "node_type", OutlineNodeType.free_writing
        )
        if not isinstance(effective_node_type, OutlineNodeType):
            effective_node_type = OutlineNodeType(str(effective_node_type))
        if "target_word_count" in data:
            raw_target_word_count = data["target_word_count"]
            data["target_word_count"] = (
                None if raw_target_word_count in ("", None) else int(raw_target_word_count)
            )
        if effective_node_type == OutlineNodeType.fixed_template:
            data["target_word_count"] = None
        elif data.get("target_word_count") is not None:
            min_word_count, max_word_count = _TARGET_WORD_COUNT_LIMITS.get(
                effective_node_type, (100, 5000)
            )
            target_word_count = data["target_word_count"]
            if not (min_word_count <= target_word_count <= max_word_count):
                raise ServiceError(
                    f"{effective_node_type.value} 章节建议字数需在 {min_word_count} 到 {max_word_count} 之间"
                )

        for key, value in data.items():
            if not hasattr(node, key):
                continue
            if key == "title" and value is None:
                continue
            if key == "node_type" and value is None:
                continue
            setattr(node, key, value)
        node.status = OutlineNodeStatus.user_edited
        await self._invalidate_render(project_id)
        await self.db.commit()
        await self.db.refresh(node)
        return node

    async def delete_outline_node(
        self, project_id: int, node_id: int, user: User,
    ) -> None:
        """删除目录节点"""
        await self._resolve_project(project_id, user, require_owner=True)
        node = await self.db.get(WritingOutlineNode, node_id)
        if not node or node.project_id != project_id:
            raise NotFoundError("节点不存在")
        await self.db.delete(node)
        await self._invalidate_render(project_id)
        await self.db.commit()

    async def reorder_outline(
        self, project_id: int, node_ids: list[int],
        parent_id: Optional[int], user: User,
    ) -> None:
        """拖拽排序"""
        await self._resolve_project(project_id, user)
        if len(set(node_ids)) != len(node_ids):
            raise ServiceError("目录排序列表包含重复节点")

        result = await self.db.execute(
            select(WritingOutlineNode).where(
                WritingOutlineNode.project_id == project_id,
                WritingOutlineNode.parent_id == parent_id,
            )
        )
        siblings = list(result.scalars().all())
        sibling_ids = {node.id for node in siblings}
        ordered_ids = set(node_ids)
        if ordered_ids != sibling_ids:
            raise ServiceError("目录排序必须提交同一父级下的完整节点列表")

        nodes_by_id = {node.id: node for node in siblings}
        for idx, nid in enumerate(node_ids):
            node = nodes_by_id[nid]
            node.sort_order = idx
            node.status = OutlineNodeStatus.user_edited
        await self._recalculate_outline_depths(project_id)
        await self._invalidate_render(project_id)
        await self.db.commit()

    async def _recalculate_outline_depths(self, project_id: int) -> None:
        """按当前 parent_id 关系重算整棵目录树的 depth 字段"""
        result = await self.db.execute(
            select(WritingOutlineNode).where(
                WritingOutlineNode.project_id == project_id,
            )
        )
        nodes = list(result.scalars().all())
        children_map: dict[Optional[int], list[WritingOutlineNode]] = {}
        for node in nodes:
            children_map.setdefault(node.parent_id, []).append(node)

        for siblings in children_map.values():
            siblings.sort(key=lambda item: item.sort_order)

        def _walk(parent_id: Optional[int], depth: int) -> None:
            for child in children_map.get(parent_id, []):
                child.depth = depth
                _walk(child.id, depth + 1)

        _walk(None, 0)


__all__ = ["OutlineService"]
