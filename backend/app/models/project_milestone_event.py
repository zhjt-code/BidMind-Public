"""项目级里程碑事件模型 (阶段三)。

设计目的:
- 为"项目级日志时间线"提供 INFO 级业务里程碑数据源
- 与 system_error_logs (WARNING+) 互补, 不污染错误日志表
- 与 task_observations 互补, 后者面向横向比较 (start/stage/end), 不承载业务摘要

字段说明:
- project_id: 关联项目, 时间线视图的核心维度
- incident_id: 与错误日志共享的链路 ID, 让"成功的里程碑"也能与失败链路关联
- task_type: 业务任务类型 (extract / review / collusion / writing_*)
- event_key: 事件键 (如 extract.completed / review.failed), 命名约定为 {阶段}.{结果}
- summary: 一句话摘要 (例如"成功提取 142 条招标要求, 总耗时 12.5s")
- payload_json: 附加结构化数据 (例如 {"items": 142, "duration_ms": 12534})
- occurred_at: 事件发生时间 (与 created_at 不同: created_at 是入库时间)
"""

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.models.base import BaseModel


class ProjectMilestoneEvent(BaseModel):
    """项目里程碑事件 (INFO 级业务节点埋点)。"""

    __tablename__ = "project_milestone_events"

    project_id = Column(
        Integer,
        nullable=True,
        index=True,
        comment="关联项目 ID (无外键: 项目删除后保留以便回看)",
    )
    project_name = Column(String(255), nullable=True, index=True, comment="项目名快照(删除前回填)")
    owner_username = Column(String(50), nullable=True, index=True, comment="项目属主用户名快照(删除前回填)")
    incident_id = Column(
        String(32),
        nullable=True,
        index=True,
        comment="错误链路关联键 (与 system_error_logs.incident_id 共享)",
    )
    task_type = Column(
        String(50),
        nullable=True,
        index=True,
        comment="业务任务类型: extract/review/collusion/writing_*",
    )
    event_key = Column(
        String(80),
        nullable=False,
        index=True,
        comment="事件键, 约定 {阶段}.{结果} 形式, 如 extract.completed / review.failed",
    )
    summary = Column(
        String(500),
        nullable=True,
        comment="一句话摘要",
    )
    payload_json = Column(
        Text,
        nullable=True,
        comment="附加结构化数据 (JSON 字符串)",
    )
    occurred_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
        comment="事件发生时间",
    )
