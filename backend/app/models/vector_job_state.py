"""向量化任务状态表。"""

import enum

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class VectorJobStatus(str, enum.Enum):
    """向量化任务状态机。

    状态流转:
        pending -> queued -> claimed -> running -> completed
                                          |-> failed
                                          |-> lost -> recovering -> queued

    语义说明:
    - PENDING: 初始/兼容态; API 已创建状态行但尚未决定调度方式
    - QUEUED: 已入队 ARQ, 等待 worker 领取
    - CLAIMED: worker 已领取, 正在做初始化(未真正开始业务逻辑)
    - RUNNING: 正在执行处理流程
    - COMPLETED: 成功结束
    - FAILED: 终态失败
    - LOST: API 侧检测到 worker 心跳丢失或任务超时未推进
    - RECOVERING: 自动恢复中(已触发重入队或本地任务)
    """

    PENDING = "pending"
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    LOST = "lost"
    RECOVERING = "recovering"


# 活跃态集合: 这些状态下任务仍占用 pending gauge, 非终态
ACTIVE_VECTOR_JOB_STATES: frozenset[VectorJobStatus] = frozenset({
    VectorJobStatus.PENDING,
    VectorJobStatus.QUEUED,
    VectorJobStatus.CLAIMED,
    VectorJobStatus.RUNNING,
    VectorJobStatus.LOST,
    VectorJobStatus.RECOVERING,
})

# 终态集合
TERMINAL_VECTOR_JOB_STATES: frozenset[VectorJobStatus] = frozenset({
    VectorJobStatus.COMPLETED,
    VectorJobStatus.FAILED,
})


class VectorJobState(BaseModel):
    """持久化向量化任务状态，支撑 ARQ 状态机、恢复、灰度和影子对比。"""

    __tablename__ = "vector_job_states"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_vector_job_states_document_id"),
    )

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目ID",
    )
    document_id = Column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属文档ID",
    )
    task_name = Column(String(128), nullable=False, comment="任务名")
    status = Column(
        Enum(
            VectorJobStatus,
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
            validate_strings=True,
            name="vectorjobstatus",
        ),
        nullable=False,
        default=VectorJobStatus.PENDING,
        comment="任务状态",
    )
    queue_mode = Column(String(32), nullable=False, default="asyncio", comment="任务承载模式")
    stage = Column(String(64), nullable=True, comment="当前阶段")
    progress = Column(String(128), nullable=True, comment="进度摘要")
    degraded_to_cpu = Column(Integer, nullable=False, default=0, comment="是否降级到CPU")
    fallback_reason = Column(String(128), nullable=True, comment="降级原因")
    recovery_mode = Column(String(32), nullable=True, comment="thread/process")
    retry_count = Column(Integer, nullable=False, default=0, comment="恢复/重试次数")
    chunk_fingerprint_version = Column(
        String(64),
        nullable=False,
        default="bge-m3-v1",
        comment="当前向量版本",
    )
    shadow_summary = Column(Text, nullable=True, comment="影子对比摘要(JSON)")
    last_error = Column(Text, nullable=True, comment="最后一次错误")

    # ─── ARQ 运行态字段 ──────────────────────────────────────────────
    # 用于精确区分 queued / claimed / running / lost, 支撑自动恢复判定
    arq_job_id = Column(String(128), nullable=True, comment="ARQ enqueue_job 返回的 job_id")
    worker_id = Column(String(128), nullable=True, comment="领取该任务的 worker 标识")
    enqueued_at = Column(DateTime(timezone=True), nullable=True, comment="入队时间")
    claimed_at = Column(DateTime(timezone=True), nullable=True, comment="worker 领取时间")
    started_at = Column(DateTime(timezone=True), nullable=True, comment="真正开始处理时间")
    finished_at = Column(DateTime(timezone=True), nullable=True, comment="结束时间(成功/失败)")
    last_heartbeat_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="worker 最近一次推进心跳的时间, API 侧据此判定超时",
    )

    project = relationship("Project")
    document = relationship("Document")
