"""统一任务观测模型。

Phase 2 核心: 把 Project.status / update_heartbeat(内存+Redis) / VectorJobState
三路"进度/状态"信号归一为一张跨三流程可横向比较的任务观测视图。

设计边界 (docs/ops_observation_model.md):
- 不替代 Project.status (业务语义终态)
- 不替代 VectorJobState (向量化专用, 字段更丰富)
- 不替代 heartbeat (毫秒级推进, 高频不落库)
- 本表只落"事件"级记录: enqueue / start / stage_transition / end 四类
"""

from __future__ import annotations

import enum

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Integer,
    String,
    Text,
)

from app.models.base import BaseModel


class TaskFlow(str, enum.Enum):
    """业务 flow 分类。"""

    TENDER = "tender"          # 招标文件审查 (提取 + 审查)
    COLLUSION = "collusion"    # 围标分析
    WRITER = "writer"          # 标书写作
    PROCESS = "process"        # 底层文档解析 / 向量化
    OTHER = "other"


class TaskPhase(str, enum.Enum):
    """观测事件的 phase 类型。"""

    ENQUEUE = "enqueue"
    START = "start"
    STAGE = "stage"
    END = "end"


class TaskOutcome(str, enum.Enum):
    """终态 outcome (仅当 phase=end 时有意义)。"""

    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class TaskObservation(BaseModel):
    """统一任务观测事件记录。

    字段说明:
      - task_id: 业务层幂等标识 (通常是 `{flow}:{project_id}:{task_type}` 或 ARQ job_id)
      - flow: 所属业务 flow (tender/collusion/writer/process)
      - task_type: 具体任务子类 (extract/review/collusion_analyze/writing_compose/...)
      - phase: 事件类型 (enqueue/start/stage/end)
      - stage: phase=stage 时当前所处阶段名; phase=end 时可填写最后阶段
      - project_id: 关联项目 ID (高基数, 仅入库不入 Prometheus)
      - outcome: 仅 phase=end 时有效
      - duration_ms: 本 phase 持续毫秒; start->end 时写总耗时, stage 时写阶段耗时
      - retry_count: 累计重试次数
      - failure_state: 五态失败契约 (completed/degraded/needs_review/inconclusive/failed)
      - last_error: 错误摘要 (长文本放到 system_error_logs, 这里只留一句话)
      - degraded: 是否触发降级 (0/1)
      - labels_json: 额外标签 JSON (model/node_key/dim/ 等)

    不包含:
      - 业务内容明细 (用 LlmExecutionTrace / project 表)
      - 栈 traceback (用 system_error_logs)
    """

    __tablename__ = "task_observations"

    task_id = Column(String(128), nullable=False, index=True, comment="任务幂等标识")
    flow = Column(
        Enum(
            TaskFlow,
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
            validate_strings=True,
            name="taskflow",
        ),
        nullable=False,
        index=True,
        comment="业务 flow",
    )
    task_type = Column(String(64), nullable=False, index=True, comment="任务子类型")
    phase = Column(
        Enum(
            TaskPhase,
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
            validate_strings=True,
            name="taskphase",
        ),
        nullable=False,
        index=True,
        comment="事件 phase",
    )
    stage = Column(String(64), nullable=True, index=True, comment="当前阶段名")
    project_id = Column(
        Integer,
        nullable=True,
        index=True,
        comment="关联项目 ID (无外键: 项目删除后保留以便回看)",
    )
    project_name = Column(String(255), nullable=True, index=True, comment="项目名快照(删除前回填)")
    owner_username = Column(String(50), nullable=True, index=True, comment="项目属主用户名快照(删除前回填)")
    outcome = Column(
        Enum(
            TaskOutcome,
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
            validate_strings=True,
            name="taskoutcome",
        ),
        nullable=True,
        index=True,
        comment="终态 outcome (phase=end)",
    )
    started_at = Column(DateTime(timezone=True), nullable=True, comment="对应 start 事件时间点")
    ended_at = Column(DateTime(timezone=True), nullable=True, comment="对应 end 事件时间点")
    duration_ms = Column(Integer, nullable=True, comment="持续毫秒 (stage 或 start->end)")
    retry_count = Column(Integer, nullable=False, default=0, comment="累计重试次数")
    failure_state = Column(String(32), nullable=True, index=True, comment="失败契约五态")
    last_error = Column(Text, nullable=True, comment="错误摘要 (单句)")
    degraded = Column(Integer, nullable=False, default=0, comment="是否触发降级 (0/1)")
    labels_json = Column(Text, nullable=True, comment="额外标签 JSON")
