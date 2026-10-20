"""评分提取 scope trace 状态——ScoringExtractionTraceState。

职责: 在单文档提取生命周期内追踪 chunk→scope 映射、scope-local 卷定义，
      供 L3 证据包构建和权威期望生成使用；lot 不唯一或 UNASSIGNED 时记录诊断并跳过。
不做: 不调用 LLM、不访问数据库、不修改任何已有模块、不持久化任何数据。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.core.extractor.scoring.scoring_full_candidate_context import (
    ScoringChunkScopeTrace,
    build_scoring_chunk_scope_trace,
)
from app.core.extractor.scoring.scoring_scope_contracts import (
    ScopeIdentity,
    ScoringScopeExpectation,
    ScoringScopeValidation,
    ScoringValidationKind,
)

_UNASSIGNED_LOT = "__unassigned__"


@dataclass
class ScoringExtractionTraceState:
    """单文档提取 scope trace 状态（内存生命周期，不持久化）。

    职责: 按 chunk 记录 scope trace；维护 scope-local volume_defs；生成权威期望。
    不做: 不猜 scope，不访问 DB，不暴露文档原文。
    """

    traces: dict[int, ScoringChunkScopeTrace] = field(default_factory=dict)
    # scope → { volume → [volume_max values from each chunk] }
    _scope_volume_max: dict[ScopeIdentity, dict[str, list[float]]] = field(
        default_factory=dict
    )
    # 被注入的明确期望（供测试或外部覆盖）
    _injected_expectations: dict[ScopeIdentity, ScoringScopeExpectation] = field(
        default_factory=dict
    )
    skipped_diagnostics: list[dict] = field(default_factory=list)

    def try_record_chunk(
        self,
        chunk: Any,
        scheme: str,
        lot: str | None,
        volume_defs: dict,
    ) -> bool:
        """尝试记录 chunk 的 scope trace。

        记录条件: scheme 非 None、lot 非 None/空/"__unassigned__"、chunk_id 有效。
        跳过时追加结构化诊断（无原文）并返回 False。

        Args:
            chunk:       chunk ORM 对象（含 id/page_num 等）。
            scheme:      SchemeSegmenter 已赋值的方案标识。
            lot:         chunk items 的唯一 lot；None/空/UNASSIGNED 表示不可追踪。
            volume_defs: ParsedScoringTable.volume_defs（该 chunk 的 scope-local 定义）。

        Returns:
            True 表示成功记录；False 表示跳过。
        """
        chunk_id = _safe_int(getattr(chunk, "id", 0))

        # lot 检查
        if not lot or lot == _UNASSIGNED_LOT:
            self.skipped_diagnostics.append({
                "chunk_id": chunk_id,
                "reason": "lot_not_unique_or_unassigned",
                "lot": lot,
            })
            return False

        # scheme 检查
        if not scheme:
            self.skipped_diagnostics.append({
                "chunk_id": chunk_id,
                "reason": "scheme_unassigned",
                "lot": lot,
            })
            return False

        scope: ScopeIdentity = (scheme, lot)

        # 尝试构建 ScoringChunkScopeTrace
        try:
            trace = build_scoring_chunk_scope_trace(chunk, scope)
        except ValueError as exc:
            self.skipped_diagnostics.append({
                "chunk_id": chunk_id,
                "reason": "trace_build_failed",
                "detail": str(exc),
            })
            return False

        self.traces[chunk_id] = trace
        self._accumulate_volume_defs(scope, volume_defs)
        return True

    def _accumulate_volume_defs(
        self, scope: ScopeIdentity, volume_defs: dict
    ) -> None:
        """将 chunk 的 volume_defs 中的 volume_max 累积到 scope-local 记录。"""
        if not volume_defs:
            return
        scope_vm = self._scope_volume_max.setdefault(scope, {})
        for vol, vdef in volume_defs.items():
            try:
                vm = float(getattr(vdef, "volume_max", 0) or 0)
            except (TypeError, ValueError):
                continue
            if vm <= 0:
                continue
            scope_vm.setdefault(vol, []).append(vm)

    def _register_scope_volume_defs(
        self, scope: ScopeIdentity, volume_defs: dict
    ) -> None:
        """供测试直接注入 scope volume_defs。"""
        self._accumulate_volume_defs(scope, volume_defs)

    def _inject_scope_expectation(
        self, scope: ScopeIdentity, exp: ScoringScopeExpectation
    ) -> None:
        """供测试直接注入明确期望。"""
        self._injected_expectations[scope] = exp

    def build_scope_expectations(
        self,
    ) -> dict[ScopeIdentity, ScoringScopeExpectation]:
        """从 scope-local volume_max 生成权威 ScoringScopeExpectation。

        规则:
        - 只含 expected_by_volume（来自 volume_max）。
        - 同一 scope 内同一 volume 存在冲突 volume_max → 整个 scope 不生成期望。
        - 注入期望优先级最高，直接覆盖生成结果。
        """
        result: dict[ScopeIdentity, ScoringScopeExpectation] = {}

        for scope, vol_map in self._scope_volume_max.items():
            by_volume: list[tuple[str, float]] = []
            conflict = False
            for vol, vm_list in vol_map.items():
                unique = set(round(v, 4) for v in vm_list)
                if len(unique) > 1:
                    conflict = True
                    break
                by_volume.append((vol, unique.pop()))
            if conflict or not by_volume:
                continue
            try:
                exp = ScoringScopeExpectation(
                    expected_by_volume=tuple(sorted(by_volume))
                )
                result[scope] = exp
            except ValueError:
                continue

        # 注入期望覆盖（测试/外部 override）
        result.update(self._injected_expectations)
        return result


def _safe_int(val: Any) -> int:
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# L3 dispatch helpers（trace 模块负责 scope/evidence 管理相关辅助）
# ---------------------------------------------------------------------------


def failure_validation(
    scope: ScopeIdentity,
    expectation: "ScoringScopeExpectation | None" = None,
) -> ScoringScopeValidation:
    """构建表示 L3 scope 失败的 EVIDENCE_GAP 验证（无原文）。

    expectation=None 时 expected_by_scope 为空（绝不硬编码100）。
    expectation 传入真实基准时写入 expected_by_scope 供下游 L3 派发使用。
    """
    from types import MappingProxyType
    exp_by_scope = MappingProxyType({scope: expectation} if expectation is not None else {})
    return ScoringScopeValidation(
        kind=ScoringValidationKind.EVIDENCE_GAP,
        needs_review=True,
        note=f"L3 scope={scope} 未闭环",
        total=0.0,
        failed_scopes=(scope,),
        expected_by_scope=exp_by_scope,
    )


def elapsed_ms(start: float) -> float:
    return round((time.monotonic() - start) * 1000, 1)


def build_l3_fidelity_sidecar(new_items: list, scope: ScopeIdentity) -> dict:
    """为 L3 采纳项构建 fidelity sidecar（ok + 逐字锚定门禁说明）。"""
    from app.core.extractor.scoring.scoring_fidelity import (
        FidelityAnnotation,
        build_fidelity_key,
    )
    sidecar = {}
    for item in new_items:
        item_scope = (
            str(getattr(item, "scheme", "") or "__single__"),
            str(getattr(item, "lot", "") or "__single__"),
        )
        if item_scope != scope:
            continue
        key = build_fidelity_key(item)
        sidecar[key] = FidelityAnnotation(
            flag="ok",
            note="L3完整候选已通过逐字锚定门禁",
            overlap_score=1.0,
        )
    return sidecar


__all__ = [
    "ScoringExtractionTraceState",
    "build_l3_fidelity_sidecar",
    "elapsed_ms",
    "failure_validation",
]
