"""项目级知识库使用配置访问层。

封装 `Project.knowledge_usage_config` 的读写与门控判定，避免审查/串标调用链
分散读取 JSON 字段并各自重复校验。

核心约束:
- 默认未启用：调用方 `is_active_for_flow()` 返回 False 时严禁向 Prompt 注入知识库。
- 必须用户显式 confirm：仅 `enabled && confirmed_by_user && flow 匹配` 才认为生效。
- 仅返回所选文档 IDs：检索层据此构造 `allowed_document_ids` 限定查询范围。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project


_VALID_FLOWS: frozenset[str] = frozenset({"bid_review", "collusion_check", "bid_writing"})
_DEFAULT_POLICIES: dict[str, str] = {
    "bid_review": "auxiliary_only",
    "collusion_check": "normal_similarity_exemption_only",
    "bid_writing": "auxiliary_only",
}


def _coerce_ids(value: Any) -> list[int]:
    if not value:
        return []
    if not isinstance(value, Iterable):
        return []
    result: list[int] = []
    seen: set[int] = set()
    for item in value:
        try:
            int_value = int(item)
        except (TypeError, ValueError):
            continue
        if int_value in seen:
            continue
        seen.add(int_value)
        result.append(int_value)
    return result


@dataclass(frozen=True)
class ProjectKnowledgeUsage:
    """项目级知识库使用配置只读视图。"""

    enabled: bool = False
    flow: Optional[str] = None
    selected_kb_document_ids: list[int] = field(default_factory=list)
    usage_policy: Optional[str] = None
    confirmed_by_user: bool = False
    confirmed_at: Optional[datetime] = None

    def is_active_for_flow(self, flow: str) -> bool:
        """判定是否对指定 flow 生效，不生效则禁止注入 Prompt。"""
        if not self.enabled or not self.confirmed_by_user:
            return False
        if not self.flow or self.flow != flow:
            return False
        return bool(self.selected_kb_document_ids)

    def to_summary(self) -> dict[str, Any]:
        """生成 LLM 调用前后可记录的使用摘要，用于结果展示与审计。"""
        return {
            "enabled": bool(self.enabled),
            "flow": self.flow,
            "usage_policy": self.usage_policy,
            "selected_kb_document_ids": list(self.selected_kb_document_ids),
            "confirmed_by_user": bool(self.confirmed_by_user),
            "confirmed_at": (
                self.confirmed_at.isoformat()
                if isinstance(self.confirmed_at, datetime)
                else self.confirmed_at
            ),
        }


def parse_usage_config(raw: Any) -> ProjectKnowledgeUsage:
    """把 Project.knowledge_usage_config 的原始 JSON 解析为只读视图。"""
    if not isinstance(raw, dict):
        return ProjectKnowledgeUsage()
    flow = raw.get("flow")
    if flow not in _VALID_FLOWS:
        flow = None
    confirmed_at_raw = raw.get("confirmed_at")
    confirmed_at: Optional[datetime] = None
    if isinstance(confirmed_at_raw, datetime):
        confirmed_at = confirmed_at_raw
    elif isinstance(confirmed_at_raw, str) and confirmed_at_raw:
        try:
            confirmed_at = datetime.fromisoformat(confirmed_at_raw.replace("Z", "+00:00"))
        except ValueError:
            confirmed_at = None
    return ProjectKnowledgeUsage(
        enabled=bool(raw.get("enabled", False)),
        flow=flow,
        selected_kb_document_ids=_coerce_ids(raw.get("selected_kb_document_ids")),
        usage_policy=str(raw.get("usage_policy") or _DEFAULT_POLICIES.get(flow or "", "")) or None,
        confirmed_by_user=bool(raw.get("confirmed_by_user", False)),
        confirmed_at=confirmed_at,
    )


async def load_usage_for_project(
    session: AsyncSession,
    project_id: int,
) -> ProjectKnowledgeUsage:
    """按项目 ID 加载知识库使用配置；项目不存在时返回未启用视图。"""
    project = await session.get(Project, project_id)
    if project is None:
        return ProjectKnowledgeUsage()
    return parse_usage_config(project.knowledge_usage_config)


def build_usage_payload(
    *,
    enabled: bool,
    flow: str,
    selected_kb_document_ids: list[int],
    usage_policy: Optional[str] = None,
) -> dict[str, Any]:
    """构造写入 `Project.knowledge_usage_config` 的标准 payload。

    - 关闭状态忽略其他字段，避免“关闭但仍带 selected_ids”造成误读。
    - 默认按 flow 设置 usage_policy，确保调用链可识别边界。
    """
    flow_value = flow if flow in _VALID_FLOWS else None
    if not enabled or flow_value is None:
        return {
            "enabled": False,
            "flow": flow_value,
            "selected_kb_document_ids": [],
            "usage_policy": None,
            "confirmed_by_user": False,
            "confirmed_at": None,
        }
    deduped_ids = _coerce_ids(selected_kb_document_ids)
    if not deduped_ids:
        return {
            "enabled": False,
            "flow": flow_value,
            "selected_kb_document_ids": [],
            "usage_policy": usage_policy or _DEFAULT_POLICIES.get(flow_value, ""),
            "confirmed_by_user": False,
            "confirmed_at": None,
        }
    policy = usage_policy or _DEFAULT_POLICIES.get(flow_value, "auxiliary_only")
    return {
        "enabled": True,
        "flow": flow_value,
        "selected_kb_document_ids": deduped_ids,
        "usage_policy": policy,
        "confirmed_by_user": True,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
    }
