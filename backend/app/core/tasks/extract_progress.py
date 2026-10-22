# -*- coding: utf-8 -*-
"""招标要求提取进度的唯一步骤契约。

职责: 定义招标要求提取对用户可见的步骤(key/label/stage 集合/整体进度区间)与
 子阶段心跳归一规则, 作为"心跳权重 + 状态接口 steps[] + 前端步骤映射"
 三处消费的单一同源。
不做: 不读写数据库、不访问 Redis、不构建 API 响应、不编排后台任务、不判定
 任务终态(完成态只由业务落库状态背书, 见
 requirement_service._EXTRACT_SETTLED_STATUSES,)。

为何独立成模块: 此前 `requirement_service._EXTRACT_STEP_CONFIG`、
`extract_task.progress_callback` 的 stage_weights 与前端 `EXTRACTION_STAGE_RANGES`
各自维护一份阶段映射, 三份靠人工对齐, 是阶段语义漂移的历史根源(评分项这一
独立长阶段一直被折叠进"保存结果"步骤内)。此处收口为唯一真源: 后端两处直接
import, 前端以 steps[].key 绑定而非按数组下标对位。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractStageBand:
    """一组同义心跳 stage 共享的整体进度区间(0-100)。"""

    stages: tuple[str, ...]
    start: int
    end: int


@dataclass(frozen=True)
class ExtractProgressStep:
    """一个用户可见步骤; 可由多个心跳 stage 子区间组成。"""

    key: str
    label: str
    bands: tuple[ExtractStageBand, ...]

    @property
    def stages(self) -> tuple[str, ...]:
        """本步骤覆盖的全部心跳 stage。"""
        return tuple(stage for band in self.bands for stage in band.stages)

    @property
    def start(self) -> int:
        """本步骤在整体进度中的起点。"""
        return min(band.start for band in self.bands)

    @property
    def end(self) -> int:
        """本步骤在整体进度中的终点。"""
        return max(band.end for band in self.bands)


# 用户可见步骤(顺序即执行顺序)。区间必须首尾相接且终于 100, 由
# test_extract_progress_contract 锁定。
#
# 与 `run_extract_task` 真实执行顺序的对应关系:
#   1 要求提取         -> TenderExtractor 分块并行 LLM 抽取
# 2 高风险条款核验 -> mandatory coverage 原子召回(合并前, 产物进入同一收敛栈;)
#   3 合并与保存       -> 合并去重 + 评审意义判定 + 普通要求落库(同一 extractor 内)
#   4 评分项提取       -> ScoringExtractor 独立子模块(长阶段, 需心跳保活)
#   5 归并核验         -> 跨通道归并 + 提取质量/警告收集 + EXTRACTED 终态提交
EXTRACT_PROGRESS_STEPS: tuple[ExtractProgressStep, ...] = (
    ExtractProgressStep(
        key="extract",
        label="要求提取",
        bands=(
            ExtractStageBand(
                (
                    "extracting",
                    "extracting_hard",
                    "extracting_hard_constraint",
                    "extracting_structure",
                    "extracting_structure_constraint",
                    "extracting_guide",
                    "extracting_generation_guide",
                ),
                0,
                50,
            ),
        ),
    ),
    ExtractProgressStep(
        key="mandatory_coverage",
        label="高风险条款核验",
        bands=(ExtractStageBand(("mandatory_coverage",), 50, 55),),
    ),
    ExtractProgressStep(
        key="consolidate",
        label="合并与保存",
        bands=(
            ExtractStageBand(("merging", "dedup"), 55, 76),
            ExtractStageBand(("saving",), 76, 90),
        ),
    ),
    ExtractProgressStep(
        key="scoring",
        label="评分项提取",
        bands=(ExtractStageBand(("scoring_extract",), 90, 96),),
    ),
    ExtractProgressStep(
        key="finalize",
        label="归并核验",
        bands=(ExtractStageBand(("finalizing",), 96, 100),),
    ),
)

# 任务终态 stage: 只允许 `run_extract_task` 在 EXTRACTED 提交后发射。
EXTRACT_STAGE_COMPLETED = "completed"

# 子提取器回调 completed 时改写成的阶段(仍属"合并与保存"步骤)。
_CHILD_COMPLETED_FALLBACK_STAGE = "saving"


def _build_stage_weights() -> dict[str, tuple[int, int]]:
    """由步骤契约派生 stage -> (start, end) 权重表。"""
    weights: dict[str, tuple[int, int]] = {}
    for step in EXTRACT_PROGRESS_STEPS:
        for band in step.bands:
            for stage in band.stages:
                weights[stage] = (band.start, band.end)
    weights[EXTRACT_STAGE_COMPLETED] = (100, 100)
    return weights


# `extract_task.progress_callback` 用于把子阶段的 current/total 折算为整体进度。
EXTRACT_STAGE_WEIGHTS: dict[str, tuple[int, int]] = _build_stage_weights()


def _consolidate_step() -> ExtractProgressStep:
    for step in EXTRACT_PROGRESS_STEPS:
        if _CHILD_COMPLETED_FALLBACK_STAGE in step.stages:
            return step
    raise AssertionError(
        f"步骤契约缺少承载 {_CHILD_COMPLETED_FALLBACK_STAGE} 的步骤"
    )


# 子提取器 completed 的进度钳制上界: 停在"合并与保存"步骤区间内(不触达其终点),
# 使该步保持 running, 而不会连带把后续步骤显示成已完成。
CHILD_COMPLETED_MAX_PROGRESS: int = _consolidate_step().end - 1


def fold_extract_stage_progress(stage: str, current: int, total: int) -> int:
    """把子阶段 current/total 折成整体 0-100，与提取心跳 percent 同源。

 同一用户步骤内的多次上报必须单调；观测元数据不得把 current 抬到
 后续真实工作之上，否则会被心跳防回退丢掉新文案。
 """
    start, end = EXTRACT_STAGE_WEIGHTS.get(stage, (0, 100))
    if total <= 0:
        return start
    ratio = max(0.0, min(float(current) / float(total), 1.0))
    return int(start + (end - start) * ratio)


def normalize_extract_heartbeat_stage(stage: str, progress: int) -> tuple[str, int]:
    """归一子提取器上报的阶段心跳, 避免其 completed 被误判为任务终态。: `TenderExtractor` 保存完普通要求即回调 stage="completed"/100%, 但
 此时评分项提取与跨通道归并尚未开始。原样写入心跳会让状态接口提前宣告完成,
 前端定格在无评分项的半成品快照。此处改写为"合并与保存"步骤内的进度。
 """
    if stage == EXTRACT_STAGE_COMPLETED:
        return _CHILD_COMPLETED_FALLBACK_STAGE, min(
            progress, CHILD_COMPLETED_MAX_PROGRESS
        )
    return stage, progress


def step_index_for_stage(stage: str) -> int:
    """返回该心跳 stage 所属的步骤下标; 未知 stage 返回 -1。"""
    for idx, step in enumerate(EXTRACT_PROGRESS_STEPS):
        if stage in step.stages:
            return idx
    return -1


__all__ = [
    "CHILD_COMPLETED_MAX_PROGRESS",
    "EXTRACT_PROGRESS_STEPS",
    "EXTRACT_STAGE_COMPLETED",
    "EXTRACT_STAGE_WEIGHTS",
    "ExtractProgressStep",
    "ExtractStageBand",
    "fold_extract_stage_progress",
    "normalize_extract_heartbeat_stage",
    "step_index_for_stage",
]
