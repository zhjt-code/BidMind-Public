"""MaterialSourceTraceGate — 素材引用追踪（B5.2 新建）。

stage=post_llm, default_severity=advisory, order=20。

四级行为：
1. material_pool_empty（项目池空）→ pass + DegradationSignal
2. no_binding（单章未绑定）→ advisory + low_confidence patch
3. pass（引用 ⊂ 绑定列表）→ pass
4. unbound_cited / no_used_id（绑定但未引或引错）→ reject

B5 内 advisory 起步（reject 仅在 enforce 模式下生效）。
"""

from __future__ import annotations

from typing import Any

from app.core.ai.gates.base import Gate, GateContext, GateVerdict


class MaterialSourceTraceGate(Gate):
    """单章级：章节是否真实引用了绑定素材。"""

    gate_name = "MaterialSourceTraceGate"
    flow = "writer"
    stage = "post_llm"
    order = 20
    default_severity = "advisory"
    description = "写标章节素材引用追踪（四级行为）"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "writer" or ctx.stage != "post_llm":
            return False
        evidence = ctx.evidence or {}
        node_type = evidence.get("node_type", "")
        return node_type in {"free_writing", "data_fill", "fixed_template"}

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        evidence = ctx.evidence or {}
        target = ctx.target

        material_pool_size: int = (ctx.config or {}).get(
            "material_pool_size",
            evidence.get("material_pool_size", 0),
        )
        bound_materials: list[int] = evidence.get("bound_materials", [])
        used_material_ids: list[int] = _get_used_ids(target)

        outcome, verdict_str, patch, reason = _classify(
            material_pool_size, bound_materials, used_material_ids,
        )

        return GateVerdict(
            gate_name=self.gate_name,
            verdict=verdict_str,
            severity=self.default_severity,
            reason=reason,
            suggested_patch=patch,
            trace={"outcome": outcome},
        )


def _get_used_ids(target: Any) -> list[int]:
    trace = getattr(target, "consumption_trace", None)
    if isinstance(trace, dict) and isinstance(trace.get("consumed_material_ids"), list):
        ids = trace.get("consumed_material_ids")
    else:
        ids = getattr(target, "used_material_ids", None)
        if ids is None:
            ids = getattr(target, "source_material_ids", None)
    return list(ids) if ids else []


def _classify(
    pool_size: int,
    bound: list[int],
    used: list[int],
) -> tuple[str, str, dict[str, Any] | None, str]:
    """纯函数：返回 (outcome, verdict, patch, reason)。"""

    if pool_size == 0 and not bound:
        return (
            "material_pool_empty",
            "pass",
            None,
            "project material pool is empty; skip check",
        )

    if not bound:
        return (
            "no_binding",
            "warn",
            {"low_confidence": True},
            "section has no bound materials; flagged low_confidence",
        )

    if not used:
        return (
            "no_used_id",
            "reject",
            {"material_trace_missing": True},
            "section did not reference any material_id despite having bindings",
        )

    bound_set = set(bound)
    unbound = [mid for mid in used if mid not in bound_set]
    if unbound:
        return (
            "unbound_cited",
            "reject",
            {"unbound_material_ids": unbound},
            f"section cited unbound material_ids: {unbound}",
        )

    return (
        "pass",
        "pass",
        None,
        "all cited material_ids are within bound set",
    )
