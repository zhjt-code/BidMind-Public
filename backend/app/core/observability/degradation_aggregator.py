"""P1.3 降级信号任务级聚合 — writer 链路的任务级可见化。

诊断结论 (高-5):
    writer 模块散布多处 except → fallback / default 的降级路径 (material_tagger
    / material_matcher / consistency_checker.cross_reference / section_composer
    compose_data/compose_free), 每处都 logger.warning, 但上游任务看到的仅是
    "全部成功"。

本模块提供最小可用的任务级聚合:
1. `DegradationAggregator`  — contextvar 绑定的单任务聚合器, 各节点通过
   `record_degradation(kind, ref, reason)` 上报;
2. `set_aggregator_for_task` / `get_aggregator`  — 任务入口/末尾访问器;
3. `summarize()`  — 按 kind 聚合 + 阈值判断 + 文本摘要。

写标 compose 任务在 finally 块里调用 `summarize()`, 把结果写入 heartbeat
的 progress 字段 + (若超阈值) 在 project.error_message 前缀 "[降级警告]"。

其他流程 (review/collusion) 可按同模式接入, 本阶段仅先联通 writer。
"""

from __future__ import annotations

import contextvars
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional


# 各 kind 的阈值比例 (> 阈值判定为严重), 规模过小时不判定为严重
_DEFAULT_RATIO_THRESHOLD: dict[str, float] = {
    "material_tag_failed": 0.20,
    "classify_failed": 0.20,
    "chapter_judge_failed": 0.20,
    "cross_reference_llm_failed": 1.0,  # 任意一次 cross_ref 失败都不算严重 (单次调用)
    "compose_failed": 0.10,
}

_MIN_DENOMINATOR_FOR_SEVERE: dict[str, int] = {
    "material_tag_failed": 3,
    "classify_failed": 3,
    "chapter_judge_failed": 3,
    "compose_failed": 3,
    "cross_reference_llm_failed": 1,
}


@dataclass
class DegradationEntry:
    """单次降级记录。"""
    kind: str
    ref: str = ""
    reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class DegradationSummary:
    """按 kind 聚合后的汇总结果。"""
    counts_by_kind: dict[str, int]
    totals_by_kind: dict[str, int]  # kind → 分母 (总规模, 如素材总数、章节总数)
    severe_kinds: list[str]  # 超阈值的 kind 列表
    text: str  # 形如 "素材打标失败 3/50, 章节裁决失败 1/24" 的紧凑摘要


class DegradationAggregator:
    """单任务内的降级事件聚合器。

    使用 contextvar 绑定, 任务入口调 `set_aggregator_for_task(flow)` 即可
    让各模块 (via `record_degradation`) 自动定位到当前任务的 aggregator 实例。
    跨 task/协程边界时, 外层 aggregator 作用域被继承。
    """

    def __init__(self, flow: str = "unknown") -> None:
        self.flow = flow
        self._entries: list[DegradationEntry] = []
        # 分母快照: kind → 实际处理的总数 (如"总素材数""总章节数"), 供比率计算
        self._denominators: dict[str, int] = {}

    def record(
        self,
        kind: str,
        ref: str = "",
        reason: str = "",
        **extra: Any,
    ) -> None:
        self._entries.append(DegradationEntry(
            kind=kind, ref=str(ref), reason=str(reason)[:200], extra=dict(extra),
        ))

    def set_denominator(self, kind: str, total: int) -> None:
        """标注某类降级的总分母 (如总素材数), 用于 ratio 判定。"""
        if total < 0:
            total = 0
        self._denominators[kind] = int(total)

    def entries(self) -> list[DegradationEntry]:
        return list(self._entries)

    def summarize(self) -> DegradationSummary:
        counts: dict[str, int] = defaultdict(int)
        for e in self._entries:
            counts[e.kind] += 1

        totals = dict(self._denominators)
        severe: list[str] = []
        parts: list[str] = []
        labels = {
            "material_tag_failed": "素材打标失败",
            "classify_failed": "约束分类失败",
            "chapter_judge_failed": "章节裁决失败",
            "cross_reference_llm_failed": "交叉引用检查失败",
            "compose_failed": "章节生成失败",
        }
        for kind, cnt in counts.items():
            total = totals.get(kind, 0)
            label = labels.get(kind, kind)
            if total > 0:
                parts.append(f"{label} {cnt}/{total}")
            else:
                parts.append(f"{label} {cnt}")

            min_denom = _MIN_DENOMINATOR_FOR_SEVERE.get(kind, 3)
            threshold = _DEFAULT_RATIO_THRESHOLD.get(kind, 0.20)
            if total >= min_denom:
                if (cnt / total) > threshold:
                    severe.append(kind)

        return DegradationSummary(
            counts_by_kind=dict(counts),
            totals_by_kind=totals,
            severe_kinds=severe,
            text=", ".join(parts) if parts else "",
        )


_current_aggregator: contextvars.ContextVar[Optional[DegradationAggregator]] = (
    contextvars.ContextVar("_writer_degradation_aggregator", default=None)
)


def set_aggregator_for_task(flow: str = "unknown") -> DegradationAggregator:
    """任务入口调用一次: 创建并绑定新的 aggregator 到 contextvar。"""
    agg = DegradationAggregator(flow=flow)
    _current_aggregator.set(agg)
    return agg


def get_aggregator() -> Optional[DegradationAggregator]:
    """从 contextvar 读取当前任务的 aggregator; 未绑定时返回 None。"""
    return _current_aggregator.get()


def record_degradation(
    kind: str,
    ref: str = "",
    reason: str = "",
    **extra: Any,
) -> None:
    """各业务模块上报降级的统一入口。无 aggregator 时静默忽略。"""
    agg = _current_aggregator.get()
    if agg is None:
        return
    agg.record(kind, ref=ref, reason=reason, **extra)


def set_denominator(kind: str, total: int) -> None:
    """设置某 kind 的总分母 (任务级只需设置一次)。无 aggregator 时忽略。"""
    agg = _current_aggregator.get()
    if agg is None:
        return
    agg.set_denominator(kind, total)


def clear_aggregator() -> None:
    """清理当前任务的 aggregator (供测试/任务清理使用)。"""
    _current_aggregator.set(None)


__all__ = [
    "DegradationEntry",
    "DegradationSummary",
    "DegradationAggregator",
    "set_aggregator_for_task",
    "get_aggregator",
    "record_degradation",
    "set_denominator",
    "clear_aggregator",
]
