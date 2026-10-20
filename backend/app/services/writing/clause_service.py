"""标书编写阶段 1：约束 CRUD 与向量化

承载约束的 CRUD + 排序 helper + 向量化 worker 入口。
``trigger_extract`` 与 ``_ensure_clause_vectorize_task`` 因为依赖
``app.services.writing_service`` 模块级 import 符号（被
``test_writing_extract_trigger.py`` / ``test_writing_clause_confirm_fast_path.py``
通过 ``monkeypatch.setattr("app.services.writing_service.is_task_active", ...)``
patch 的方式覆盖）继续保留在 WritingService 主类，本服务通过 late-binding
callable 与之协作。

接口约定（Task 5 Step 2）：
- ``project_resolver`` 注入：lambda 包装编排器的 ``_get_project_or_raise``，
  保证测试在实例化 WritingService 后再 monkey patch 该方法仍然生效
- ``tender_doc_resolver`` 注入：同上，包装 ``_get_tender_doc``
- ``ensure_vectorize_task_trigger`` 注入：lambda 包装编排器的
  ``_ensure_clause_vectorize_task``，保证 ``confirm_clauses`` 触发的
  vectorize 任务遵循 monkey patch 后的实例方法
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ServiceError
from app.models.document import Document
from app.models.project import Project
from app.models.user import User
from app.models.writing_clause import WritingClause, WritingClauseLayer


class ClauseService:
    """约束 CRUD + 向量化子服务

    迁移自原 ``WritingService`` 的 ``# ==================== 阶段1: 约束提取 ====================``
    区块中的 5 个 CRUD 方法（list/create/update/delete/confirm）+ 2 个排序 helper
    + ``_vectorize_confirmed_clauses``；不含 ``trigger_extract`` /
    ``_ensure_clause_vectorize_task``（见 module docstring）。
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        project_resolver: Callable[..., Awaitable[Project]],
        tender_doc_resolver: Callable[[int], Awaitable[Optional[Document]]],
        ensure_vectorize_task_trigger: Callable[[int], Awaitable[None]],
    ) -> None:
        self.db = db
        self._resolve_project = project_resolver
        self._get_tender_doc = tender_doc_resolver
        self._trigger_vectorize_task = ensure_vectorize_task_trigger

    async def list_clauses(
        self, project_id: int, user: User,
        layer: Optional[str] = None,
    ) -> list[WritingClause]:
        """获取约束列表"""
        await self._resolve_project(project_id, user)
        query = select(WritingClause).where(
            WritingClause.project_id == project_id,
        )
        if layer:
            query = query.where(WritingClause.layer == layer)
        query = query.order_by(WritingClause.sort_order)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_scoring_report(
        self, project_id: int, user: User,
    ) -> Optional[dict[str, Any]]:
        """获取评分项按卷满分核计报告（约束确认页横幅）。

        无评分约束时返回 None（前端不渲染横幅）。校验口径与审查确认页同源。
        """
        from app.services.writing.scoring_report import build_scoring_report_payload

        await self._resolve_project(project_id, user)
        result = await self.db.execute(
            select(WritingClause).where(
                WritingClause.project_id == project_id,
                WritingClause.sub_type == "scoring_criterion",
            )
        )
        clauses = list(result.scalars().all())
        return build_scoring_report_payload(clauses)

    async def create_clause(
        self,
        project_id: int,
        user: User,
        data: dict[str, Any],
    ) -> WritingClause:
        """手动新增约束"""
        await self._resolve_project(project_id, user)

        tender_doc = await self._get_tender_doc(project_id)
        if not tender_doc:
            raise ServiceError("项目没有招标文档，请先上传招标文件")

        insert_after_id = data.pop("insert_after_id", None)
        sort_order = await self._get_next_clause_sort_order(project_id)
        if insert_after_id is not None:
            anchor = await self.db.get(WritingClause, insert_after_id)
            if not anchor or anchor.project_id != project_id:
                raise NotFoundError("插入位置对应的约束不存在")
            sort_order = anchor.sort_order + 1
            await self._shift_clause_sort_orders(project_id, sort_order, delta=1)

        clause = WritingClause(
            project_id=project_id,
            document_id=tender_doc.id,
            layer=WritingClauseLayer(data["layer"]),
            sub_type=str(data["sub_type"]),
            content=str(data["content"]).strip(),
            raw_text=data.get("raw_text"),
            source_page=data.get("source_page"),
            source_page_start=data.get("source_page_start"),
            source_page_end=data.get("source_page_end"),
            score_weight=data.get("score_weight"),
            metadata_json=data.get("metadata_json"),
            confirmed=0,
            is_manual=1,
            sort_order=sort_order,
        )
        self.db.add(clause)
        await self.db.commit()
        await self.db.refresh(clause)
        return clause

    async def update_clause(
        self, project_id: int, clause_id: int,
        user: User, data: dict[str, Any],
    ) -> WritingClause:
        """编辑约束"""
        await self._resolve_project(project_id, user)
        clause = await self.db.get(WritingClause, clause_id)
        if not clause or clause.project_id != project_id:
            raise NotFoundError("约束不存在")
        should_revectorize = False
        for key, value in data.items():
            if value is not None and hasattr(clause, key):
                if key in {"content", "raw_text"} and getattr(clause, key) != value:
                    should_revectorize = clause.confirmed == 1
                setattr(clause, key, value)
        if should_revectorize:
            clause.embedding = None
        await self.db.commit()
        await self.db.refresh(clause)
        if should_revectorize:
            await self._trigger_vectorize_task(project_id)
        return clause

    async def delete_clause(
        self, project_id: int, clause_id: int, user: User,
    ) -> None:
        """删除约束并重排顺序"""
        await self._resolve_project(project_id, user)
        clause = await self.db.get(WritingClause, clause_id)
        if not clause or clause.project_id != project_id:
            raise NotFoundError("约束不存在")
        removed_sort_order = clause.sort_order
        await self.db.delete(clause)
        await self.db.flush()
        await self._shift_clause_sort_orders(project_id, removed_sort_order + 1, delta=-1)
        await self.db.commit()

    async def confirm_clauses(
        self, project_id: int, clause_ids: list[int], user: User,
    ) -> int:
        """批量确认约束并异步触发向量化。"""
        await self._resolve_project(project_id, user, require_owner=True)
        result = await self.db.execute(
            select(WritingClause).where(
                WritingClause.project_id == project_id,
                WritingClause.id.in_(clause_ids),
            )
        )
        clauses = list(result.scalars().all())
        for c in clauses:
            c.confirmed = 1
        await self.db.commit()
        if clauses:
            await self._trigger_vectorize_task(project_id)

        return len(clauses)

    async def _get_next_clause_sort_order(self, project_id: int) -> int:
        """返回当前项目约束列表的下一个排序值"""
        result = await self.db.execute(
            select(WritingClause).where(
                WritingClause.project_id == project_id,
            ).order_by(WritingClause.sort_order.desc(), WritingClause.id.desc())
        )
        clause = result.scalars().first()
        return 0 if clause is None else int(clause.sort_order) + 1

    async def _shift_clause_sort_orders(
        self, project_id: int, start_sort_order: int, delta: int,
    ) -> None:
        """批量平移指定位置之后的约束排序"""
        if delta == 0:
            return
        result = await self.db.execute(
            select(WritingClause).where(
                WritingClause.project_id == project_id,
                WritingClause.sort_order >= start_sort_order,
            ).order_by(WritingClause.sort_order.desc() if delta > 0 else WritingClause.sort_order.asc())
        )
        clauses = list(result.scalars().all())
        for clause in clauses:
            clause.sort_order += delta

    async def _vectorize_confirmed_clauses(self, project_id: int) -> int:
        """对已确认的约束进行向量化。

        成功返回正整数；无待处理记录返回 0；失败直接抛出异常，
        由调用方(后台任务)决定心跳状态。

        Raises:
            RuntimeError / torch.cuda.OutOfMemoryError 等：向量化过程中的异常
        """

        result = await self.db.execute(
            select(WritingClause).where(
                WritingClause.project_id == project_id,
                WritingClause.confirmed == 1,
                WritingClause.embedding.is_(None),
            )
        )
        clauses = list(result.scalars().all())

        if not clauses:
            return 0

        from app.core.vectorizer.encode_gate import safe_encode

        texts = []
        for c in clauses:
            text = (c.content or "") + " " + (c.raw_text or "")
            texts.append(text.strip()[:2000])

        embeddings = await safe_encode(texts, initial_batch_size=min(32, len(texts)))

        for clause, emb in zip(clauses, embeddings):
            clause.embedding = emb.tolist()

        await self.db.commit()
        logger.info(f"约束向量化完成: project={project_id}, count={len(clauses)}")
        return len(clauses)


__all__ = ["ClauseService"]
