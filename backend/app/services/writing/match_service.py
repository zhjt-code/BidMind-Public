"""标书编写阶段 3：三元图匹配

承载 ``trigger_match`` + ``_vectorize_outline_nodes`` + 静态 helper
``_build_outline_embedding_text``。

接口约定（Task 5 Step 4）：
- ``project_resolver`` 注入：lambda 包装编排器的 ``_get_project_or_raise``
- ``trigger_match`` 内部使用 function-local import ``from app.core.tasks import
  is_task_active`` 以保持原契约：``test_writer_text_material_consumption`` 通过
  ``monkeypatch.setattr("app.core.tasks.is_task_active", ...)`` 影响行为
- ``_build_outline_embedding_text`` 作为 staticmethod 同时挂在
  ``MatchService`` 与 ``WritingService`` 两处，兼容
  ``WritingService._build_outline_embedding_text(...)`` 直接调用
"""

from __future__ import annotations

import json
from typing import Awaitable, Callable, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.user import User
from app.models.writing_outline_node import WritingOutlineNode
from app.services.writing.clause_readiness import WritingClauseReadinessService


class MatchService:
    """匹配阶段子服务"""

    def __init__(
        self,
        db: AsyncSession,
        *,
        project_resolver: Callable[..., Awaitable[Project]],
    ) -> None:
        self.db = db
        self._resolve_project = project_resolver

    async def trigger_match(
        self, project_id: int, user: User,
        *, reset_reviewed_bindings: bool = False,
    ) -> dict[str, str]:
        """触发三元图匹配(后台任务)

 匹配前置的重预处理放入后台任务内执行，保证接口快速返回。

 reset_reviewed_bindings=False（默认）时仅重算 suggested，保留人工
 confirmed/rejected；用户显式"重置审核并重新匹配"时才清空全部。
 """
        project = await self._resolve_project(project_id, user)
        await WritingClauseReadinessService(self.db).ensure_visible_clauses_confirmed(
            project_id
        )

        from app.core.tasks import is_task_active
        from app.core.tasks.writing_match_task import (
            get_writing_match_task_name,
            run_writing_match_task,
        )

        task_name = get_writing_match_task_name(project_id)
        if is_task_active(task_name):
            return {"task_name": task_name, "message": "匹配任务进行中"}

        # 结算后重新匹配必须新建预占（正常流程复用既有 HOLDING，零行为变化）
        from app.services.writing.material_billing_gate import (
            ensure_writing_billing_hold,
        )
        await ensure_writing_billing_hold(self.db, project, project.owner_id)

        from app.core.tasks import dispatch_background_job
        from app.services.retry_service import RetryService

        retry_svc = RetryService(self.db)
        run = await retry_svc.begin_attempt(
            project_id=project_id,
            node_key=RetryService.NODE_WRITING_MATCH,
            user_id=getattr(user, "id", None),
            task_name=task_name,
            explicit_retry=False,
            last_stage="queued",
        )
        queue_mode, arq_job_id = await dispatch_background_job(
            task_name=task_name,
            arq_function="writing_match_job",
            fallback_factory=lambda: run_writing_match_task(
                project_id, reset_reviewed_bindings=reset_reviewed_bindings
            ),
            task_type="writing_match",
            project_id=project_id,
            arq_args=(project_id, False, reset_reviewed_bindings),
        )
        await retry_svc.attach_dispatch_result(
            run,
            queue_mode=queue_mode,
            arq_job_id=arq_job_id,
            last_stage="queued",
        )
        return {
            "task_name": task_name,
            "message": "素材匹配已启动",
            "queue_mode": queue_mode,
        }

    async def _vectorize_outline_nodes(
        self,
        project_id: int,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> int:
        """对目录节点进行向量化(仅处理叶子节点)

        Args:
            project_id: 项目ID
            progress_callback: 向量化进度回调，签名为
                callback(current: int, total: int, message: str)

        Returns:
            int: 向量化的节点数量
        """

        def _report(current: int, total: int, message: str) -> None:
            if not progress_callback:
                return
            try:
                progress_callback(current, total, message)
            except Exception:
                pass

        result = await self.db.execute(
            select(WritingOutlineNode).where(
                WritingOutlineNode.project_id == project_id,
                WritingOutlineNode.embedding.is_(None),
            )
        )
        nodes = list(result.scalars().all())

        if not nodes:
            return 0

        try:
            total_units = max(len(nodes) + 3, 4)
            _report(1, total_units, f"已加载待向量目录节点 {len(nodes)} 个")
            logger.info(f"目录节点待向量化加载完成: project={project_id}, count={len(nodes)}")

            from app.core.vectorizer.encode_gate import safe_encode

            all_nodes_result = await self.db.execute(
                select(WritingOutlineNode).where(
                    WritingOutlineNode.project_id == project_id,
                )
            )
            all_nodes = list(all_nodes_result.scalars().all())
            node_map = {node.id: node for node in all_nodes}
            texts = [
                self._build_outline_embedding_text(node, node_map)
                for node in nodes
            ]
            _report(2, total_units, f"已构造目录向量文本 {len(texts)} 条")
            logger.info(f"目录节点向量文本构造完成: project={project_id}, count={len(texts)}")

            def _on_encode_progress(done: int, total: int) -> None:
                current = min(2 + done, total_units - 1)
                _report(current, total_units, f"目录向量化 {done}/{total}")

            embeddings = await safe_encode(
                texts,
                initial_batch_size=min(32, len(texts)),
                progress_callback=_on_encode_progress,
            )

            for node, emb in zip(nodes, embeddings):
                node.embedding = emb.tolist()

            _report(total_units - 1, total_units, "目录向量回写完成，准备提交")
            logger.info(f"目录节点向量回写完成: project={project_id}, count={len(nodes)}")

            await self.db.commit()
            _report(total_units, total_units, f"目录向量准备完成 {len(nodes)} 个节点")
            logger.info(f"目录节点向量化完成: project={project_id}, count={len(nodes)}")
            return len(nodes)

        except Exception as e:
            logger.warning(f"目录节点向量化失败(不阻断匹配): project={project_id}, err={e}")
            return 0

    @staticmethod
    def _build_outline_embedding_text(
        node: WritingOutlineNode,
        node_map: dict[int, WritingOutlineNode],
    ) -> str:
        """构造目录节点 embedding 文本，增强短标题的语义承载。"""
        path_parts: list[str] = []
        current: Optional[WritingOutlineNode] = node
        while current is not None:
            title = (current.title or "").strip()
            if title:
                path_parts.append(title)
            parent_id = getattr(current, "parent_id", None)
            current = node_map.get(parent_id) if parent_id is not None else None
        path_text = " > ".join(reversed(path_parts))

        profile_text = ""
        chapter_profile = getattr(node, "chapter_profile", None)
        if chapter_profile:
            try:
                profile = json.loads(chapter_profile)
            except (json.JSONDecodeError, TypeError):
                profile = {}
            if isinstance(profile, dict):
                semantic_role = str(profile.get("semantic_role") or "").strip()
                expects = "；".join(
                    str(item).strip()
                    for item in profile.get("expects", [])
                    if str(item).strip()
                )
                rejects = "；".join(
                    str(item).strip()
                    for item in profile.get("rejects", [])
                    if str(item).strip()
                )
                profile_parts = [
                    part for part in (
                        f"章节职责: {semantic_role}" if semantic_role else "",
                        f"重点覆盖: {expects}" if expects else "",
                        f"避免混入: {rejects}" if rejects else "",
                    )
                    if part
                ]
                profile_text = " | ".join(profile_parts)

        text_parts = [f"标题: {node.title}", f"路径: {path_text}"]
        if profile_text:
            text_parts.append(profile_text)
        return "\n".join(text_parts)


__all__ = ["MatchService"]
