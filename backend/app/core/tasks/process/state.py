"""文档处理流水线共享状态对象与纯函数

替代原 _run_process_task_inner 内的多层 nonlocal / 闭包字典。
所有 dataclass 默认 field_factory 保证零参数构造即可用, 子模块直接 mutate self.xxx。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.models.project import Project


# ── 纯函数 ──────────────────────────────────────────────────────────────────


def image_key(img: Any) -> str:
    """生成图片在 VL 流程中的唯一键, 用于跨阶段去重统计。

    与原 _run_process_task_inner 内 `_image_key` 闭包保持字面量一致(防退化)。
    """
    return f"{img.page_num}:{img.image_index}:{img.file_path or ''}"


# ── PDF 解析阶段返回值 ──────────────────────────────────────────────────────


@dataclass
class PreflightResult:
    """preflight 阶段返回值

    若 preflight 决定 early return(文档不存在 / 项目不匹配 / 幂等命中),
    返回 None 让主编排器跳过后续阶段。
    """

    file_path: str
    doc_project_id: int
    project: Optional[Project]
    business_flow: str
    effective_skip_vl: bool
    image_save_dir: Path


# ── VL 阶段共享状态 ─────────────────────────────────────────────────────────


@dataclass
class VLPhaseState:
    """VL 阶段共享状态(替代 ~15 个 nonlocal 变量)

    生命周期: vl_stage 内由 run_vl_stage 实例化并直接 mutate, 不向 stage 外暴露。
    心跳字段映射: 与原闭包 _refresh_vl_status 字面量一致, 前端依赖。
    """

    image_count: int = 0
    vl_attempted_keys: set[str] = field(default_factory=set)
    vl_recognized_keys: set[str] = field(default_factory=set)
    vl_phase: str = "round1_running"  # round1_running / round1_done / retry_running / done
    vl_current_round: int = 1
    vl_round_total: int = 0
    vl_round_done_keys: set[str] = field(default_factory=set)
    vl_retry_pending: int = 0
    vl_unrecognized_final: int = 0
    vl_retry_round: int = 0
    total_ocr_ok: int = 0
    # 整页 OCR 统计(为 监控)
    scan_ocr_attempted: int = 0
    scan_ocr_ok: int = 0
    scan_ocr_degraded_before: int = 0
    scan_ocr_degraded_after: int = 0


@dataclass
class VLStageResult:
    """VL 阶段对外返回值"""

    total_ocr_ok: int
    vl_count: int
    unrecognized_count: int


@dataclass
class DocumentImageOutcome:
    """
 职责: 承载单文档处理后的图片识别结果计数与来源快照(OCR成功数/图片总数/是否跳过),
 供 vectorize_stage 在 parsed=1 同一事务写入 Document(计费透明度契约)。
 不做: 不承担 VL 阶段过程统计(见 VLPhaseState), 不参与计价计算。
 """

    ocr_ok_count: int = 0
    image_count: int = 0
    vl_skipped: bool = False


# ── chunk 阶段返回值 ─────────────────────────────────────────────────────────


@dataclass
class ChunkStageResult:
    """chunk 阶段返回值

    chunks 必须传给 vectorize_stage; shadow_summary 同步透传到 VectorJobState。
    """

    chunks: list
    shadow_summary: Optional[Any] = None
    evidence_count: int = 0


# ── 向量化阶段共享状态 ──────────────────────────────────────────────────────


@dataclass
class VectorizeKeepaliveState:
    """向量化阶段 keepalive 共享状态(替代 _vec_keepalive_state dict)

    心跳 extra 字段映射: 与原字典字面量一致, 前端依赖
    (vectorizing_substage / vectorizing_meta / degraded_to_cpu / fallback_reason
     / recovery_mode / device_cooldown_sec / stick_to_cpu)。
    """

    substage: str = "wait_lock"
    done: int = 0
    total: int = 0
    degraded_to_cpu: bool = False
    fallback_reason: Optional[str] = None
    meta: dict = field(default_factory=dict)
    recovery_mode: str = "thread"
    device_cooldown_sec: float = 0.0
    stick_to_cpu: bool = False
