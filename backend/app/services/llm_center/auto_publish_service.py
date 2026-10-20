"""LLM Center 部署后自动发布服务。

职责: 将部署后的代码注册源变更纳入现有同步、校验、发布闭环。
不做: 不实现 Prompt 校验细节、不绕过 VersionService 发布逻辑、不处理前端交互。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession


_DEPLOY_CHANGE_NOTE = "部署后自动同步校验并发布代码注册源"


class AutoPublishCodeRegistryError(RuntimeError):
    """部署后自动发布失败。"""


class _BatchPublishService(Protocol):
    """自动发布所需的 LLM Center 批量发布接口。"""

    async def batch_validate_and_publish(
        self,
        scope: str,
        flow_key: str | None,
        include_technical: bool,
        username: str,
        change_note: str | None = None,
    ) -> dict:
        """同步、校验并发布待处理节点。"""


def _failed_items_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    """提取部署脚本需要展示的失败项。"""
    failed_statuses = {
        "validation_failed",
        "publish_failed",
    }
    failed: list[dict[str, Any]] = []
    for item in result.get("items", []):
        status = item.get("status")
        if status not in failed_statuses:
            continue
        failed.append({
            "node_key": item.get("node_key"),
            "status": status,
            "error_message": item.get("error_message"),
        })
    return failed


def _is_auto_publish_success(result: dict[str, Any], failed_items: list[dict[str, Any]]) -> bool:
    """判断批量结果是否可视为部署发布成功。"""
    summary = result.get("summary", {})
    total = int(summary.get("total") or 0)
    validated_failed = int(summary.get("validated_failed") or 0)
    published = int(summary.get("published") or 0)
    if failed_items or validated_failed > 0:
        return False
    if total == 0:
        return True
    return published == total


async def auto_publish_code_registry(
    db: AsyncSession,
    username: str = "deploy:auto",
    service_factory: Callable[[AsyncSession], _BatchPublishService] | None = None,
) -> dict[str, Any]:
    """部署后自动同步、校验并发布全部待处理代码注册源节点。"""
    if service_factory is None:
        from app.services.llm_center_service import LlmCenterService

        service_factory = LlmCenterService

    service = service_factory(db)
    result = await service.batch_validate_and_publish(
        scope="all_pending",
        flow_key=None,
        include_technical=True,
        username=username,
        change_note=_DEPLOY_CHANGE_NOTE,
    )
    failed_items = _failed_items_from_result(result)
    return {
        "ok": _is_auto_publish_success(result, failed_items),
        "summary": result.get("summary", {}),
        "failed_items": failed_items,
        "raw_result": result,
    }


def assert_auto_publish_success(report: dict[str, Any]) -> None:
    """部署脚本使用的失败判定，失败时抛出可读错误。"""
    if report.get("ok") is True:
        return
    failed_items = report.get("failed_items", [])
    summary = report.get("summary", {})
    failed_desc = "; ".join(
        f"{item.get('node_key')}[{item.get('status')}]: {item.get('error_message') or ''}".strip()
        for item in failed_items
    )
    raise AutoPublishCodeRegistryError(
        f"LLM Registry 自动发布失败: summary={summary}, failed_items={failed_desc or '无明细'}"
    )
