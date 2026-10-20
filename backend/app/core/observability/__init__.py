"""facts-first harness 跨子域指标登记处。

B0 新建。复用 prometheus_client（与 app/core/ai/metrics.py 同框架）。

分工说明：
- app/core/ai/metrics.py ：LLM 子域指标（llm_calls_total / llm_tokens_total 等），保持原位不动
- app/core/observability/  ：跨子域指标（guard_* / gate_* / failure_state_* / regression_*），B0-B5 逐步填充

命名空间预留（B0 仅实例化 guard_* 与 json_repair_* / empty_return_* / fallback_*）：
- guard_*           — B0 现有 guard 观测埋点
- gate_*            — B3 Gate Pipeline 指标（暂不实例化）
- failure_state_*   — B1 失败合同分布指标（暂不实例化）
- regression_*      — B0 回归执行指标（暂不实例化）
"""

import os

OBSERVABILITY_ENABLED: bool = os.environ.get("OBSERVABILITY_ENABLED", "true").lower() == "true"
