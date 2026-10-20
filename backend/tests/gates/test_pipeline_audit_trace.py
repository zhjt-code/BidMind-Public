"""S.3.5 单元测试：GatePipeline 写入 _audit 审计字段到 verdict.trace。"""

from __future__ import annotations

import pytest

from app.core.ai.gates.base import Gate, GateContext, GateVerdict
from app.core.ai.gates.pipeline import GatePipeline


class _EchoGate(Gate):
    """最小 Gate：每次都 pass，便于观察 Pipeline 注入 _audit 的行为。"""

    gate_name = "_EchoGate"
    flow = "investigate"
    stage = "post_llm"
    order = 1
    default_severity = "advisory"
    description = "test only"

    def applies(self, ctx: GateContext) -> bool:
        return True

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        return GateVerdict(
            gate_name=self.gate_name,
            verdict="pass",
            severity=self.default_severity,
            reason="echo",
            trace={"existing": "kept"},
        )


def _make_ctx() -> GateContext:
    return GateContext(
        flow="investigate", stage="post_llm",
        target={}, evidence={}, config={},
    )


class TestAuditTraceInjection:
    def test_audit_injected_with_enabled_flag(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "GATE_AUDIT_TRACE_ENABLED", True)

        # 模拟一个有 contextvar 的调用链
        from app.core.ai.prompt_registry import current_node_key
        from app.core.ai.determinism import current_project_id

        tok_nk = current_node_key.set("bid_review.review_item")
        tok_pid = current_project_id.set(101)
        try:
            pipeline = GatePipeline([_EchoGate()], registry=None)
            verdicts = pipeline.run(_make_ctx())
        finally:
            current_node_key.reset(tok_nk)
            current_project_id.reset(tok_pid)

        assert len(verdicts) == 1
        v = verdicts[0]
        assert v.trace is not None
        # 原有字段应保留
        assert v.trace.get("existing") == "kept"
        # 新审计字段应写入
        audit = v.trace.get("_audit")
        assert isinstance(audit, dict)
        assert audit.get("node_key") == "bid_review.review_item"
        assert audit.get("project_id") == 101
        # seed 应被派生（非 None）
        assert "seed" in audit
        # stability_class 未注册节点仍会走 fallback 推导 deterministic
        # 但我们只在 load 阶段设置 stability_class，这里节点未注册
        # 所以 entry is None，不会有 stability_class / prompt_hash
        # 这是预期行为（只在注册节点才有 prompt_hash）

    def test_audit_skipped_when_disabled(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "GATE_AUDIT_TRACE_ENABLED", False)

        pipeline = GatePipeline([_EchoGate()], registry=None)
        verdicts = pipeline.run(_make_ctx())
        assert len(verdicts) == 1
        v = verdicts[0]
        # 开关关闭时不注入 _audit
        assert v.trace is not None
        assert "_audit" not in v.trace

    def test_audit_preserves_existing_trace_keys(self, monkeypatch):
        """既有 trace 字段不应被 _audit 注入覆盖。"""
        from app.core.config import settings
        monkeypatch.setattr(settings, "GATE_AUDIT_TRACE_ENABLED", True)

        pipeline = GatePipeline([_EchoGate()], registry=None)
        verdicts = pipeline.run(_make_ctx())
        v = verdicts[0]
        assert v.trace.get("existing") == "kept"

    def test_audit_no_seed_when_disabled_llm_seed(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "GATE_AUDIT_TRACE_ENABLED", True)
        monkeypatch.setattr(settings, "LLM_SEED_ENABLED", False)

        from app.core.ai.determinism import current_project_id
        tok_pid = current_project_id.set(222)
        try:
            pipeline = GatePipeline([_EchoGate()], registry=None)
            verdicts = pipeline.run(_make_ctx())
        finally:
            current_project_id.reset(tok_pid)

        v = verdicts[0]
        audit = v.trace.get("_audit") or {}
        # LLM_SEED_ENABLED=False 时不派生 seed
        assert "seed" not in audit
        # 但 project_id 仍从 contextvar 读到
        assert audit.get("project_id") == 222
