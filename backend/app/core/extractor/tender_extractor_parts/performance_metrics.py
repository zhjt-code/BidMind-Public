"""
职责: 记录招标要求提取单次任务的轻量性能指标。
不做: 不做业务判定、不做缓存读写、不做数据库持久化策略。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter


@dataclass
class TenderExtractionMetrics:
    """单次招标要求提取任务的内存态观测快照。"""

    total_chunks: int = 0
    llm_calls: int = 0
    cache_hits: int = 0
    empty_chunks: int = 0
    failed_chunks: int = 0
    candidate_requirements: int = 0
    premerge_before: int = 0
    premerge_after: int = 0
    merged_requirements: int = 0
    gated_singletons: int = 0
    gated_components: int = 0
    near_dup_premerged: int = 0
    embed_wait_ms: int = 0
    _stage_started_at: dict[str, float] = field(default_factory=dict)
    _stage_durations_ms: dict[str, int] = field(default_factory=dict)

    def set_total_chunks(self, count: int) -> None:
        self.total_chunks = max(int(count), 0)

    def mark_stage_start(self, stage: str) -> None:
        if stage:
            self._stage_started_at[stage] = perf_counter()

    def mark_stage_end(self, stage: str) -> None:
        if not stage:
            return
        started_at = self._stage_started_at.pop(stage, None)
        if started_at is None:
            return
        self._stage_durations_ms[f"{stage}_ms"] = max(
            int((perf_counter() - started_at) * 1000),
            0,
        )

    def record_llm_call(self) -> None:
        self.llm_calls += 1

    def record_cache_hit(self, *, item_count: int) -> None:
        self.cache_hits += 1
        self.record_chunk_result(item_count=item_count)

    def record_chunk_result(self, *, item_count: int) -> None:
        safe_count = max(int(item_count), 0)
        if safe_count == 0:
            self.empty_chunks += 1
        self.candidate_requirements += safe_count

    def record_chunk_failure(self) -> None:
        self.failed_chunks += 1

    def record_premerge(self, *, before_count: int, after_count: int) -> None:
        self.premerge_before = max(int(before_count), 0)
        self.premerge_after = max(int(after_count), 0)

    def record_merge_result(self, *, merged_count: int) -> None:
        self.merged_requirements = max(int(merged_count), 0)

    def record_candidate_gate(
        self,
        *,
        singletons: int,
        components: int,
        embed_wait_ms: int = 0,
    ) -> None:
        self.gated_singletons += max(int(singletons), 0)
        self.gated_components += max(int(components), 0)
        self.embed_wait_ms += max(int(embed_wait_ms), 0)

    def record_near_dup_premerge(self, *, removed_count: int) -> None:
        self.near_dup_premerged += max(int(removed_count), 0)

    def to_heartbeat_meta(self) -> dict[str, object]:
        return {
            "total_chunks": self.total_chunks,
            "llm_calls": self.llm_calls,
            "cache_hits": self.cache_hits,
            "empty_chunks": self.empty_chunks,
            "failed_chunks": self.failed_chunks,
            "candidate_requirements": self.candidate_requirements,
            "premerge_before": self.premerge_before,
            "premerge_after": self.premerge_after,
            "merged_requirements": self.merged_requirements,
            "gated_singletons": self.gated_singletons,
            "gated_components": self.gated_components,
            "near_dup_premerged": self.near_dup_premerged,
            "embed_wait_ms": self.embed_wait_ms,
            "stage_durations_ms": dict(self._stage_durations_ms),
        }
