"""NodeDetailService（R2-14 拆分产物）

LLM Center 节点详情查询：节点完整信息 + 版本列表 + 参数定义 + 版本 diff。

公共方法：
- get_node_detail: 获取节点完整详情（含 active_prompt + all_versions + params）
- get_node_versions: 获取节点全部版本列表
- get_node_params: 获取节点参数定义
- diff_versions: 两版本 diff 对比（unified diff 文本）
"""

from __future__ import annotations

import difflib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_node import LlmNode
from app.models.llm_param_def import LlmParamDef
from app.models.llm_template_version import LlmTemplateVersion
from app.schemas.llm_center import (
    NodeDetail,
    ParamDefInfo,
    TemplateVersionInfo,
)


class NodeDetailService:
    """LLM Center 节点详情查询服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_node_detail(self, node_key: str) -> NodeDetail | None:
        """获取节点完整详情"""
        node = (await self.db.execute(
            select(LlmNode).where(LlmNode.node_key == node_key)
        )).scalar_one_or_none()
        if not node:
            return None

        versions = (await self.db.execute(
            select(LlmTemplateVersion)
            .where(LlmTemplateVersion.node_id == node.id)
            .order_by(LlmTemplateVersion.version.desc())
        )).scalars().all()

        params = (await self.db.execute(
            select(LlmParamDef)
            .where(LlmParamDef.node_id == node.id)
            .order_by(LlmParamDef.display_order)
        )).scalars().all()

        active_prompt = None
        all_versions = []
        for v in versions:
            vi = TemplateVersionInfo(
                id=v.id, template_role=v.template_role, version=v.version,
                content=v.content, is_active=v.is_active, source=v.source,
                status=v.status, change_note=v.change_note, created_by=v.created_by,
                validated_at=v.validated_at, published_at=v.published_at,
                created_at=v.created_at,
            )
            all_versions.append(vi)
            if v.is_active and v.template_role == "system" and active_prompt is None:
                active_prompt = vi

        param_infos = [ParamDefInfo(
            id=p.id, param_key=p.param_key, direction=p.direction,
            data_type=p.data_type, is_required=p.is_required,
            default_value=p.default_value, description=p.description,
            source_type=p.source_type, source_hint=p.source_hint,
            example_value=p.example_value, ui_hint=p.ui_hint, status=p.status,
        ) for p in params]

        return NodeDetail(
            node_key=node.node_key, name=node.name, description=node.description,
            builder_key=node.builder_key, resolver_key=node.resolver_key,
            source_file=node.source_file, execution_channel=node.execution_channel,
            call_method=node.call_method, default_temperature=node.default_temperature,
            default_max_tokens=node.default_max_tokens,
            output_schema_name=node.output_schema_name,
            visibility=node.visibility, editable_level=node.editable_level,
            supports_hot_reload=node.supports_hot_reload,
            active_prompt=active_prompt, all_versions=all_versions,
            params=param_infos,
        )

    async def diff_versions(self, node_key: str, id1: int, id2: int) -> dict:
        """两版本 diff 对比"""
        v1 = (await self.db.execute(
            select(LlmTemplateVersion).where(LlmTemplateVersion.id == id1)
        )).scalar_one_or_none()
        v2 = (await self.db.execute(
            select(LlmTemplateVersion).where(LlmTemplateVersion.id == id2)
        )).scalar_one_or_none()
        if not v1 or not v2:
            from app.core.exceptions import NotFoundError
            raise NotFoundError("版本不存在")

        lines1 = v1.content.splitlines(keepends=True)
        lines2 = v2.content.splitlines(keepends=True)
        diff = list(difflib.unified_diff(
            lines1, lines2,
            fromfile=f"v{v1.version} ({v1.template_role})",
            tofile=f"v{v2.version} ({v2.template_role})",
            lineterm="",
        ))
        return {
            "version_a": {"id": v1.id, "version": v1.version, "role": v1.template_role, "source": v1.source},
            "version_b": {"id": v2.id, "version": v2.version, "role": v2.template_role, "source": v2.source},
            "diff_text": "\n".join(diff),
            "has_changes": len(diff) > 0,
        }

    async def get_node_versions(self, node_key: str) -> list[TemplateVersionInfo]:
        """获取节点的全部版本列表"""
        node = (await self.db.execute(
            select(LlmNode).where(LlmNode.node_key == node_key)
        )).scalar_one_or_none()
        if not node:
            return []

        versions = (await self.db.execute(
            select(LlmTemplateVersion)
            .where(LlmTemplateVersion.node_id == node.id)
            .order_by(LlmTemplateVersion.version.desc())
        )).scalars().all()

        return [TemplateVersionInfo(
            id=v.id, template_role=v.template_role, version=v.version,
            content=v.content, is_active=v.is_active, source=v.source,
            status=v.status, change_note=v.change_note, created_by=v.created_by,
            validated_at=v.validated_at, published_at=v.published_at,
            created_at=v.created_at,
        ) for v in versions]

    async def get_node_params(self, node_key: str) -> list[ParamDefInfo]:
        """获取节点参数定义"""
        node = (await self.db.execute(
            select(LlmNode).where(LlmNode.node_key == node_key)
        )).scalar_one_or_none()
        if not node:
            return []

        params = (await self.db.execute(
            select(LlmParamDef)
            .where(LlmParamDef.node_id == node.id)
            .order_by(LlmParamDef.display_order)
        )).scalars().all()

        return [ParamDefInfo(
            id=p.id, param_key=p.param_key, direction=p.direction,
            data_type=p.data_type, is_required=p.is_required,
            default_value=p.default_value, description=p.description,
            source_type=p.source_type, source_hint=p.source_hint,
            example_value=p.example_value, ui_hint=p.ui_hint, status=p.status,
        ) for p in params]
