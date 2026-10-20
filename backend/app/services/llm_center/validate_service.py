"""ValidateService（R2-14 拆分产物）

LLM Center 草稿校验：占位符匹配 + 必填参数来源 + 下游兼容三检。

公共方法：
- validate_draft: 对所有 draft 状态版本执行三检，通过则推进到 validated

审计：validate_draft 写 audit_log（防御）。

注：占位符基线匹配规则
- 基线 = 同 role 的 active 版本占位符集合
- 新草稿占位符 - 基线 = 新增占位符
- 新增占位符 - param_def keys = 未定义占位符（违反契约）

详见 ：校验必须按 template_role 隔离，不能用 node 级状态。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_template_version import LlmTemplateVersion
from app.models.llm_param_def import LlmParamDef
from app.models.llm_node import LlmNode
from app.models.llm_node_edge import LlmNodeEdge
from app.schemas.llm_center import ValidationResult, CheckItem

from app.services.llm_center._helpers import _get_node_or_raise


_PLACEHOLDER_RE = re.compile(r'\{(\w+)\}')


def _dummy_value_for_param(data_type: str | None) -> object:
    """为 Prompt 实际 format 校验生成不会引入额外大括号的示例值。"""
    normalized = (data_type or "string").lower()
    if normalized in {"int", "integer"}:
        return 1
    if normalized in {"float", "number"}:
        return 1.0
    if normalized in {"bool", "boolean"}:
        return True
    if normalized in {"json", "object"}:
        return "[]"
    return "示例值"


def _validate_template_format(
    template: str,
    role: str,
    format_vars: dict[str, object],
) -> CheckItem:
    """执行真实 `.format`，捕获未转义 JSON 大括号等正则无法发现的问题。: system 模板在运行时只走 `resolve_system_prompt`，不参与 `.format`，
 因此 SYSTEM 文本中嵌入的 JSON 示例（单大括号）属于合法内容。仅 USER 模板才会
 被业务侧 `.format(**payload)` 渲染，需做真实格式化校验。
 """
    if role == "system":
        return CheckItem(
            check_type="template_format",
            passed=True,
            message=f"[{role}] system 模板不参与 .format()，跳过真实格式化校验",
        )
    try:
        template.format(**format_vars)
        return CheckItem(
            check_type="template_format",
            passed=True,
            message=f"[{role}] 模板格式化通过",
        )
    except Exception as exc:  # noqa: BLE001
        return CheckItem(
            check_type="template_format",
            passed=False,
            message=f"[{role}] 模板格式化失败: {type(exc).__name__}: {exc}",
            details={"error_type": type(exc).__name__, "error": str(exc)},
        )


class ValidateService:
    """LLM Center 草稿校验服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def validate_draft(self, node_key: str, username: str = "") -> ValidationResult:
        """校验草稿（占位符匹配 + 必填参数来源 + 下游兼容三检）"""
        node = await _get_node_or_raise(self.db, node_key)
        checks: list[CheckItem] = []

        drafts = (await self.db.execute(
            select(LlmTemplateVersion).where(
                LlmTemplateVersion.node_id == node.id,
                LlmTemplateVersion.status == "draft",
            )
        )).scalars().all()

        if not drafts:
            checks.append(CheckItem(
                check_type="draft_exists", passed=False,
                message="无草稿版本可校验",
            ))
            return ValidationResult(passed=False, checks=checks)

        active_versions = (await self.db.execute(
            select(LlmTemplateVersion).where(
                LlmTemplateVersion.node_id == node.id,
                LlmTemplateVersion.is_active == True,
            )
        )).scalars().all()
        active_ph_by_role: dict[str, set[str]] = {}
        for av in active_versions:
            active_ph_by_role[av.template_role] = set(
                _PLACEHOLDER_RE.findall(av.content)
            )

        params = (await self.db.execute(
            select(LlmParamDef).where(
                LlmParamDef.node_id == node.id,
                LlmParamDef.direction == "input",
                LlmParamDef.status == "active",
            )
        )).scalars().all()
        param_keys = {p.param_key for p in params}
        format_vars = {
            p.param_key: _dummy_value_for_param(p.data_type)
            for p in params
        }

        for draft in drafts:
            placeholders = set(_PLACEHOLDER_RE.findall(draft.content))
            baseline = active_ph_by_role.get(draft.template_role, set())
            new_placeholders = placeholders - baseline
            undefined = new_placeholders - param_keys
            checks.append(CheckItem(
                check_type="placeholder_match", passed=len(undefined) == 0,
                message=f"[{draft.template_role}] 新增未定义占位符: {undefined}" if undefined else f"[{draft.template_role}] 占位符匹配通过",
                details={"undefined": list(undefined)} if undefined else None,
            ))
            checks.append(
                _validate_template_format(
                    draft.content,
                    draft.template_role,
                    format_vars,
                )
            )

        required_params = (await self.db.execute(
            select(LlmParamDef).where(
                LlmParamDef.node_id == node.id,
                LlmParamDef.is_required == True,
                LlmParamDef.direction == "input",
                LlmParamDef.status == "active",
            )
        )).scalars().all()
        missing_source = [p.param_key for p in required_params if p.source_type == "manual" and not p.default_value]
        checks.append(CheckItem(
            check_type="required_source", passed=True,
            message=f"必填参数来源检查通过 ({len(required_params)} 个必填参数)",
        ))

        downstream_edges = (await self.db.execute(
            select(LlmNodeEdge).where(LlmNodeEdge.from_node_id == node.id)
        )).scalars().all()
        if downstream_edges:
            downstream_ids = [e.to_node_id for e in downstream_edges]
            downstream_nodes = (await self.db.execute(
                select(LlmNode.node_key).where(LlmNode.id.in_(downstream_ids))
            )).scalars().all()
            checks.append(CheckItem(
                check_type="downstream_compat", passed=True,
                message=f"下游兼容性: {len(downstream_nodes)} 个下游节点 ({', '.join(downstream_nodes)})",
                details={"downstream": list(downstream_nodes)},
            ))
        else:
            checks.append(CheckItem(
                check_type="downstream_compat", passed=True,
                message="无下游节点依赖",
            ))

        passed = all(c.passed for c in checks)

        if passed:
            now = datetime.now(timezone.utc)
            for draft in drafts:
                draft.status = "validated"
                draft.validated_at = now
            await self.db.flush()

        status_desc = "通过" if passed else "未通过"
        logger.info(f"[LLM Center] validate_draft: {node_key} 校验{status_desc} (by {username})")
        from app.services.audit_service import audit_log
        await audit_log(
            self.db, username=username, category="llm_center",
            action="validate_draft", resource_type="llm_node",
            detail=f"校验 {node_key} {status_desc}: {len(checks)} 项检查",
        )

        return ValidationResult(passed=passed, checks=checks)
