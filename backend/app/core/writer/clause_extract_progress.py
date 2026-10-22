# -*- coding: utf-8 -*-
"""
职责: 汇总写标约束提取三层并行的整体进度并输出单调心跳快照。
不做: 不写 Redis heartbeat、不访问数据库、不定义 pipeline stage 语义、不执行提取。

背景: ClauseExtractor.extract 用 asyncio.gather 并行运行
hard_constraint / structure_constraint / generation_guide 三层, 每层各自维护
extracted_count 并调用同一 progress_callback, 导致 heartbeat 的 overall%
按最后一次事件所属层的局部进度覆盖, 前端观察到"前进后跳"。

本模块只做两件事:
 1. 聚合三层各自的 done 计数 (层内 + 跨层单调保护)
 2. 将三层事件改写为统一 stage="extracting" 的单条心跳, 让 heartbeat
 在整个约束提取阶段严格单调。
非并行 stage (dedup / saving / scoring_authority / render_profile / industry_profile / ...)
一律按原有 WRITING_EXTRACT_STAGE_WEIGHTS 权重换算, 不进入聚合器。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from app.core.writer.progress_contract import get_stage_weight_range

# 提取子 stage 到聚合 key。三层旧心跳仍可聚合；单次过正文用 extracting_clauses。
_LAYER_STAGE_TO_KEY: dict[str, str] = {
    "extracting_hard_constraint": "hard_constraint",
    "extracting_structure_constraint": "structure_constraint",
    "extracting_generation_guide": "generation_guide",
    "extracting_clauses": "clauses",
}
_THREE_LAYER_KEYS = (
    "hard_constraint",
    "structure_constraint",
    "generation_guide",
)
_SINGLE_PASS_LAYER = "clauses"

# 三层聚合后在 pipeline 整体进度中占据 [0, 60] 区间,
# 与 WRITING_EXTRACT_STAGE_WEIGHTS 中三层区间尾部对齐 (generation_guide 40-60),
# 保证与 dedup(60-75)/saving(75-80)/... 后续阶段无缝衔接。
_AGGREGATED_STAGE = "extracting"
_AGGREGATED_RANGE: tuple[int, int] = (0, 60)


@dataclass(frozen=True)
class ClauseExtractProgressSnapshot:
    """聚合器输出的单条快照，用于生成心跳。"""

    overall_pct: int
    """在 pipeline 整体进度中的百分比 (0-60)。"""

    message: str
    """展示给前端的 detail 文案 (例如 "约束提取 N/M")。"""


@dataclass
class ClauseExtractProgressAggregator:
    """提取进度聚合器。

    契约:
      - record 接受三层旧心跳或单次过正文 (layer=clauses) 的累计 done;
      - snapshot 按模式计算整体百分比: 三层为 total*3, 单次过正文为批次数;
      - 未知层名抛 ValueError, 防止调用方拼错层名而静默漂移。
    """

    _layer_done: dict[str, int] = field(
        default_factory=lambda: {
            "hard_constraint": 0,
            "structure_constraint": 0,
            "generation_guide": 0,
        }
    )
    _total_per_layer: int = 0
    _use_single_pass: bool = False

    def record(
        self,
        *,
        layer: str,
        done: int,
        total_per_layer: int,
    ) -> ClauseExtractProgressSnapshot:
        if layer == _SINGLE_PASS_LAYER:
            self._use_single_pass = True
            self._layer_done.setdefault(_SINGLE_PASS_LAYER, 0)
        elif layer not in _THREE_LAYER_KEYS:
            raise ValueError(f"未知的约束提取层: {layer!r}")

        if total_per_layer > 0:
            # 三层由同一 batches 列表驱动, total_per_layer 恒等;
            # 只做保底 max, 防止某次调用意外传 0/负数覆盖已锁定值。
            self._total_per_layer = max(self._total_per_layer, int(total_per_layer))

        prev = self._layer_done[layer]
        clamped = max(prev, int(done) if done is not None else prev)
        self._layer_done[layer] = clamped

        return self.snapshot()

    def snapshot(self) -> ClauseExtractProgressSnapshot:
        if self._use_single_pass:
            total = self._total_per_layer
            done = int(self._layer_done.get(_SINGLE_PASS_LAYER, 0))
        else:
            total = self._total_per_layer * 3
            done = sum(int(self._layer_done[key]) for key in _THREE_LAYER_KEYS)
        if total <= 0:
            return ClauseExtractProgressSnapshot(overall_pct=0, message="")
        # 钳制到 [0, 60]; 提取完成即触达 60, 交给下游 dedup 继续推进。
        start, end = _AGGREGATED_RANGE
        raw = start + (end - start) * (done / total)
        overall_pct = max(start, min(int(raw), end))

        return ClauseExtractProgressSnapshot(
            overall_pct=overall_pct,
            message=f"约束提取 {done}/{total}",
        )


HeartbeatFn = Callable[..., None]
ProgressCallback = Callable[..., None]


def build_writing_extract_progress_callback(
    *,
    task_name: str,
    heartbeat: HeartbeatFn,
    aggregator: Optional[ClauseExtractProgressAggregator] = None,
) -> ProgressCallback:
    """构造写标约束提取任务的 progress_callback。

    - 三层 stage 事件 → 聚合器 → 单一 stage="extracting" 心跳 (单调)
    - 非三层 stage 事件 → 按 WRITING_EXTRACT_STAGE_WEIGHTS 权重换算 (与旧行为等价)

    Args:
        task_name: 心跳键，通常为 ``writing_extract_{project_id}``。
        heartbeat: 心跳写入函数，签名 ``(name, *, stage, progress, extra=None)``。
        aggregator: 供测试注入现成聚合器；生产路径由本函数内部构造。
    """
    agg = aggregator or ClauseExtractProgressAggregator()

    def _callback(
        stage: str,
        current: int,
        total: int,
        message: str = "",
        *_: object,
    ) -> None:
        layer_key = _LAYER_STAGE_TO_KEY.get(stage)
        if layer_key is not None:
            snapshot = agg.record(
                layer=layer_key,
                done=current,
                total_per_layer=total,
            )
            heartbeat(
                task_name,
                stage=_AGGREGATED_STAGE,
                progress=f"{snapshot.overall_pct}%|{snapshot.message}",
            )
            return

        start, end = get_stage_weight_range(stage)
        ratio = current / total if total > 0 else 0
        overall = int(start + (end - start) * ratio)
        heartbeat(
            task_name,
            stage=stage,
            progress=f"{overall}%|{message}",
        )

    return _callback


__all__ = [
    "ClauseExtractProgressAggregator",
    "ClauseExtractProgressSnapshot",
    "build_writing_extract_progress_callback",
]
