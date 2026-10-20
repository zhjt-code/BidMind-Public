"""素材批处理流水线子包（R2-4）

承载 ``MaterialService._execute_material_batch`` 拆分后的 Phase 1-4 子模块。
主类通过 ``from app.services.writing.material_batch import ...`` 装配各阶段函数，
所有 module 仅承担纯流程职责，状态通过函数入参显式传递，不持有跨阶段实例字段。
"""

from app.services.writing.material_batch import (
    phase1_parse,
    phase2_vl,
    phase3_assemble,
    phase4_postprocess,
)
from app.services.writing.material_batch.heartbeat import MaterialBatchHeartbeat

__all__ = [
    "MaterialBatchHeartbeat",
    "phase1_parse",
    "phase2_vl",
    "phase3_assemble",
    "phase4_postprocess",
]
