"""标书编写阶段 5：渲染与输出（public 接口）

只承载 3 个 public 方法（render_docx / get_html_preview / get_rendered_docx）。

接口约定（Task 5 Step 7）：
- ``project_resolver`` 注入：lambda 包装 ``_get_project_or_raise``
- ``ensure_preview_ready`` 注入：lambda 包装 ``_ensure_preview_ready``
- ``ensure_render_artifact`` 注入：lambda 包装 ``_ensure_render_artifact``

注意：7 个 render-artifact 私有方法 + ``_render_locks`` ClassVar +
``_ensure_preview_ready`` staticmethod 全部保留在 ``WritingService`` 主类，
因为 ``test_writer_render_artifact_cache`` 期望它们之间通过 ``self.method`` 互调
时能正确响应 ``service._xxx = AsyncMock(...)`` 的实例 monkey patch。
sub-service 内部 ``self`` 不同实例，无法做到这一点。
"""

from __future__ import annotations

import json
from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ServiceError
from app.models.project import Project
from app.models.user import User
from app.models.writing_render_artifact import WritingRenderArtifact


class RenderService:
    """渲染阶段子服务（薄层封装 3 个 public 入口）"""

    def __init__(
        self,
        db: AsyncSession,
        *,
        project_resolver: Callable[..., Awaitable[Project]],
        ensure_preview_ready: Callable[[Project], None],
        ensure_render_artifact: Callable[..., Awaitable[WritingRenderArtifact]],
    ) -> None:
        self.db = db
        self._resolve_project = project_resolver
        self._ensure_preview_ready = ensure_preview_ready
        self._ensure_render_artifact = ensure_render_artifact

    async def render_docx(
        self, project_id: int, user: User,
    ) -> bytes:
        """显式重新渲染 Word 文档并刷新当前渲染快照"""
        project = await self._resolve_project(project_id, user)
        self._ensure_preview_ready(project)
        artifact = await self._ensure_render_artifact(
            project_id,
            force_rebuild=True,
            explicit_render=True,
        )
        if not artifact.docx_blob:
            raise ServiceError("渲染结果为空")
        return bytes(artifact.docx_blob)

    async def get_html_preview(
        self, project_id: int, user: User,
    ) -> str:
        """HTML 预览（复用当前渲染快照）"""
        project = await self._resolve_project(project_id, user)
        self._ensure_preview_ready(project)
        artifact = await self._ensure_render_artifact(project_id)
        if not artifact.preview_html:
            raise ServiceError("预览结果为空")
        return artifact.preview_html

    async def get_rendered_docx(
        self, project_id: int, user: User, *, allow_blocking_issues: bool = False,
    ) -> bytes:
        """下载当前渲染快照对应的 Word 文档"""
        project = await self._resolve_project(project_id, user)
        self._ensure_preview_ready(project)
        artifact = await self._ensure_render_artifact(project_id)
        if not artifact.docx_blob:
            raise ServiceError("Word文档尚未生成")
        if not allow_blocking_issues:
            self._ensure_export_has_no_blocking_issues(artifact)
        return bytes(artifact.docx_blob)

    @staticmethod
    def _load_consistency_issues(artifact: WritingRenderArtifact) -> list[dict]:
        if (
            not getattr(artifact, "consistency_issues_json", None)
            or getattr(artifact, "consistency_render_version", None)
            != getattr(artifact, "render_version", None)
        ):
            return []
        try:
            raw = json.loads(str(artifact.consistency_issues_json))
        except json.JSONDecodeError:
            return []
        return raw if isinstance(raw, list) else []

    @classmethod
    def _ensure_export_has_no_blocking_issues(
        cls,
        artifact: WritingRenderArtifact,
    ) -> None:
        issues = cls._load_consistency_issues(artifact)
        blocking = [
            issue for issue in issues
            if isinstance(issue, dict)
            and issue.get("severity") == "high"
            and str(issue.get("type") or "").startswith("fixed_template_")
        ]
        if not blocking:
            return
        first = str(blocking[0].get("message") or "固定模板存在高风险问题")
        raise ServiceError(
            f"固定模板存在 {len(blocking)} 项高风险问题，请修复后再导出：{first}",
            status_code=400,
        )


__all__ = ["RenderService"]
