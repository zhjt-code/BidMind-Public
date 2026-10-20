"""GateRegistry 注册表。

支持运行时灰度与 A/B 路由。
签名按 docs/evidence_gate_design.md §3.3 定义。
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any

from .base import Flow, Gate, GateContext, Severity

logger = logging.getLogger(__name__)

ab_branch: ContextVar[str | None] = ContextVar("ab_branch", default=None)


class GateRegistry:
    """Gate 注册中心，支持运行时灰度与 A/B。"""

    def __init__(self, global_overrides: str | dict[str, Any] | None = None):
        self._gates: list[Gate] = []
        self._overrides: dict[str, Severity] = {}
        self._project_overrides: dict[str, dict[str, Severity]] = {}
        self._ab_overrides: dict[str, dict[str, Severity]] = {}

        if global_overrides:
            self._load_global_overrides(global_overrides)

    def _load_global_overrides(self, overrides: str | dict[str, Any]) -> None:
        if isinstance(overrides, str):
            try:
                overrides = json.loads(overrides)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Invalid GATE_SEVERITY_OVERRIDES JSON: %s", overrides)
                return

        if isinstance(overrides, dict):
            for gate_name, severity in overrides.items():
                if severity in ("advisory", "enforce", "reject", "off"):
                    self._overrides[gate_name] = severity
                else:
                    logger.warning(
                        "忽略非法 gate severity 配置 %s=%s（合法值: advisory/enforce/reject/off），"
                        "该 Gate 继续使用默认 severity",
                        gate_name,
                        severity,
                    )

    def register(
        self,
        gate: Gate,
        default_severity: Severity | None = None,
    ) -> None:
        if default_severity is not None:
            gate.default_severity = default_severity
        self._gates.append(gate)

    def list_gates(self) -> list[Gate]:
        return list(self._gates)

    def list_by_flow(self, flow: Flow) -> list[Gate]:
        return [g for g in self._gates if g.flow == flow or g.flow == "all"]

    def set_project_override(
        self,
        project_id: str,
        gate_name: str,
        severity: Severity,
    ) -> None:
        self._project_overrides.setdefault(project_id, {})[gate_name] = severity

    def set_ab_override(
        self,
        branch: str,
        gate_name: str,
        severity: Severity,
    ) -> None:
        self._ab_overrides.setdefault(branch, {})[gate_name] = severity

    def resolve_severity(self, gate: Gate, ctx: GateContext) -> Severity:
        """Resolve effective severity for a gate.

        Priority (high to low, per evidence_gate_design §3.3):
        1. AB route override
        2. Project-level override
        3. Global config (GATE_SEVERITY_OVERRIDES)
        4. gate.default_severity
        """
        # 1. AB route
        branch = ab_branch.get()
        if branch and branch in self._ab_overrides:
            ab = self._ab_overrides[branch]
            if gate.gate_name in ab:
                return ab[gate.gate_name]

        # 2. Project-level
        project_id = None
        if ctx.config and "project_id" in ctx.config:
            project_id = str(ctx.config["project_id"])
        if project_id and project_id in self._project_overrides:
            po = self._project_overrides[project_id]
            if gate.gate_name in po:
                return po[gate.gate_name]

        # 3. Global
        if gate.gate_name in self._overrides:
            return self._overrides[gate.gate_name]

        # 4. Default
        return gate.default_severity
