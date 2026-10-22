"""文档处理子包

按业务阶段拆分原 process_task._run_process_task_inner 的 6 个职责模块:

- preflight: Session 1 初始化 + 不匹配/幂等 early return + skip_vl 收口
- parse_stage: 阶段1 PDF 解析(进程池 + keepalive + BrokenProcessPool 重试)
- vl_stage: 阶段2 VL 识别(探测 + 两阶段并行 + 两轮重试 + 质量门禁)
- chunk_stage: 阶段2.5 目录 + 阶段3 分块 + 阶段3.5 图片证据
- vectorize_stage: 阶段4 向量化(含 原子事务边界)
- failure_handler: 取消 / 失败收尾(ProjectCancelledError + 通用异常 + RetryService)

主编排器 `_run_process_task_inner` 仅负责调用本子包的 stage 入口 + try/except 路由。
不变契约: ARQ 任务签名 / VectorJobState 状态机 / Heartbeat 字段 / 原子事务边界。
"""

from app.core.tasks.process.state import (
    ChunkStageResult,
    PreflightResult,
    VectorizeKeepaliveState,
    VLPhaseState,
    VLStageResult,
    image_key,
)

__all__ = [
    "ChunkStageResult",
    "PreflightResult",
    "VectorizeKeepaliveState",
    "VLPhaseState",
    "VLStageResult",
    "image_key",
]
