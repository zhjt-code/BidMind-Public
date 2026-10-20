"""GatePipeline 执行器。

按 stage + order 执行一组 Gate，聚合 Verdict，应用 Patch。
签名按 docs/evidence_gate_design.md §3.2 定义。
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Any, Iterable

from app.core.config import settings
from app.core.contracts.failure_state import FailureState, max_severity, severity_rank
from app.core.contracts.failure_state_determiner import FailureStateDeterminer
from app.core.observability.instrument import instrument as _observability_instrument

from .base import Gate, GateContext, GateVerdict, Severity, Stage

logger = logging.getLogger(__name__)


def _build_audit_fields(ctx: GateContext) -> dict | None:
    """S.3.5: 构造 gate_trace 审计字段。

    从 contextvar 读取当前 node_key / project_id / seed；从版本缓存计算 prompt_hash；
    不调 DB 不调网络，失败时返回 None（由调用方决定是否写入 trace）。
    """
    try:
        audit: dict = {}
        # node_key + prompt_hash（基于 system/user version id 的轻量哈希）
        try:
            from app.core.ai.prompt_registry import (
                current_node_key as _nk_var,
                get_registry as _get_reg,
            )
            node_key = _nk_var.get()
            if node_key:
                audit["node_key"] = node_key
                reg = _get_reg()
                entry = reg._cache.get(node_key)  # noqa: SLF001 读缓存不改状态
                if entry is not None:
                    sys_v = entry.system_version_id or 0
                    usr_v = entry.user_version_id or 0
                    audit["prompt_hash"] = f"s{sys_v}u{usr_v}"
                    audit["stability_class"] = entry.stability_class
        except Exception:
            pass

        # seed + project_id
        try:
            from app.core.ai.determinism import (
                current_project_id as _pid_var,
                resolve_current_seed,
            )
            pid = _pid_var.get()
            if pid is not None:
                audit["project_id"] = pid
            seed = resolve_current_seed(project_id=pid)
            if seed is not None:
                audit["seed"] = seed
        except Exception:
            pass

        # model_id（从 ctx.config 读，调用方可在构造 GateContext 时传入；
        # 暂时无明确的来源，留占位以便未来扩展）
        if ctx.config and ctx.config.get("model_id"):
            audit["model_id"] = ctx.config["model_id"]

        return audit or None
    except Exception:
        return None

try:
    from app.core.observability.metrics import (
        gate_verdict_total,
        gate_pipeline_duration_seconds,
        failure_state_distribution_total,
    )
    _METRICS_AVAILABLE = True
except Exception:
    _METRICS_AVAILABLE = False

STAGE_ORDER: dict[Stage, int] = {
    "pre_llm": 0,
    "post_llm": 1,
    "post_pipeline": 2,
}

_RISK_LEVEL_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}
_COMPLIANCE_LEVEL_ORDER = {"fully": 0, "partially": 1, "missing": 2, "not": 3}


class GatePipeline:
    """按 stage + order 执行一组 Gate，聚合 Verdict，应用 Patch。"""

    def __init__(
        self,
        gates: Iterable[Gate],
        registry: Any | None = None,
    ):
        self._gates = list(gates)
        self._registry = registry
        self._determiner = FailureStateDeterminer()

    def _sorted_gates(self) -> list[Gate]:
        return sorted(
            self._gates,
            key=lambda g: (STAGE_ORDER.get(g.stage, 99), g.order),
        )

    @_observability_instrument(
        name="gate_pipeline_run_seconds",
        layer="quality",
        pillar="reliability",
        direction="lower_better",
        business="all",
        measure="latency",
        description="GatePipeline.run 单次执行耗时",
    )
    def run(
        self,
        ctx: GateContext,
        apply_patch_to: Any | None = None,
        degraded_signals: set[str] | None = None,
        task_exception: Exception | None = None,
        no_llm_output: bool = False,
    ) -> list[GateVerdict]:
        """Execute all applicable Gates, return Verdict list.

        Execution order: stage (pre_llm -> post_llm -> post_pipeline),
        then order ascending within same stage.
        """
        if degraded_signals is None:
            degraded_signals = set()

        verdicts: list[GateVerdict] = []

        for gate in self._sorted_gates():
            if gate.stage != ctx.stage:
                continue

            if not gate.applies(ctx):
                continue

            effective_severity = self._resolve_severity(gate, ctx)
            if effective_severity == "off":
                continue

            v = gate.evaluate(ctx)
            v = replace(v, severity=effective_severity)
            # S.3.5: 审计字段注入 —— prompt_hash / model_id / seed 写入 trace._audit
            # 便于 A/B 盲评区分"模型飘"（同 seed 不同 model）vs "prompt 改"（同 seed 同
            # model 但 prompt_hash 不同）vs "LLM 飘"（都相同但输出仍有差异）
            if getattr(settings, "GATE_AUDIT_TRACE_ENABLED", True):
                _audit = _build_audit_fields(ctx)
                if _audit:
                    _existing = v.trace or {}
                    v = replace(v, trace={**_existing, "_audit": _audit})
            verdicts.append(v)

            if _METRICS_AVAILABLE:
                gate_verdict_total.labels(
                    gate=v.gate_name, flow=ctx.flow,
                    stage=ctx.stage, verdict=v.verdict,
                    effective_severity=effective_severity,
                ).inc()

        if apply_patch_to is not None:
            self._apply_patches(verdicts, apply_patch_to)

        return verdicts

    def run_all_stages(
        self,
        ctx_pre: GateContext | None = None,
        ctx_post_llm: GateContext | None = None,
        ctx_post_pipeline: GateContext | None = None,
        apply_patch_to: Any | None = None,
        degraded_signals: set[str] | None = None,
        task_exception: Exception | None = None,
        no_llm_output: bool = False,
    ) -> tuple[list[GateVerdict], FailureState, str]:
        """Convenience: run all three stages and determine failure state."""
        if degraded_signals is None:
            degraded_signals = set()

        t0 = time.monotonic()
        all_verdicts: list[GateVerdict] = []

        for ctx in [ctx_pre, ctx_post_llm, ctx_post_pipeline]:
            if ctx is not None:
                vs = self.run(ctx, apply_patch_to=apply_patch_to,
                              degraded_signals=degraded_signals,
                              task_exception=task_exception,
                              no_llm_output=no_llm_output)
                all_verdicts.extend(vs)

        state, reason = self._determiner.determine(
            all_verdicts,
            degraded_signals=degraded_signals,
            task_exception=task_exception,
            no_llm_output=no_llm_output,
        )

        if _METRICS_AVAILABLE:
            flow = "unknown"
            for ctx in [ctx_post_llm, ctx_post_pipeline, ctx_pre]:
                if ctx is not None:
                    flow = ctx.flow
                    break
            gate_pipeline_duration_seconds.labels(flow=flow).observe(
                time.monotonic() - t0,
            )
            failure_state_distribution_total.labels(
                flow=flow, state=state.value,
            ).inc()

        return all_verdicts, state, reason

    def _resolve_severity(self, gate: Gate, ctx: GateContext) -> Severity:
        if self._registry is not None:
            return self._registry.resolve_severity(gate, ctx)
        return gate.default_severity

    @staticmethod
    def _apply_patches(
        verdicts: list[GateVerdict],
        target: Any,
    ) -> None:
        """Apply suggested_patch to target using conservative-merge rules.

        Merge rules (evidence_gate_design §3.4):
        - score: take minimum
        - risk_level: take highest (high > medium > low > none)
        - compliance_level: take most restrictive (not > missing > partially > fully)
        - failure_state: take most severe
        - Other fields: last writer wins (with warning)

        P2-2 扩展: 当 patch value 是 dict 且含 ``certainty_cap`` 字段时，
        对应 target[key] 子字典的 ``certainty`` 字段取 ``min(existing, cap)``，
        不覆盖其他维度字段（score/findings/evidence_level）。这样可以让 Gate
        在"结构信息不可用"时仅降低 LLM 输出的置信度上限，而不误报维度内容。

        Advisory verdicts only apply patches when verdict is "warn" (append-only).
        Enforce/reject verdicts apply all patches.
        """
        merged: dict[str, Any] = {}
        # P2-2: 按 (dim_key) 汇总所有 certainty_cap 请求，最终取最严格的（最小值）
        certainty_caps: dict[str, float] = {}

        for v in verdicts:
            if v.suggested_patch is None:
                continue

            for key, val in v.suggested_patch.items():
                # P2-2: "嵌套 certainty_cap"模式属于保守/append-only 操作
                # （不覆盖维度其他字段，仅限制上限），advisory 也允许应用
                if (
                    isinstance(val, dict)
                    and "certainty_cap" in val
                    and len(val) == 1
                ):
                    try:
                        cap = float(val["certainty_cap"])
                    except (TypeError, ValueError):
                        continue
                    if key not in certainty_caps or cap < certainty_caps[key]:
                        certainty_caps[key] = cap
                    continue

                # 其他 patch 字段：advisory 不应用，enforce/reject 才生效
                if v.severity == "advisory":
                    continue

                if key not in merged:
                    merged[key] = val
                    continue

                existing = merged[key]

                if key == "score":
                    merged[key] = min(existing, val)
                elif key == "risk_level":
                    if _RISK_LEVEL_ORDER.get(val, 0) > _RISK_LEVEL_ORDER.get(existing, 0):
                        merged[key] = val
                elif key == "compliance_level":
                    if _COMPLIANCE_LEVEL_ORDER.get(val, 0) > _COMPLIANCE_LEVEL_ORDER.get(existing, 0):
                        merged[key] = val
                elif key == "failure_state":
                    merged[key] = max_severity([existing, val]).value
                else:
                    logger.warning(
                        "Patch conflict on field '%s': %s vs %s, last writer wins",
                        key, existing, val,
                    )
                    merged[key] = val

        if not merged and not certainty_caps:
            return

        if isinstance(target, dict):
            if merged:
                target.update(merged)
            # P2-2: certainty_cap 合并（不覆盖现有 dim 字段）
            for dim_key, cap in certainty_caps.items():
                dim_payload = target.get(dim_key)
                if not isinstance(dim_payload, dict):
                    # target 中没有该维度时，不创建伪维度，仅忽略 cap（保持保守）
                    continue
                existing_certainty = dim_payload.get("certainty")
                try:
                    existing_val = float(existing_certainty) if existing_certainty is not None else 1.0
                except (TypeError, ValueError):
                    existing_val = 1.0
                dim_payload["certainty"] = round(min(existing_val, cap), 4)
        else:
            for k, v_val in merged.items():
                if hasattr(target, k):
                    setattr(target, k, v_val)
            # P2-2: 对非 dict target（如 Pydantic 模型），尝试 setattr 做 cap 合并
            for dim_key, cap in certainty_caps.items():
                dim_payload = getattr(target, dim_key, None)
                if not isinstance(dim_payload, dict):
                    continue
                existing_certainty = dim_payload.get("certainty")
                try:
                    existing_val = float(existing_certainty) if existing_certainty is not None else 1.0
                except (TypeError, ValueError):
                    existing_val = 1.0
                dim_payload["certainty"] = round(min(existing_val, cap), 4)
