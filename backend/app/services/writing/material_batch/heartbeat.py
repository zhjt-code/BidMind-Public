"""素材批处理心跳写入封装（R2-4 Step 2）

集中维护 MaterialService._execute_material_batch 内的 4+ 阶段心跳契约，
避免 ``_set_phase`` 闭包散布在 387 行长方法中、phase 字符串与 progress 格式漂移。

字节级契约（与原 ``_set_phase`` 闭包 byte-equal）：
- ``stage = phase``
- ``progress = f"{progress_pct}%|{message}" if message else f"{progress_pct}%|"``
- ``extra = {"phase": phase}``；当 ``vl_done`` 与 ``vl_total`` 同时不为 ``None`` 时，
  追加 ``extra["vl_progress"] = f"{vl_done}/{vl_total}"``
- 当 ``task_name`` 为 ``None`` 或空串时 early return（asyncio 回退路径不写心跳）

phase 字符串集合（前端 ``WritingMaterials`` 页面与
``MaterialService.get_batch_process_status._FRONTEND_PHASE_ALIAS`` 依赖）：
``parsing`` / ``vl_recognition`` / ``indexing`` / ``image_classify`` /
``label_resolve`` / ``tagging`` / ``completed`` / ``failed`` / ``cancelled``。

入口 phase ``starting`` / ``queued`` 由 ``writing_material_process_task.py``
worker 入口与 ``RetryService`` 写入，本模块不接管。
"""

from __future__ import annotations

from typing import Any, Optional


class MaterialBatchHeartbeat:
    """素材批处理心跳写入器

    与原 ``MaterialService._execute_material_batch._set_phase`` 闭包行为完全一致，
    仅做职责抽离与 phase 字符串集中化。
    """

    PHASE_PARSING: str = "parsing"
    PHASE_VL_RECOGNITION: str = "vl_recognition"
    PHASE_INDEXING: str = "indexing"
    PHASE_IMAGE_CLASSIFY: str = "image_classify"
    PHASE_LABEL_RESOLVE: str = "label_resolve"
    PHASE_TAGGING: str = "tagging"
    PHASE_COMPLETED: str = "completed"

    def __init__(self, task_name: Optional[str]) -> None:
        self.task_name = task_name

    def emit(
        self,
        phase: str,
        *,
        progress_pct: int,
        message: str = "",
        vl_done: Optional[int] = None,
        vl_total: Optional[int] = None,
    ) -> None:
        """写入心跳；asyncio 回退路径下 ``task_name`` 为空时 no-op。"""
        if not self.task_name:
            return

        from app.core.tasks import update_heartbeat as _update_hb

        extra: dict[str, Any] = {"phase": phase}
        if vl_done is not None and vl_total is not None:
            extra["vl_progress"] = f"{vl_done}/{vl_total}"
        _update_hb(
            self.task_name,
            stage=phase,
            progress=f"{progress_pct}%|{message}" if message else f"{progress_pct}%|",
            extra=extra,
        )


__all__ = ["MaterialBatchHeartbeat"]
