"""ops_metrics 子包: 运维监控指标聚合服务层 (R2-8 拆分产物).

按 v2 refactor_execution_plan_round2.md R2-8 卡片要求,把 admin_ops.py router
中 8 个 layer 函数 + 2 个领域聚合 + 6 个 SQL/装配密集 endpoint 业务块迁出到此子包,
admin_ops.py router 仅保留 endpoint 装饰、参数校验与共享 helper (作为测试 monkeypatch
锚点)。

子模块按业务/职责划分:
- platform / throughput / stage / quality / llm / vector / stability / ops: 八层 metric
- business_health / cost_snapshot: 两个领域聚合
- overview / flow_funnel / metrics_catalog / metrics_summary / metrics_hotspots / metrics_timeseries:
  endpoint 内业务装配 (SQL + 数据装配)

子模块函数命名规范 (沿用 R2-2 / R2-3 子模块方案):
- 去前导下划线: _layer_quality → layer_quality
- 第一参数 keyword-only: def layer_quality(*, prom_counter_sum, critic_flow_breakdown)
- 共享 helper (_prom_counter_sum / _critic_flow_breakdown / PROM_REGISTRY 等) 仍留
  admin_ops 主文件作为测试 patch 锚点, 通过 kwargs 依赖注入透传到子模块
"""
from __future__ import annotations
