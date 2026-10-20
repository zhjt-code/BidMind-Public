"""系统错误日志服务

提供错误日志的查询、统计和状态管理功能。
"""

from typing import Optional, Any
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func, desc, case, extract, delete, update, distinct
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_error_log import SystemErrorLog
from app.core.message_fingerprint import (
    normalize_message, compute_fingerprint, FINGERPRINT_ALGORITHM_VERSION,
)
from app.models.log_suppression_rule import LogSuppressionRule
from app.models.user import User
from app.models.project import Project
from app.core.config import settings
from app.core.observability.traffic_class import resolve_query_traffic_class

import logging as _logging
_logger = _logging.getLogger(__name__)


class ErrorLogService:
    """系统错误日志服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_logs(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        category: Optional[str] = None,
        level: Optional[str] = None,
        source: Optional[str] = None,
        module: Optional[str] = None,
        task_type: Optional[str] = None,
        project_id: Optional[int] = None,
        user_id: Optional[int] = None,
        resolved: Optional[bool] = None,
        keyword: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        fingerprint: Optional[str] = None,
        incident_id: Optional[str] = None,
        traffic_class: Optional[str] = None,
    ) -> dict[str, Any]:
        """分页查询错误日志（LEFT JOIN 关联用户名和项目名）"""
        query = (
            select(
                SystemErrorLog,
                User.username.label("username"),
                Project.name.label("project_name"),
            )
            .outerjoin(User, SystemErrorLog.user_id == User.id)
            .outerjoin(Project, SystemErrorLog.project_id == Project.id)
        )
        count_query = select(func.count(SystemErrorLog.id))

        filters = self._build_filters(
            category=category, level=level, source=source, module=module,
            task_type=task_type, project_id=project_id, user_id=user_id,
            resolved=resolved, keyword=keyword,
            start_date=start_date, end_date=end_date,
            fingerprint=fingerprint,
            incident_id=incident_id,
            traffic_class=traffic_class,
        )
        for f in filters:
            query = query.where(f)
            count_query = count_query.where(f)

        total_result = await self.db.execute(count_query)
        total: int = total_result.scalar() or 0

        offset = (page - 1) * page_size
        query = query.order_by(desc(SystemErrorLog.created_at)).offset(offset).limit(page_size)

        result = await self.db.execute(query)
        rows = result.all()
        items = [
            {"log": row[0], "username": row[1], "project_name": row[2]}
            for row in rows
        ]
        total_pages = (total + page_size - 1) // page_size

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }

    async def list_grouped_logs(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "latest_at",
        category: Optional[str] = None,
        level: Optional[str] = None,
        source: Optional[str] = None,
        module: Optional[str] = None,
        task_type: Optional[str] = None,
        project_id: Optional[int] = None,
        user_id: Optional[int] = None,
        resolved: Optional[bool] = None,
        keyword: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        traffic_class: Optional[str] = None,
    ) -> dict[str, Any]:
        """按 message_fingerprint 分组返回聚合结果

        Args:
            sort_by: 排序字段, "latest_at"(默认按最新时间) 或 "count"(按数量)
        """
        fp_col = SystemErrorLog.message_fingerprint

        agg_cols = [
            fp_col.label("fingerprint"),
            func.max(SystemErrorLog.module).label("module"),
            func.max(SystemErrorLog.level).label("level"),
            func.max(SystemErrorLog.category).label("category"),
            func.max(SystemErrorLog.source).label("source"),
            func.max(SystemErrorLog.message).label("sample_message"),
            # 行数不再等于发生次数(同源回声已折叠), 必须按 occurrence_count 求和才不漏报
            func.sum(SystemErrorLog.occurrence_count).label("count"),
            func.sum(
                case((SystemErrorLog.resolved == False, 1), else_=0)  # noqa: E712
            ).label("unresolved_count"),
            func.max(SystemErrorLog.created_at).label("latest_at"),
            func.min(SystemErrorLog.created_at).label("earliest_at"),
            # 分组维度: 涉及用户数 / 项目数, 让分组视图能直观看出该错误的"影响面"
            # 区别于 unresolved_count(未处理条数), user_count/project_count 是"独立实体"统计
            func.count(distinct(SystemErrorLog.user_id)).label("user_count"),
            func.count(distinct(SystemErrorLog.project_id)).label("project_count"),
        ]

        filters = self._build_filters(
            category=category, level=level, source=source, module=module,
            task_type=task_type, project_id=project_id, user_id=user_id,
            resolved=resolved, keyword=keyword,
            start_date=start_date, end_date=end_date,
            traffic_class=traffic_class,
        )

        base = select(*agg_cols)
        for f in filters:
            base = base.where(f)
        # fingerprint 为 NULL 的历史数据,退化为单条一组
        base = base.where(fp_col.isnot(None))
        base = base.group_by(fp_col)

        count_sub = base.subquery()
        total_result = await self.db.execute(
            select(func.count()).select_from(count_sub)
        )
        total_groups: int = total_result.scalar() or 0

        order_col = desc("count") if sort_by == "count" else desc("latest_at")
        offset = (page - 1) * page_size
        query = base.order_by(order_col).offset(offset).limit(page_size)
        result = await self.db.execute(query)
        rows = result.all()

        groups = []
        for row in rows:
            sample_msg = row.sample_message or ""
            groups.append({
                "fingerprint": row.fingerprint,
                "module": row.module,
                "level": row.level,
                "category": row.category,
                "source": row.source,
                "sample_message": normalize_message(sample_msg),
                "count": int(row.count or 0),
                "unresolved_count": row.unresolved_count or 0,
                "latest_at": row.latest_at.isoformat() if row.latest_at else None,
                "earliest_at": row.earliest_at.isoformat() if row.earliest_at else None,
                "user_count": int(row.user_count or 0),
                "project_count": int(row.project_count or 0),
            })

        total_pages = (total_groups + page_size - 1) // page_size if total_groups else 0
        return {
            "groups": groups,
            "total_groups": total_groups,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }

    async def get_detail(self, log_id: int) -> Optional[dict[str, Any]]:
        """查询单条详情（LEFT JOIN 关联用户名和项目名）"""
        query = (
            select(
                SystemErrorLog,
                User.username.label("username"),
                Project.name.label("project_name"),
            )
            .outerjoin(User, SystemErrorLog.user_id == User.id)
            .outerjoin(Project, SystemErrorLog.project_id == Project.id)
            .where(SystemErrorLog.id == log_id)
        )
        result = await self.db.execute(query)
        row = result.one_or_none()
        if row is None:
            return None
        return {"log": row[0], "username": row[1], "project_name": row[2]}

    async def list_chain(self, incident_id: str) -> list[dict[str, Any]]:
        """按 incident_id 拉取整条错误链路, 按 chain_seq 升序返回。

        chain_seq 为 NULL 的兜底退到按 created_at 升序, 保证视觉上的时序连贯。
        """
        query = (
            select(
                SystemErrorLog,
                User.username.label("username"),
                Project.name.label("project_name"),
            )
            .outerjoin(User, SystemErrorLog.user_id == User.id)
            .outerjoin(Project, SystemErrorLog.project_id == Project.id)
            .where(SystemErrorLog.incident_id == incident_id)
            .order_by(
                SystemErrorLog.chain_seq.asc().nullslast(),
                SystemErrorLog.created_at.asc(),
            )
        )
        result = await self.db.execute(query)
        rows = result.all()
        return [
            {"log": row[0], "username": row[1], "project_name": row[2]}
            for row in rows
        ]

    async def get_stats(
        self,
        *,
        hours: int = 24,
        traffic_class: Optional[str] = None,
    ) -> dict[str, Any]:
        """错误统计(近 N 小时)"""
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        # 统计口径必须与列表一致: 默认只统计 production, 否则条数仍被测试噪声淹没
        scope = [
            SystemErrorLog.created_at >= since,
            *self._build_filters(traffic_class=traffic_class),
        ]

        # 按级别统计
        level_result = await self.db.execute(
            select(
                SystemErrorLog.level,
                func.count(SystemErrorLog.id),
            )
            .where(*scope)
            .group_by(SystemErrorLog.level)
        )
        by_level = {row[0]: row[1] for row in level_result.all()}

        # 按来源统计
        source_result = await self.db.execute(
            select(
                SystemErrorLog.source,
                func.count(SystemErrorLog.id),
            )
            .where(*scope)
            .group_by(SystemErrorLog.source)
        )
        by_source = {row[0]: row[1] for row in source_result.all()}

        # 按业务分类统计
        cat_result = await self.db.execute(
            select(
                SystemErrorLog.category,
                func.count(SystemErrorLog.id),
            )
            .where(*scope)
            .group_by(SystemErrorLog.category)
        )
        by_category = {row[0]: row[1] for row in cat_result.all()}

        # 未处理数
        unresolved_result = await self.db.execute(
            select(func.count(SystemErrorLog.id))
            .where(*scope)
            .where(SystemErrorLog.resolved == False)  # noqa: E712
        )
        unresolved: int = unresolved_result.scalar() or 0

        # 按小时分布(最近 hours 小时)
        hourly_result = await self.db.execute(
            select(
                extract("hour", SystemErrorLog.created_at).label("hour"),
                func.count(SystemErrorLog.id),
            )
            .where(*scope)
            .group_by("hour")
            .order_by("hour")
        )
        by_hour = {int(row[0]): row[1] for row in hourly_result.all()}

        # 按模块 Top10
        module_result = await self.db.execute(
            select(
                SystemErrorLog.module,
                func.count(SystemErrorLog.id).label("cnt"),
            )
            .where(*scope)
            .group_by(SystemErrorLog.module)
            .order_by(desc("cnt"))
            .limit(10)
        )
        top_modules = [{"module": row[0], "count": row[1]} for row in module_result.all()]

        total = sum(by_level.values())

        return {
            "total": total,
            "unresolved": unresolved,
            "by_level": by_level,
            "by_source": by_source,
            "by_category": by_category,
            "by_hour": by_hour,
            "top_modules": top_modules,
            "hours": hours,
        }

    async def resolve_log(
        self,
        log_id: int,
        *,
        resolved_by: str,
    ) -> Optional[SystemErrorLog]:
        """标记已处理"""
        result = await self.db.execute(
            select(SystemErrorLog).where(SystemErrorLog.id == log_id)
        )
        log = result.scalar_one_or_none()
        if log is None:
            return None
        log.resolved = True
        log.resolved_by = resolved_by
        log.resolved_at = datetime.now(timezone.utc)
        await self.db.flush()
        return log

    async def batch_delete(
        self,
        *,
        log_ids: Optional[list[int]] = None,
        select_all: bool = False,
        exclude_ids: Optional[list[int]] = None,
        category: Optional[str] = None,
        level: Optional[str] = None,
        source: Optional[str] = None,
        module: Optional[str] = None,
        task_type: Optional[str] = None,
        project_id: Optional[int] = None,
        user_id: Optional[int] = None,
        resolved: Optional[bool] = None,
        keyword: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        fingerprints: Optional[list[str]] = None,
        traffic_class: Optional[str] = None,
    ) -> dict:
        """批量物理删除错误日志，支持按筛选结果全选"""
        target_ids = await self._resolve_target_ids(
            log_ids=log_ids,
            select_all=select_all,
            exclude_ids=exclude_ids,
            category=category,
            level=level,
            source=source,
            module=module,
            task_type=task_type,
            project_id=project_id,
            user_id=user_id,
            resolved=resolved,
            keyword=keyword,
            start_date=start_date,
            end_date=end_date,
            fingerprints=fingerprints,
            traffic_class=traffic_class,
        )
        if not target_ids:
            return {
                "deleted": 0,
                "matched": 0,
                "excluded": len(exclude_ids or []),
                "not_found": 0 if select_all else len(log_ids or []),
            }

        await self.db.execute(
            delete(SystemErrorLog).where(SystemErrorLog.id.in_(target_ids))
        )
        await self.db.flush()
        return {
            "deleted": len(target_ids),
            "matched": len(target_ids),
            "excluded": len(exclude_ids or []),
            "not_found": 0 if select_all else max(len(log_ids or []) - len(target_ids), 0),
        }

    async def batch_resolve(
        self,
        log_ids: Optional[list[int]] = None,
        *,
        select_all: bool = False,
        exclude_ids: Optional[list[int]] = None,
        resolved_by: str,
        category: Optional[str] = None,
        level: Optional[str] = None,
        source: Optional[str] = None,
        module: Optional[str] = None,
        task_type: Optional[str] = None,
        project_id: Optional[int] = None,
        user_id: Optional[int] = None,
        resolved: Optional[bool] = None,
        keyword: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        fingerprints: Optional[list[str]] = None,
        traffic_class: Optional[str] = None,
    ) -> dict:
        """批量标记已处理，支持按筛选结果全选"""
        target_ids = await self._resolve_target_ids(
            log_ids=log_ids,
            select_all=select_all,
            exclude_ids=exclude_ids,
            category=category,
            level=level,
            source=source,
            module=module,
            task_type=task_type,
            project_id=project_id,
            user_id=user_id,
            resolved=resolved,
            keyword=keyword,
            start_date=start_date,
            end_date=end_date,
            fingerprints=fingerprints,
            traffic_class=traffic_class,
        )
        if not target_ids:
            return {
                "resolved": 0,
                "already_resolved": 0,
                "matched": 0,
                "excluded": len(exclude_ids or []),
                "not_found": 0 if select_all else len(log_ids or []),
            }

        result = await self.db.execute(
            select(SystemErrorLog.id, SystemErrorLog.resolved).where(
                SystemErrorLog.id.in_(target_ids)
            )
        )
        rows = result.all()
        resolvable_ids = [row[0] for row in rows if not row[1]]
        already_resolved = len(rows) - len(resolvable_ids)
        now = datetime.now(timezone.utc)
        if resolvable_ids:
            await self.db.execute(
                update(SystemErrorLog)
                .where(SystemErrorLog.id.in_(resolvable_ids))
                .values(
                    resolved=True,
                    resolved_by=resolved_by,
                    resolved_at=now,
                )
            )
        await self.db.flush()
        return {
            "resolved": len(resolvable_ids),
            "already_resolved": already_resolved,
            "matched": len(rows),
            "excluded": len(exclude_ids or []),
            "not_found": 0 if select_all else max(len(log_ids or []) - len(rows), 0),
        }

    # -- 内部辅助 --

    @staticmethod
    def _build_filters(
        *,
        category: Optional[str] = None,
        level: Optional[str] = None,
        source: Optional[str] = None,
        module: Optional[str] = None,
        task_type: Optional[str] = None,
        project_id: Optional[int] = None,
        user_id: Optional[int] = None,
        resolved: Optional[bool] = None,
        keyword: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        fingerprint: Optional[str] = None,
        fingerprints: Optional[list[str]] = None,
        incident_id: Optional[str] = None,
        traffic_class: Optional[str] = None,
    ) -> list:
        """构建公共筛选条件列表

 traffic_class: 不传 = 只看 production, "all" = 不过滤。历史数据标 unknown
 且默认不展示 -- 其来源不可回溯且绝大多数是测试噪声, 默认放行等于没治理。
 """
        filters = []
        if traffic_class is None and not settings.ERROR_LOG_TRAFFIC_ISOLATION_ENABLED:
            effective_traffic_class = None
        else:
            effective_traffic_class = resolve_query_traffic_class(traffic_class)
        if effective_traffic_class is not None:
            filters.append(SystemErrorLog.traffic_class == effective_traffic_class)
        if category:
            filters.append(SystemErrorLog.category == category)
        if level:
            filters.append(SystemErrorLog.level == level)
        if source:
            filters.append(SystemErrorLog.source == source)
        if module:
            filters.append(SystemErrorLog.module.ilike(f"%{module}%"))
        if task_type:
            filters.append(SystemErrorLog.task_type == task_type)
        if project_id is not None:
            filters.append(SystemErrorLog.project_id == project_id)
        if user_id is not None:
            filters.append(SystemErrorLog.user_id == user_id)
        if resolved is not None:
            filters.append(SystemErrorLog.resolved == resolved)
        if keyword:
            filters.append(SystemErrorLog.message.ilike(f"%{keyword}%"))
        if start_date:
            filters.append(SystemErrorLog.created_at >= start_date)
        if end_date:
            filters.append(SystemErrorLog.created_at <= end_date)
        if fingerprint:
            filters.append(SystemErrorLog.message_fingerprint == fingerprint)
        if fingerprints:
            filters.append(SystemErrorLog.message_fingerprint.in_(fingerprints))
        if incident_id:
            filters.append(SystemErrorLog.incident_id == incident_id)
        return filters

    async def _resolve_target_ids(
        self,
        *,
        log_ids: Optional[list[int]],
        select_all: bool,
        exclude_ids: Optional[list[int]],
        category: Optional[str],
        level: Optional[str],
        source: Optional[str],
        module: Optional[str],
        task_type: Optional[str],
        project_id: Optional[int],
        user_id: Optional[int],
        resolved: Optional[bool],
        keyword: Optional[str],
        start_date: Optional[datetime],
        end_date: Optional[datetime],
        fingerprints: Optional[list[str]] = None,
        traffic_class: Optional[str] = None,
    ) -> list[int]:
        """解析批量操作的目标 ID 列表

        必须与列表查询共用同一套筛选(含 traffic_class), 否则"按筛选结果全选"会作用到看不见的行。
        """
        use_filter_query = select_all or (fingerprints and not log_ids)
        if use_filter_query:
            query = select(SystemErrorLog.id)
            filters = self._build_filters(
                category=category,
                level=level,
                source=source,
                module=module,
                task_type=task_type,
                project_id=project_id,
                user_id=user_id,
                resolved=resolved,
                keyword=keyword,
                start_date=start_date,
                end_date=end_date,
                fingerprints=fingerprints,
                traffic_class=traffic_class,
            )
            for filter_item in filters:
                query = query.where(filter_item)
            if exclude_ids:
                query = query.where(~SystemErrorLog.id.in_(exclude_ids))
            result = await self.db.execute(query)
            return list(result.scalars().all())

        if not log_ids:
            return []
        result = await self.db.execute(
            select(SystemErrorLog.id).where(SystemErrorLog.id.in_(log_ids))
        )
        return list(result.scalars().all())

    # -- 抑制规则 CRUD --

    async def create_suppression_rule(
        self,
        source_log_id: int,
        *,
        created_by: str,
        reason: str = "",
    ) -> Optional[LogSuppressionRule]:
        """从源日志自动提取匹配条件并创建抑制规则"""
        result = await self.db.execute(
            select(SystemErrorLog).where(SystemErrorLog.id == source_log_id)
        )
        log = result.scalar_one_or_none()
        if log is None:
            return None

        message_prefix = (log.message or "")[:200]

        # 检查是否已存在相同规则
        existing = await self.db.execute(
            select(LogSuppressionRule.id).where(
                LogSuppressionRule.module == log.module,
                LogSuppressionRule.message_prefix == message_prefix,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return None  # 由路由层返回具体错误

        rule = LogSuppressionRule(
            module=log.module or "",
            function_name=log.function_name,
            message_prefix=message_prefix,
            level=log.level,
            category=log.category,
            enabled=True,
            reason=reason,
            created_by=created_by,
            source_log_id=source_log_id,
        )
        self.db.add(rule)
        await self.db.flush()
        return rule

    async def list_suppression_rules(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        enabled: Optional[bool] = None,
    ) -> dict[str, Any]:
        """分页列出抑制规则"""
        query = select(LogSuppressionRule)
        count_query = select(func.count(LogSuppressionRule.id))

        if enabled is not None:
            query = query.where(LogSuppressionRule.enabled == enabled)
            count_query = count_query.where(LogSuppressionRule.enabled == enabled)

        total_result = await self.db.execute(count_query)
        total: int = total_result.scalar() or 0

        offset = (page - 1) * page_size
        query = query.order_by(desc(LogSuppressionRule.created_at)).offset(offset).limit(page_size)

        result = await self.db.execute(query)
        rules = list(result.scalars().all())
        total_pages = (total + page_size - 1) // page_size

        return {
            "items": rules,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }

    async def toggle_suppression_rule(
        self,
        rule_id: int,
        *,
        enabled: bool,
    ) -> Optional[LogSuppressionRule]:
        """启用/禁用抑制规则"""
        result = await self.db.execute(
            select(LogSuppressionRule).where(LogSuppressionRule.id == rule_id)
        )
        rule = result.scalar_one_or_none()
        if rule is None:
            return None
        rule.enabled = enabled
        await self.db.flush()
        return rule

    async def delete_suppression_rule(self, rule_id: int) -> bool:
        """物理删除抑制规则"""
        result = await self.db.execute(
            select(LogSuppressionRule).where(LogSuppressionRule.id == rule_id)
        )
        rule = result.scalar_one_or_none()
        if rule is None:
            return False
        await self.db.delete(rule)
        await self.db.flush()
        return True

    async def recompute_fingerprints(self, batch_size: int = 500) -> dict[str, int]:
        """重新计算所有日志的 message_fingerprint, 用于归一化规则更新后的数据修正"""
        total_updated = 0
        offset = 0
        while True:
            result = await self.db.execute(
                select(SystemErrorLog.id, SystemErrorLog.module, SystemErrorLog.level, SystemErrorLog.message)
                .order_by(SystemErrorLog.id)
                .offset(offset)
                .limit(batch_size)
            )
            rows = result.all()
            if not rows:
                break
            for row in rows:
                new_fp = compute_fingerprint(row.module, row.level, row.message)
                await self.db.execute(
                    update(SystemErrorLog)
                    .where(SystemErrorLog.id == row.id)
                    .values(message_fingerprint=new_fp)
                )
            total_updated += len(rows)
            offset += batch_size
            await self.db.flush()
        return {"updated": total_updated}

    @staticmethod
    async def auto_recompute_if_needed(db: AsyncSession) -> None:
        """启动时自动检测指纹算法版本, 如有变化则重算全量指纹。

        使用 system_error_logs 表中首条记录的指纹与当前算法重算结果对比,
        如不一致则说明算法已更新, 需要重算。
        """
        result = await db.execute(
            select(
                SystemErrorLog.id,
                SystemErrorLog.module,
                SystemErrorLog.level,
                SystemErrorLog.message,
                SystemErrorLog.message_fingerprint,
            )
            .where(SystemErrorLog.message_fingerprint.isnot(None))
            .order_by(desc(SystemErrorLog.id))
            .limit(1)
        )
        sample = result.one_or_none()
        if sample is None:
            return

        current_fp = compute_fingerprint(sample.module, sample.level, sample.message)
        if current_fp == sample.message_fingerprint:
            _logger.info(
                "指纹算法版本未变化 (v%s), 跳过重算",
                FINGERPRINT_ALGORITHM_VERSION,
            )
            return

        _logger.info(
            "检测到指纹算法版本变化 (v%s), 开始自动重算...",
            FINGERPRINT_ALGORITHM_VERSION,
        )
        service = ErrorLogService(db)
        stats = await service.recompute_fingerprints()
        await db.commit()
        _logger.info(
            "指纹自动重算完成: 更新 %d 条记录",
            stats["updated"],
        )
