"""项目控制台响应契约。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ProjectDashboardSummary(BaseModel):
    """控制台核心统计。"""

    total: int = Field(default=0, description="项目总数")
    pending: int = Field(default=0, description="待处理项目数")
    processing: int = Field(default=0, description="处理中项目数")
    completed: int = Field(default=0, description="已完成项目数")
    failed: int = Field(default=0, description="失败项目数")
    this_month: int = Field(default=0, description="本月新增项目数")


class ProjectDashboardBucket(BaseModel):
    """控制台通用分布桶。"""

    key: str = Field(..., description="稳定枚举键")
    label: str = Field(..., description="中文展示名")
    count: int = Field(default=0, description="数量")


class ProjectTypeStatusRow(BaseModel):
    """项目类型与状态交叉统计行。"""

    project_type: str = Field(..., description="项目类型")
    label: str = Field(..., description="项目类型中文名")
    pending: int = Field(default=0, description="待处理数量")
    processing: int = Field(default=0, description="处理中数量")
    completed: int = Field(default=0, description="已完成数量")
    failed: int = Field(default=0, description="失败数量")


class ProjectDailyCount(BaseModel):
    """按日期统计的新增项目数量。"""

    date: str = Field(..., description="UTC 日期, YYYY-MM-DD")
    count: int = Field(default=0, description="当日新增数量")


class ProjectDashboardRecentProject(BaseModel):
    """控制台最近更新项目摘要。"""

    id: int
    name: str
    project_type: str
    status: str
    created_at: datetime
    updated_at: datetime


class ProjectTypeCompletionDuration(BaseModel):
    """某一业务下，单个已完成项目的任务总耗时平均值。"""

    project_type: str
    label: str
    avg_duration_ms: float | None = Field(
        default=None,
        description="最终结果落入健康窗口的已完成项目，创建到该产出之间成功任务耗时的平均毫秒；不含失败、取消、中断、空档和产出之后的再次成功",
    )
    sample_count: int = Field(default=0, description="纳入平均的已完成项目数")


class ProjectDashboardHealth(BaseModel):
    """运行健康。办结成功率只统计已上传文件的完成与失败；净增按 updated_at。平均耗时按最终结果产出时间。未办结个数是当前存量。"""

    success_rate: float | None = Field(
        default=None,
        description="健康窗口内、已上传过文件的办结成功率：已完成 /（已完成 + 失败）；从未上传的不计入，分母为0则为空",
    )
    completion_by_type: list[ProjectTypeCompletionDuration] = Field(default_factory=list)
    backlog: int = Field(default=0, description="当前未办结项目数，单位为个，口径为待处理+处理中，不随窗口变化")
    net_change: int = Field(default=0, description="健康窗口内新建数减办结数，单位为个")


class ProjectDashboardActionFilter(BaseModel):
    """行动项下钻到项目列表的筛选描述。"""

    status: str | None = Field(default=None, description="状态组")
    stages: list[str] = Field(default_factory=list, description="细粒度状态列表")
    stale_hours: int | None = Field(default=None, description="未更新小时阈值")


class ProjectDashboardActionItem(BaseModel):
    """需要人立即处理的行动项。"""

    id: str
    label: str
    count: int = 0
    tone: Literal["red", "orange", "amber", "cyan"]
    action_label: str
    filter: ProjectDashboardActionFilter


class ProjectDashboardStageGroup(BaseModel):
    """一级办理阶段，成员状态供下钻，不按业务域拆路径。"""

    key: str
    label: str
    count: int = 0
    member_statuses: list[str] = Field(default_factory=list)


class ProjectDashboardResponse(BaseModel):
    """项目控制台聚合数据。"""

    scope: Literal["mine", "all"] = Field(..., description="统计范围")
    generated_at: datetime = Field(..., description="生成时间")
    window_days: int = Field(..., description="新建与完成趋势窗口天数")
    health_window_days: int = Field(..., description="运行健康窗口天数")
    summary: ProjectDashboardSummary
    type_distribution: list[ProjectDashboardBucket]
    status_distribution: list[ProjectDashboardBucket]
    stage_distribution: list[ProjectDashboardBucket]
    type_status_matrix: list[ProjectTypeStatusRow]
    daily_created: list[ProjectDailyCount]
    daily_completed: list[ProjectDailyCount]
    recent_projects: list[ProjectDashboardRecentProject]
    health: ProjectDashboardHealth
    action_queue: list[ProjectDashboardActionItem]
    stage_groups: list[ProjectDashboardStageGroup]
