"""
职责: 项目运行模式枚举、可编辑阶段与回退 guided。
不做: 不读写数据库、不推进流水线、不校验权限。
"""

from __future__ import annotations

from enum import Enum

from app.models.project import Project, ProjectStatus


class ProjectRunMode(str, Enum):
    GUIDED = "guided"
    EXPRESS = "express"


RUN_MODE_EDITABLE_STATUSES = frozenset({
    ProjectStatus.CREATED,
    ProjectStatus.UPLOADING,
    ProjectStatus.PARSING,
})


def is_express_run_mode(value: str | None) -> bool:
    return value == ProjectRunMode.EXPRESS.value


def revert_run_mode_to_guided(project: Project) -> None:
    """推进决策失败时回退人工确认，不改项目业务状态。"""
    project.run_mode = ProjectRunMode.GUIDED.value
