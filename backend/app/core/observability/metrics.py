"""facts-first harness 指标注册表。

B0 仅实例化 guard 观测 + JSON 修复 + 空返回 + fallback 指标。
后续批次追加 gate_* / failure_state_* / regression_* 指标时在此文件新增即可。

使用 prometheus_client（与 app/core/ai/metrics.py 一致）。
"""

from prometheus_client import Counter, Gauge, Histogram

# ==================== Guard 观测指标（B0 埋点用）====================

guard_invocation_total = Counter(
    "guard_invocation_total",
    "现有 guard 函数调用总次数",
    ["guard_name", "flow", "verdict"],
)

guard_latency_seconds = Histogram(
    "guard_latency_seconds",
    "Guard 函数执行延迟（秒）",
    ["guard_name", "flow"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0),
)

# ==================== JSON 修复指标 ====================

json_repair_total = Counter(
    "json_repair_total",
    "JSON 截断修复触发次数",
    ["repair_type", "flow"],
)

# ==================== 空返回指标 ====================

empty_return_total = Counter(
    "empty_return_total",
    "LLM 空返回（content=None 或空字符串）次数",
    ["flow", "purpose"],
)

# ==================== Fallback 指标 ====================

fallback_trigger_total = Counter(
    "fallback_trigger_total",
    "降级/兜底路径触发次数",
    ["trigger_type", "flow"],
)

# ==================== B2: 约束解码指标 ====================

constrained_decoding_attempts_total = Counter(
    "constrained_decoding_attempts_total",
    "约束解码尝试次数",
    ["flow", "provider", "outcome"],
)

constrained_decoding_latency_seconds = Histogram(
    "constrained_decoding_latency_seconds",
    "约束解码请求延迟（秒）",
    ["flow"],
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0),
)

# ==================== B2: DeAIFilter 指标 ====================

ai_phrase_count_total = Counter(
    "ai_phrase_count_total",
    "AI 套话短语检出累计数",
    ["flow", "node_type"],
)

# ==================== B2: 交叉引用检查异常指标 ====================

cross_reference_check_failure_total = Counter(
    "cross_reference_check_failure_total",
    "交叉引用 LLM 检查异常次数",
    ["flow", "reason"],
)

# ==================== P2.2: node_type 关键词覆盖冲突 ====================
writer_node_type_conflict_total = Counter(
    "writer_node_type_conflict_total",
    "outline_generator 关键词规则覆盖 LLM node_type 判定的次数",
    ["llm_type", "keyword_type"],
)

# ==================== B3: Gate Pipeline 指标 ====================

gate_verdict_total = Counter(
    "gate_verdict_total",
    "Gate 执行裁决次数",
    ["gate", "flow", "stage", "verdict", "effective_severity"],
)

gate_pipeline_duration_seconds = Histogram(
    "gate_pipeline_duration_seconds",
    "Gate Pipeline 完整执行延迟（秒）",
    ["flow"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
)

failure_state_distribution_total = Counter(
    "failure_state_distribution_total",
    "FailureState 分布（B3.9 验收指标）",
    ["flow", "state"],
)

# ==================== B4: 算法化替换指标 ====================

algorithmic_risk_band_agreement_total = Counter(
    "algorithmic_risk_band_agreement_total",
    "算法 risk_band 与 LLM 一致次数",
    ["flow", "is_mandatory", "category"],
)

algorithmic_risk_band_disagreement_total = Counter(
    "algorithmic_risk_band_disagreement_total",
    "算法 vs LLM 不一致 4x4 混淆矩阵",
    ["flow", "alg_band", "llm_band", "is_mandatory"],
)

algorithmic_risk_band_confidence_bucket_total = Counter(
    "algorithmic_risk_band_confidence_bucket_total",
    "算法信度分档 x 一致/不一致",
    ["confidence_bucket", "agree"],
)

physical_form_skip_total = Counter(
    "physical_form_skip_total",
    "物理形态 skip 次数",
    ["flow", "outcome"],
)

# ==================== 阶段8a: 未响应确定性判定影子对比 ====================

deterministic_no_response_outcome_total = Counter(
    "deterministic_no_response_outcome_total",
    "未响应确定性判定产出分布（建议/弃权）",
    ["mode", "outcome"],
)

deterministic_no_response_abstain_total = Counter(
    "deterministic_no_response_abstain_total",
    "未响应确定性判定弃权原因分布",
    ["reason"],
)

deterministic_no_response_agreement_total = Counter(
    "deterministic_no_response_agreement_total",
    "算法建议与 LLM 结论一致次数（按维度）",
    ["dimension", "mode"],
)

deterministic_no_response_disagreement_total = Counter(
    "deterministic_no_response_disagreement_total",
    "算法建议与 LLM 结论分歧混淆矩阵（按维度）",
    ["dimension", "alg", "llm"],
)

date_range_verdict_total = Counter(
    "date_range_verdict_total",
    "时效性判定分布",
    ["verdict"],
)

amount_parse_verdict_total = Counter(
    "amount_parse_verdict_total",
    "金额解析分布",
    ["verdict"],
)

algorithmic_d6_d7_disagreement_total = Counter(
    "algorithmic_d6_d7_disagreement_total",
    "串标 D6/D7 算法 vs LLM 分歧",
    ["dim", "alg_verdict", "llm_verdict"],
)

# ==================== B5: 写标 Gate + Critic 指标 ====================

material_source_trace_outcome_total = Counter(
    "material_source_trace_outcome_total",
    "素材引用追踪结果分布",
    ["flow", "outcome"],
)

cove_outcome_total = Counter(
    "cove_outcome_total",
    "CoVe 复核结果分布",
    ["flow", "trigger", "outcome"],
)

judge_outcome_total = Counter(
    "judge_outcome_total",
    "LLM-as-Judge 结果分布",
    ["flow", "verdict"],
)

judge_inconclusive_total = Counter(
    "judge_inconclusive_total",
    "Judge 不可用（池空或错误）次数",
    ["flow", "reason"],
)

writer_gate_reject_total = Counter(
    "writer_gate_reject_total",
    "写标 Gate reject 次数",
    ["gate", "stage"],
)

# ==================== B5: 输入文本质量指标 ====================

chunk_text_quality_total = Counter(
    "chunk_text_quality_total",
    "Chunk 文本质量分布（clean/degraded/rejected）",
    ["flow", "outcome"],
)

# ==================== 弹性向量化指标 ====================

vector_chunk_plan_total = Counter(
    "vector_chunk_plan_total",
    "Chunk planning 产出分布",
    ["flow", "chunk_type", "outcome"],
)

vector_chunk_char_histogram = Histogram(
    "vector_chunk_char_histogram",
    "Chunk 字符长度分布",
    ["flow", "chunk_type"],
    buckets=(50, 100, 200, 400, 800, 1200, 1800, 2400, 3200, 5000, 8000),
)

vector_encode_batch_total = Counter(
    "vector_encode_batch_total",
    "向量化批次执行分布",
    ["flow", "device", "result"],
)

vector_encode_batch_chars = Histogram(
    "vector_encode_batch_chars",
    "单批向量化字符预算分布",
    ["flow", "device"],
    buckets=(500, 1000, 2000, 4000, 8000, 12000, 16000, 24000, 32000),
)

vector_encode_latency_seconds = Histogram(
    "vector_encode_latency_seconds",
    "向量化批次执行耗时",
    ["flow", "device", "mode"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 20, 40, 80, 160, 320),
)

vector_fallback_total = Counter(
    "vector_fallback_total",
    "向量化回退次数",
    ["flow", "reason", "mode"],
)

gpu_admission_decision_total = Counter(
    "gpu_admission_decision_total",
    "GPU Admission 决策分布",
    ["kind", "action", "reason"],
)

gpu_memory_free_mb = Gauge(
    "gpu_memory_free_mb",
    "GPU 当前可用显存(MB)",
    ["source"],
)

gpu_inflight_gauge = Gauge(
    "gpu_inflight",
    "GPU Admission 当前许可占用",
    ["kind"],
)

vector_lock_wait_seconds = Histogram(
    "vector_lock_wait_seconds",
    "等待 GPU encode 锁耗时",
    ["flow"],
    buckets=(0.001, 0.01, 0.05, 0.1, 0.5, 1, 5, 10, 30, 60, 120),
)

vector_shadow_diff_total = Counter(
    "vector_shadow_diff_total",
    "新旧 chunk 方案影子对比结果",
    ["flow", "diff_type"],
)

vector_job_state_total = Counter(
    "vector_job_state_total",
    "向量化任务状态流转",
    ["state", "queue_mode"],
)

vector_pending_jobs_gauge = Gauge(
    "vector_pending_jobs_gauge",
    "当前未完成的向量化任务数",
    ["queue_mode"],
)

# ==================== CPU 路径自适应指标 ====================
# 与 GPU 路径完全解耦; 由 CpuEncodeConcurrencyController + cpu_encode_semaphore 维护

vector_cpu_concurrency_gauge = Gauge(
    "vector_cpu_concurrency",
    "CPU 路径当前 semaphore 容量 (允许的并发 encode 路数)",
)

vector_cpu_inflight_gauge = Gauge(
    "vector_cpu_inflight",
    "CPU 路径当前在飞 encode 数",
)

vector_cpu_queue_wait_seconds = Histogram(
    "vector_cpu_queue_wait_seconds",
    "等待 CPU encode semaphore 槽位耗时",
    buckets=(0.001, 0.01, 0.05, 0.1, 0.5, 1, 5, 10, 30, 60, 120),
)

vector_cpu_concurrency_adjust_total = Counter(
    "vector_cpu_concurrency_adjust_total",
    "CPU 并发路数 AIMD 调整次数",
    ["direction", "reason"],
)

vector_cpu_load_average_gauge = Gauge(
    "vector_cpu_load_average",
    "系统 1 分钟 load average (Linux); Windows 用 cpu_percent 替代",
)

vector_cpu_topology_gauge = Gauge(
    "vector_cpu_topology",
    "CPU 拓扑与预算只读快照",
    ["field"],
)

# ==================== ARQ Worker / 队列运行态指标 ====================

arq_worker_alive_gauge = Gauge(
    "arq_worker_alive",
    "当前新鲜窗口内存活的 ARQ worker 数 (按 function 维度)",
    ["function"],
)

arq_queue_depth_gauge = Gauge(
    "arq_queue_depth",
    "ARQ 队列当前待消费任务数",
    ["queue_name"],
)

arq_job_queue_wait_seconds = Histogram(
    "arq_job_queue_wait_seconds",
    "ARQ 任务 enqueued_at -> claimed_at 的排队等待时长",
    ["function"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600),
)

arq_job_retry_total = Counter(
    "arq_job_retry_total",
    "ARQ 任务恢复/重试触发计数",
    ["function", "reason"],
)

arq_job_lost_total = Counter(
    "arq_job_lost_total",
    "API 侧判定任务丢失 (worker 心跳过期或超时未推进) 次数",
    ["function", "reason"],
)

worker_resource_inflight_gauge = Gauge(
    "worker_resource_inflight",
    "Worker 资源许可当前占用数",
    ["resource_type"],
)

worker_resource_permit_wait_seconds = Histogram(
    "worker_resource_permit_wait_seconds",
    "Worker 获取资源许可等待时间",
    ["resource_type", "acquired"],
    buckets=(0.01, 0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
)

fallback_active_jobs_gauge = Gauge(
    "fallback_active_jobs",
    "FastAPI 本地 fallback 当前执行中任务数",
    ["resource_type"],
)

worker_job_started_total = Counter(
    "worker_job_started_total",
    "Worker job 开始执行次数",
    ["function", "resource_type", "queue_mode"],
)

worker_job_blocked_total = Counter(
    "worker_job_blocked_total",
    "Worker job 因资源许可不足等待次数",
    ["function", "resource_type"],
)

# ==================== S.4: 稳定性四维指标 ====================
# 由 scripts/regression/stability_replay.py 按 (project_id, flow, runs=N) 采集。
# 四维定义见 docs/regression_sample_spec.md §6.4。

stability_conclusion_rate = Gauge(
    "stability_conclusion_rate",
    "结论稳定率：同输入 N 次回放中 risk_level / compliance_level / overall_risk 完全一致的比例",
    ["flow", "project_id"],
)

stability_failure_state_rate = Gauge(
    "stability_failure_state_rate",
    "失败合同稳定率：同输入 N 次回放中 failure_state 完全一致的比例",
    ["flow", "project_id"],
)

stability_evidence_jaccard = Gauge(
    "stability_evidence_jaccard",
    "关键证据稳定率：source_pages / material_id / finding 集合的平均 Jaccard 相似度",
    ["flow", "project_id"],
)

stability_explanation_drift_rate = Gauge(
    "stability_explanation_drift_rate",
    "解释漂移率：结论相同但 comment / reason 编辑距离 > 阈值的比例（越低越稳定）",
    ["flow", "project_id"],
)

# ==================== S.5.3: D3 算法化对跑指标 ====================

d3_algorithmic_agreement_total = Counter(
    "d3_algorithmic_agreement_total",
    "D3 算法判定与 LLM 判定一致的累计次数（advisory 观测）",
    ["flow", "alg_high", "llm_high"],
)

d3_algorithmic_disagreement_total = Counter(
    "d3_algorithmic_disagreement_total",
    "D3 算法判定与 LLM 判定分歧的累计次数（advisory 观测）",
    ["flow", "alg_high", "llm_high"],
)
