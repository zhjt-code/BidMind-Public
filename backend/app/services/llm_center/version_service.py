"""VersionService（R2-14 拆分产物）

LLM Center 版本发布/回滚/节点配置/影响分析 + 参数 CRUD。

公共方法：
- publish: 把 validated 草稿设为 active（按 template_role 隔离， 防御）
- rollback: 回滚到上一个 published 版本
- update_node_config: 更新节点运行时配置（temperature/max_tokens/node_class）
- impact_analysis: 变更影响分析（下游节点 + 风险等级）
- create_param: 新增参数定义
- update_param: 更新参数定义

审计：publish_prompt / rollback_prompt 写 audit_log（防御）。
跨进程一致性：publish / rollback / update_node_config 三处必须调
`_broadcast_invalidate(node_key)` 通知其它 worker reload prompt（防御）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_template_version import LlmTemplateVersion
from app.models.llm_param_def import LlmParamDef
from app.models.llm_node import LlmNode
from app.models.llm_node_edge import LlmNodeEdge
from app.models.llm_publish_record import LlmPublishRecord
from app.schemas.llm_center import (
    TemplateVersionInfo, ParamDefInfo,
    NodeConfigUpdate, ImpactAnalysisResponse,
    ParamDefCreate, ParamDefUpdate,
)

from app.services.llm_center._helpers import _broadcast_invalidate, _get_node_or_raise


class VersionService:
    """LLM Center 版本发布/回滚/配置/影响分析服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def publish(self, node_key: str, username: str, change_note: str | None = None) -> TemplateVersionInfo:
        """发布（将 validated 草稿设为 active，按 template_role 隔离）"""
        node = await _get_node_or_raise(self.db, node_key)

        validated = (await self.db.execute(
            select(LlmTemplateVersion).where(
                LlmTemplateVersion.node_id == node.id,
                LlmTemplateVersion.status == "validated",
            )
        )).scalars().all()

        if not validated:
            from app.core.exceptions import ServiceError
            raise ServiceError("无已校验的草稿可发布，请先执行校验")

        publish_targets_by_role: dict[str, LlmTemplateVersion] = {}
        stale_validated: list[LlmTemplateVersion] = []
        for v in validated:
            current = publish_targets_by_role.get(v.template_role)
            if current is None or (v.version, v.id) > (current.version, current.id):
                if current is not None:
                    stale_validated.append(current)
                publish_targets_by_role[v.template_role] = v
            else:
                stale_validated.append(v)

        for stale in stale_validated:
            stale.is_active = False
            stale.status = "archived"

        publish_targets = list(publish_targets_by_role.values())
        roles_to_publish = set(publish_targets_by_role)

        old_active = (await self.db.execute(
            select(LlmTemplateVersion).where(
                LlmTemplateVersion.node_id == node.id,
                LlmTemplateVersion.is_active == True,
                LlmTemplateVersion.template_role.in_(roles_to_publish),
            )
        )).scalars().all()
        old_active_map: dict[str, LlmTemplateVersion] = {}
        for old in old_active:
            prev = old_active_map.get(old.template_role)
            if prev is None or (old.version, old.id) > (prev.version, prev.id):
                old_active_map[old.template_role] = old

        # 降级保护: 目标版本不高于同 role 当前激活版本时视为陈旧草稿,
        # 归档而非发布, 防止把 Prompt 内容回退到旧版本。
        stale_downgrades: list[LlmTemplateVersion] = []
        for role, target in list(publish_targets_by_role.items()):
            active = old_active_map.get(role)
            if active is not None and target.version <= active.version:
                stale_downgrades.append(target)
                del publish_targets_by_role[role]
        for stale in stale_downgrades:
            stale.is_active = False
            stale.status = "archived"
        publish_targets = list(publish_targets_by_role.values())
        if not publish_targets:
            # 归档结果必须落库: 后续 ServiceError 会触发请求级 rollback,
            # 不提交则陈旧草稿永远滞留 pending 列表。
            await self.db.commit()
            from app.core.exceptions import ServiceError
            stale_desc = "/".join(
                f"{v.template_role}:v{v.version}" for v in stale_downgrades
            )
            raise ServiceError(
                f"待发布草稿 [{stale_desc}] 版本不高于当前激活版本，"
                "已自动归档，无需发布"
            )

        for old in old_active:
            if old.template_role in publish_targets_by_role:
                old.is_active = False
                old.status = "archived"
        # 取消激活必须先于激活落库, 否则激活目标主键小于旧激活行时
        # SQLAlchemy 按主键顺序刷写 UPDATE 会瞬时违反 uq_llm_tv_active_node_role。
        await self.db.flush()

        now = datetime.now(timezone.utc)
        for v in publish_targets:
            v.is_active = True
            v.status = "published"
            v.published_at = now

            prev = old_active_map.get(v.template_role)
            self.db.add(LlmPublishRecord(
                node_id=node.id, template_version_id=v.id,
                action="publish", published_by=username,
                previous_version_id=prev.id if prev else None,
                change_note=change_note or v.change_note,
            ))

        await self.db.flush()

        from app.core.ai.prompt_registry import get_registry
        await get_registry().refresh(self.db, node_key)
        await _broadcast_invalidate(node_key)

        from app.services.audit_service import audit_log
        role_desc = "/".join(
            f"{v.template_role}:v{v.version}"
            for v in sorted(publish_targets, key=lambda item: item.template_role)
        )
        logger.info(f"[LLM Center] publish: {node_key} [{role_desc}] (by {username})")
        await audit_log(
            self.db, username=username, category="llm_center",
            action="publish_prompt", resource_type="llm_node",
            detail=f"发布 {node_key} [{role_desc}]",
        )

        first = sorted(publish_targets, key=lambda item: item.template_role)[0]
        return TemplateVersionInfo(
            id=first.id, template_role=first.template_role,
            version=first.version, content=first.content,
            is_active=True, source=first.source, status="published",
            change_note=first.change_note, created_by=first.created_by,
            validated_at=first.validated_at, published_at=now,
            created_at=first.created_at,
        )

    async def rollback(self, node_key: str, username: str) -> TemplateVersionInfo:
        """回滚到上一个 published 版本"""
        node = await _get_node_or_raise(self.db, node_key)

        published = (await self.db.execute(
            select(LlmTemplateVersion).where(
                LlmTemplateVersion.node_id == node.id,
                LlmTemplateVersion.status.in_(["published", "archived"]),
            ).order_by(LlmTemplateVersion.version.desc())
        )).scalars().all()

        current_active = [v for v in published if v.is_active]
        candidates = [v for v in published if not v.is_active and v.status == "archived"]

        if not candidates:
            from app.core.exceptions import ServiceError
            raise ServiceError("无可回滚的历史版本")

        for ca in current_active:
            ca.is_active = False
            ca.status = "archived"
        # 回滚目标主键必然小于当前激活行, 必须先 flush 取消激活,
        # 否则激活 UPDATE 先执行会瞬时违反 uq_llm_tv_active_node_role。
        await self.db.flush()

        rollback_targets: dict[str, LlmTemplateVersion] = {}
        for c in candidates:
            if c.template_role not in rollback_targets:
                rollback_targets[c.template_role] = c

        now = datetime.now(timezone.utc)
        for v in rollback_targets.values():
            v.is_active = True
            v.status = "published"
            v.published_at = now
            self.db.add(LlmPublishRecord(
                node_id=node.id, template_version_id=v.id,
                action="rollback", published_by=username,
                previous_version_id=current_active[0].id if current_active else None,
            ))

        await self.db.flush()

        from app.core.ai.prompt_registry import get_registry
        await get_registry().refresh(self.db, node_key)
        await _broadcast_invalidate(node_key)

        from app.services.audit_service import audit_log
        first = next(iter(rollback_targets.values()))
        logger.info(f"[LLM Center] rollback: {node_key} 到 v{first.version} (by {username})")
        await audit_log(
            self.db, username=username, category="llm_center",
            action="rollback_prompt", resource_type="llm_node",
            detail=f"回滚 {node_key} 到 v{first.version}",
        )

        return TemplateVersionInfo(
            id=first.id, template_role=first.template_role,
            version=first.version, content=first.content,
            is_active=True, source=first.source, status="published",
            change_note=first.change_note, created_by=first.created_by,
            validated_at=first.validated_at, published_at=now,
            created_at=first.created_at,
        )

    async def update_node_config(self, node_key: str, data: NodeConfigUpdate) -> dict:
        """更新节点运行时配置（temperature/max_tokens/node_class）"""
        node = await _get_node_or_raise(self.db, node_key)
        updated = {}
        if data.default_temperature is not None:
            node.default_temperature = data.default_temperature
            updated["default_temperature"] = data.default_temperature
        if data.default_max_tokens is not None:
            node.default_max_tokens = data.default_max_tokens
            updated["default_max_tokens"] = data.default_max_tokens
        if data.node_class is not None:
            node.node_class = data.node_class
            updated["node_class"] = data.node_class
        await self.db.flush()

        from app.core.ai.prompt_registry import get_registry
        await get_registry().refresh(self.db, node_key)
        await _broadcast_invalidate(node_key)

        return updated

    async def impact_analysis(self, node_key: str) -> ImpactAnalysisResponse:
        """变更影响分析"""
        node = await _get_node_or_raise(self.db, node_key)
        downstream = (await self.db.execute(
            select(LlmNodeEdge).where(LlmNodeEdge.from_node_id == node.id)
        )).scalars().all()
        downstream_keys = []
        for edge in downstream:
            dn = (await self.db.execute(
                select(LlmNode.node_key).where(LlmNode.id == edge.to_node_id)
            )).scalar_one_or_none()
            if dn:
                downstream_keys.append(dn)

        risk = "safe" if not downstream_keys else "warning"
        rec = "无下游依赖，可安全发布" if not downstream_keys else f"影响 {len(downstream_keys)} 个下游节点，建议验证后发布"
        return ImpactAnalysisResponse(
            affected_downstream_nodes=downstream_keys,
            running_projects_count=0,
            risk_level=risk,
            recommendation=rec,
        )

    async def create_param(self, node_key: str, data: ParamDefCreate) -> ParamDefInfo:
        """新增参数定义"""
        node = await _get_node_or_raise(self.db, node_key)
        param = LlmParamDef(
            node_id=node.id, param_key=data.param_key, direction=data.direction,
            data_type=data.data_type, is_required=data.is_required,
            default_value=data.default_value, description=data.description,
            source_type=data.source_type, source_hint=data.source_hint,
            example_value=data.example_value, ui_hint=data.ui_hint,
        )
        self.db.add(param)
        await self.db.flush()
        return ParamDefInfo(
            id=param.id, param_key=param.param_key, direction=param.direction,
            data_type=param.data_type, is_required=param.is_required,
            default_value=param.default_value, description=param.description,
            source_type=param.source_type, source_hint=param.source_hint,
            example_value=param.example_value, ui_hint=param.ui_hint,
            status=param.status,
        )

    async def update_param(self, node_key: str, param_key: str, data: ParamDefUpdate) -> ParamDefInfo:
        """更新参数定义"""
        node = await _get_node_or_raise(self.db, node_key)
        param = (await self.db.execute(
            select(LlmParamDef).where(
                LlmParamDef.node_id == node.id,
                LlmParamDef.param_key == param_key,
            )
        )).scalar_one_or_none()
        if not param:
            from app.core.exceptions import NotFoundError
            raise NotFoundError(f"参数 '{param_key}' 不存在")
        for field_name in ('description', 'default_value', 'source_type', 'source_hint', 'example_value', 'ui_hint', 'status'):
            val = getattr(data, field_name, None)
            if val is not None:
                setattr(param, field_name, val)
        await self.db.flush()
        return ParamDefInfo(
            id=param.id, param_key=param.param_key, direction=param.direction,
            data_type=param.data_type, is_required=param.is_required,
            default_value=param.default_value, description=param.description,
            source_type=param.source_type, source_hint=param.source_hint,
            example_value=param.example_value, ui_hint=param.ui_hint,
            status=param.status,
        )
