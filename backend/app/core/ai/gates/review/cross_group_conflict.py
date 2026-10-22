"""CrossGroupConflictGate — 跨事实组冲突检测（B3.6, post_pipeline）。

迁移自 reviewer.py ``_cross_group_conflict_detect``。
在 post_pipeline 阶段运行，操作的 target 是分组后的审查结果。
"""

from __future__ import annotations

from app.core.ai.gates.base import Gate, GateContext, GateVerdict


class CrossGroupConflictGate(Gate):
    """跨事实组冲突：A 组称缺失的证据在 B 组已找到时标记冲突。"""

    gate_name = "CrossGroupConflictGate"
    flow = "investigate"
    stage = "post_pipeline"
    order = 10
    default_severity = "enforce"
    description = "跨事实组冲突检测"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "investigate" or ctx.stage != "post_pipeline":
            return False
        return isinstance(ctx.target, dict) and len(ctx.target) > 0

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        """Detect cross-group conflicts in grouped review results.

        ctx.target: dict[fact_key, list[(ReviewResult, Requirement, BidResponse|None)]]
        ctx.evidence: {"extract_missing_keywords": callable, "evidence_covers_missing": callable}
        """
        grouped = ctx.target
        evidence = ctx.evidence or {}
        extract_missing_kw = evidence.get("extract_missing_keywords")
        evidence_covers = evidence.get("evidence_covers_missing")

        if not extract_missing_kw or not evidence_covers:
            return GateVerdict(
                gate_name=self.gate_name, verdict="pass",
                severity=self.default_severity,
                reason="helper functions not provided in evidence",
            )

        conflicts_found = 0

        for key_a, items_a in grouped.items():
            for rr_a, req_a, br_a in items_a:
                cl = getattr(rr_a, "compliance_level", None)
                cl_val = cl.value if hasattr(cl, "value") else cl
                if cl_val not in ("not", "missing", "NOT", "MISSING"):
                    continue
                missing_kws = extract_missing_kw(rr_a.comment or "")
                if not missing_kws:
                    continue

                found_conflict = False
                for key_b, items_b in grouped.items():
                    if key_b == key_a or found_conflict:
                        break
                    for rr_b, req_b, br_b in items_b:
                        cl_b = getattr(rr_b, "compliance_level", None)
                        cl_b_val = cl_b.value if hasattr(cl_b, "value") else cl_b
                        if cl_b_val != "fully":
                            continue
                        if evidence_covers(br_b, rr_b, missing_kws):
                            rr_a.consistency_flag = "cross_conflict"
                            rr_a.reconcile_note = (
                                f"跨类别冲突：本条声称缺失的证据在"
                                f"'{(req_b.description or '')[:40]}'中已找到"
                                f"(来源页:{br_b.source_pages if br_b else '未知'})，"
                                f"建议复核。"
                            )
                            if "跨类别冲突" not in (rr_a.comment or ""):
                                rr_a.comment = (
                                    f"{rr_a.comment}；跨类别冲突：相关证据已在其他类别中找到，建议复核"
                                )
                            conflicts_found += 1
                            found_conflict = True
                            break

        verdict = "enforce_patch" if conflicts_found > 0 else "pass"
        return GateVerdict(
            gate_name=self.gate_name,
            verdict=verdict,
            severity=self.default_severity,
            reason=f"cross_group_conflict: {conflicts_found} conflict(s) found",
            confidence=1.0,
        )
