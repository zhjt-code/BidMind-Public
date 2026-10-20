"""审计日志服务

提供审计记录的写入、查询、统计和批量管理功能。
"""

from typing import Optional, Any
from datetime import datetime

from sqlalchemy import select, func, desc, delete
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.models.audit_log import AuditLog


class AuditService:
    """审计日志服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def log(
        self,
        *,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        category: str = "system",
        action: str,
        resource_type: str,
        resource_id: Optional[int] = None,
        detail: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> AuditLog:
        """写入一条审计记录"""
        entry = AuditLog(
            user_id=user_id,
            username=username,
            category=category,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail,
            ip_address=ip_address,
        )
        self.db.add(entry)
        await self.db.flush()
        return entry

    async def list_logs(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        category: Optional[str] = None,
        action: Optional[str] = None,
        resource_type: Optional[str] = None,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        keyword: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """分页查询审计日志"""
        query = select(AuditLog)
        count_query = select(func.count(AuditLog.id))

        filters = self._build_filters(
            category=category,
            action=action,
            resource_type=resource_type,
            user_id=user_id,
            username=username,
            keyword=keyword,
            start_date=start_date,
            end_date=end_date,
        )
        for f in filters:
            query = query.where(f)
            count_query = count_query.where(f)

        total_result = await self.db.execute(count_query)
        total: int = total_result.scalar() or 0

        offset = (page - 1) * page_size
        query = query.order_by(desc(AuditLog.created_at)).offset(offset).limit(page_size)

        result = await self.db.execute(query)
        items = result.scalars().all()

        total_pages = (total + page_size - 1) // page_size

        return {
            "items": list(items),
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }

    async def get_stats(self) -> dict[str, Any]:
        """按 category 和 action 统计审计日志"""
        cat_result = await self.db.execute(
            select(
                AuditLog.category,
                func.count(AuditLog.id),
            ).group_by(AuditLog.category)
        )
        by_category = {row[0]: row[1] for row in cat_result.all()}

        action_result = await self.db.execute(
            select(
                AuditLog.action,
                func.count(AuditLog.id),
            ).group_by(AuditLog.action)
        )
        by_action = {row[0]: row[1] for row in action_result.all()}

        total_result = await self.db.execute(
            select(func.count(AuditLog.id))
        )
        total: int = total_result.scalar() or 0

        return {
            "total": total,
            "by_category": by_category,
            "by_action": by_action,
        }

    async def batch_delete(
        self,
        *,
        log_ids: Optional[list[int]] = None,
        select_all: bool = False,
        exclude_ids: Optional[list[int]] = None,
        category: Optional[str] = None,
        action: Optional[str] = None,
        resource_type: Optional[str] = None,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        keyword: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> dict:
        """批量物理删除审计日志，支持按筛选结果全选"""
        target_ids = await self._resolve_target_ids(
            log_ids=log_ids,
            select_all=select_all,
            exclude_ids=exclude_ids,
            category=category,
            action=action,
            resource_type=resource_type,
            user_id=user_id,
            username=username,
            keyword=keyword,
            start_date=start_date,
            end_date=end_date,
        )
        if not target_ids:
            return {
                "deleted": 0,
                "matched": 0,
                "excluded": len(exclude_ids or []),
                "not_found": 0 if select_all else len(log_ids or []),
            }

        await self.db.execute(
            delete(AuditLog).where(AuditLog.id.in_(target_ids))
        )
        await self.db.flush()
        return {
            "deleted": len(target_ids),
            "matched": len(target_ids),
            "excluded": len(exclude_ids or []),
            "not_found": 0 if select_all else max(len(log_ids or []) - len(target_ids), 0),
        }

    # -- 内部辅助 --

    @staticmethod
    def _build_filters(
        *,
        category: Optional[str],
        action: Optional[str],
        resource_type: Optional[str],
        user_id: Optional[int],
        username: Optional[str],
        keyword: Optional[str],
        start_date: Optional[datetime],
        end_date: Optional[datetime],
    ) -> list:
        """构建公共筛选条件列表"""
        filters = []
        if category:
            filters.append(AuditLog.category == category)
        if action:
            filters.append(AuditLog.action == action)
        if resource_type:
            filters.append(AuditLog.resource_type == resource_type)
        if user_id is not None:
            filters.append(AuditLog.user_id == user_id)
        username_key = (username or "").strip()
        if username_key:
            filters.append(AuditLog.username.ilike(f"%{username_key}%"))
        if keyword:
            filters.append(AuditLog.detail.ilike(f"%{keyword}%"))
        if start_date:
            filters.append(AuditLog.created_at >= start_date)
        if end_date:
            filters.append(AuditLog.created_at <= end_date)
        return filters

    async def _resolve_target_ids(
        self,
        *,
        log_ids: Optional[list[int]],
        select_all: bool,
        exclude_ids: Optional[list[int]],
        category: Optional[str],
        action: Optional[str],
        resource_type: Optional[str],
        user_id: Optional[int],
        username: Optional[str],
        keyword: Optional[str],
        start_date: Optional[datetime],
        end_date: Optional[datetime],
    ) -> list[int]:
        """解析批量操作的目标 ID 列表"""
        if select_all:
            query = select(AuditLog.id)
            filters = self._build_filters(
                category=category,
                action=action,
                resource_type=resource_type,
                user_id=user_id,
                username=username,
                keyword=keyword,
                start_date=start_date,
                end_date=end_date,
            )
            for filter_item in filters:
                query = query.where(filter_item)
            if exclude_ids:
                query = query.where(~AuditLog.id.in_(exclude_ids))
            result = await self.db.execute(query)
            return list(result.scalars().all())

        if not log_ids:
            return []
        result = await self.db.execute(
            select(AuditLog.id).where(AuditLog.id.in_(log_ids))
        )
        return list(result.scalars().all())


async def audit_log(db: AsyncSession, **kwargs: Any) -> None:
    """模块级便捷函数，供 Service 层一行调用"""
    try:
        svc = AuditService(db)
        await svc.log(**kwargs)
    except Exception as e:
        logger.warning(f"审计日志写入失败(不影响业务): {e}")
