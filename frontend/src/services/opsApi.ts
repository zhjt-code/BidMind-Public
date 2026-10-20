import { api } from './httpClient'
import {
  normalizeFlowFunnelResponse,
  normalizeHotspotsResponse,
  normalizeOpsOverviewResponse,
  normalizeWorkerHealthResponse,
} from './opsApiNormalize'

export interface OpsMetricItem {
  name: string
  layer: string
  pillar: string
  direction: 'higher_better' | 'lower_better' | 'neutral'
  kind: 'counter' | 'histogram' | 'gauge'
  description: string
  value: number
  baseline: number
  verdict: 'improving' | 'stable' | 'warn' | 'critical' | 'neutral'
  delta_pct: number
  reason: string
  breakdown?: unknown
}

export const COST_FLOW_ORDER = ['bid_writing', 'bid_review', 'collusion_check'] as const
export type CostFlowKey = (typeof COST_FLOW_ORDER)[number]

export interface CostFlowRow {
  flow: CostFlowKey
  project_count: number
  input_tokens: number
  output_tokens: number
  total_credits: number
  project_avg_credits: number
}

export interface OpsOverviewResponse {
  window: string
  window_start: string
  baseline_start: string
  generated_at: string
  layers: Record<string, OpsMetricItem[]>
  pillar_summary: Record<string, Record<string, number>>
  business_health: Record<
    string,
    { flow: string; label: string; active: number; failed_in_window: number; status: 'healthy' | 'warn' | 'critical' }
  >
  cost: {
    page_count: number
    input_tokens: number
    output_tokens: number
    project_count: number
    tokens_per_project: number
    tokens_per_page: number
    project_avg_credits: number
    total_credits: number
    credit_yuan_rate: number
    estimated_yuan: number
    unknown_project_count: number
    by_flow: CostFlowRow[]
  }
  dispatcher: Record<string, { channel_count: number; channels: unknown[]; metrics?: unknown }>
}

export interface FlowFunnelItem {
  task_type: string
  started: number
  ended: number
  success: number
  failure: number
  other: number
  success_ratio: number
  avg_duration_ms: number
}

export interface FlowFunnelResponse {
  flow: string
  window: string
  window_start: string
  generated_at: string
  funnel: FlowFunnelItem[]
  outcome_distribution: Record<string, number>
  time_series: Array<{ ts: string; counts: Record<string, number>; total: number }>
}

export type OpsBusinessFlow = 'tender' | 'collusion' | 'writer'

export interface ProjectLogItem {
  project_id: number
  project_name: string | null
  owner_username: string | null
  status: string | null
  is_deleted: boolean
  error_count: number
  warning_count: number
  unresolved_count: number
  last_activity_at: string | null
  last_error_summary: string | null
}

export interface ProjectLogsResponse {
  items: ProjectLogItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface ProjectLogBatchPayload {
  project_ids?: number[]
  select_all?: boolean
  exclude_project_ids?: number[]
  keyword?: string
  days?: number
  only_unresolved?: boolean
}

export interface MetricCatalogItem {
  name: string
  layer: string
  pillar: string
  business: string
  direction: 'higher_better' | 'lower_better' | 'neutral'
  kind: 'counter' | 'histogram' | 'gauge'
  description: string
  labels: string[]
  thresholds: Record<string, number>
  source: { module: string; qualname: string; file: string; line: number }
  unit?: string | null
}

export interface MetricSummaryItem {
  name: string
  layer: string
  pillar: string
  business: string
  direction: string
  kind: string
  description: string
  value: number
  series_points: number
  verdict: 'improving' | 'stable' | 'warn' | 'critical' | 'neutral' | 'degrading'
  algorithm: string
  reason: string
  score: number
  change_points: Array<{ index: number; direction: string }>
  detail: Record<string, unknown>
  vs_baseline: { baseline_value: number; current_value: number; delta_pct: number; source: string } | null
}

export interface HotspotItem {
  name: string
  layer: string
  pillar: string
  business: string
  direction: string
  kind: string
  description: string
  verdict: string
  algorithm: string
  reason: string
  score: number
  change_points: Array<{ index: number; direction: string }>
  detail: Record<string, unknown>
  samples: number
  source: { module: string; qualname: string; file: string; line: number }
  related: {
    llm_execution_trace: Array<{ id: number; created_at: string | null; project_id: number | null }>
    system_error_log: Array<{ id: number; created_at: string | null; level: string; module: string | null; message: string }>
    prompt_version_changes: Array<{
      id: number; purpose: string; version: number; name: string | null; is_active: boolean; updated_at: string | null
    }>
  }
}

export interface HotspotsResponse {
  window: string
  generated_at: string
  total: number
  items: HotspotItem[]
}

export interface MetricRuleItem {
  id: number
  metric_name: string
  rule_type: string
  algorithm: string
  params: Record<string, unknown>
  verdict_map: Record<string, unknown>
  enabled: boolean
  updated_by: string | null
  updated_at: string | null
  description: string | null
}

export type WorkerHealthLevel = 'healthy' | 'degraded' | 'unhealthy' | 'offline'

export interface WorkerHealthItem {
  worker_id: string
  host: string | null
  pid: number | null
  functions: string[]
  state: string
  current_function: string | null
  current_resource: string | null
  event_loop_lag_ms: number | null
  gpu_admission_enabled: boolean | null
  gpu_inflight: number | null
  gpu_free_mb: number | null
  gpu_snapshot_source: string | null
  cuda_model_loaded: boolean | null
  cpu_fallback_model_loaded: boolean | null
}

export interface WorkerResourceSnapshot {
  resource_type: string
  inflight: number
  limit: number
}

export interface WorkerActionItem {
  code: string
  title: string
  severity: 'critical' | 'warn' | 'info'
  owner: string
  description: string
  command: string
  requires_supervisor: boolean
}

export interface WorkerHealthResponse {
  health: WorkerHealthLevel
  worker_count: number
  workers: WorkerHealthItem[]
  expected_functions: string[]
  missing_functions: string[]
  covered_functions: string[]
  resources: WorkerResourceSnapshot[]
  reasons: {
    unhealthy: string[]
    degraded: string[]
  }
  thresholds: {
    event_loop_lag_warn_ms: number
    gpu_probe_min_free_mb: number
  }
  recommended_actions: string[]
  action_items: WorkerActionItem[]
}

// ==================== P0-A: LLM 可靠性 ====================

export interface LlmReliabilityTotals {
  physical_attempts: number
  logical_calls: number
  retried_calls: number
  retry_rate: number
  first_valid_rate: number
  final_valid_rate: number
  empty_rate: number
  http_error_rate: number
  timeout_rate: number
  structured_attempts: number
  structured_fail_rate: number
  degraded_count: number
  outcome_counts: Record<string, number>
  rate_limit_kinds: Record<string, number>
  latency_p50_ms: number
  latency_p95_ms: number
  queue_p50_ms: number
  queue_p95_ms: number
}

export interface LlmReliabilityBucket {
  bucket: string
  attempts: number
  valid: number
  failed: number
}

export interface LlmReliabilityBreakdownRow {
  key: string
  raw_key?: string | null
  display_name?: string
  secondary?: string | null
  label_state?: 'labeled' | 'not_collected' | 'unrecognized'
  attempts: number
  valid_rate: number
  empty_count: number
  structured_fail_count: number
  latency_p95_ms: number
}

export interface LlmLabelCoverageDim {
  labeled: number
  not_collected: number
  unrecognized: number
  coverage_rate: number
}

export interface LlmLabelCoverage {
  total: number
  dimensions: Record<string, LlmLabelCoverageDim>
}

export interface LlmReliabilityResponse {
  window: string
  traffic_class: string
  bucket_seconds: number
  generated_at: string
  totals: LlmReliabilityTotals
  timeseries: LlmReliabilityBucket[]
  group_by: string | null
  breakdown: LlmReliabilityBreakdownRow[]
  label_coverage?: LlmLabelCoverage
  recorder: { queued: number; inserted: number; dropped: number }
}

export interface LlmFailureAggregateRow {
  model: string | null
  model_display: string
  node_key: string | null
  node_display: string
  config_id: string | null
  config_display?: string
  provider: string | null
  provider_display?: string
  attempts: number
  latency_p95_ms: number
}

export interface LlmFailureSample {
  id: number
  occurred_at: string | null
  logical_call_id: string
  attempt_no: number
  outcome: string
  provider: string | null
  provider_display?: string
  model_name: string | null
  config_id: string | null
  config_display?: string
  node_key: string | null
  node_display?: string
  flow: string | null
  flow_display?: string
  purpose: string | null
  purpose_display?: string
  method: string | null
  http_status: number | null
  latency_ms: number
  rate_limit_kind: string | null
}

export interface LlmFailureDrilldownResponse {
  window: string
  traffic_class: string
  outcome: string
  aggregates: LlmFailureAggregateRow[]
  samples: LlmFailureSample[]
  generated_at: string
}

export interface LlmWeeklyAdviceItem {
  id: number
  week_start: string | null
  scope: 'purpose' | 'node' | string
  target: string
  target_display?: string
  advice_action: string
  advice_action_label?: string
  recommended_config_id: string | null
  recommended_config_display?: string | null
  demote_config_id: string | null
  demote_config_display?: string | null
  proposed_priority: Record<string, number> | null
  evidence_json: Record<string, unknown>
  rule_id: string
  rule_label?: string
  status: string
  status_label?: string
  applied_snapshot_id: number | null
  applied_at: string | null
  applied_by: string | null
  writable: boolean
}

export interface LlmAdvicePreview {
  advice_id: number
  purpose: string
  advice_action: string
  proposed_priority: Record<string, number> | null
  current: {
    purpose: string
    priority: Record<string, number>
    fingerprint: string
  }
  rule_id: string
  evidence_json: Record<string, unknown>
}

// axios 拦截器解包了第一层 HTTP 响应 (response.data -> {code, message, data})；
// 这里再解一层把 ApiResponse.data 直接返回业务，避免每个页面都写 resp.data.xxx。
async function unwrapEnvelope<T>(p: Promise<unknown>): Promise<T> {
  const res = (await p) as { data?: T } | T | null | undefined
  if (res && typeof res === 'object' && 'data' in (res as object)) {
    const inner = (res as { data?: T }).data
    return (inner ?? ({} as T))
  }
  return (res as T) ?? ({} as T)
}

export const opsService = {
  getOverview: async (window: string = '24h'): Promise<OpsOverviewResponse> => {
    const resp = await unwrapEnvelope<OpsOverviewResponse>(api.get('/admin/ops/overview', { params: { window } }))
    return normalizeOpsOverviewResponse(resp)
  },
  getVectorJobDistribution: async () => {
    return unwrapEnvelope<{ breakdown: Record<string, number>; active_total: number; terminal_total: number }>(
      api.get('/admin/ops/vector-jobs/distribution'),
    )
  },
  getFlowFunnel: async (flow: 'tender' | 'collusion' | 'writer' | 'process', window: string = '24h') => {
    const resp = await unwrapEnvelope<FlowFunnelResponse>(api.get(`/admin/ops/flow-funnel/${flow}`, { params: { window } }))
    return normalizeFlowFunnelResponse(resp)
  },
  getMetricsCatalog: async (params: { layer?: string; pillar?: string; business?: string } = {}) => {
    return unwrapEnvelope<{ items: MetricCatalogItem[]; total: number }>(
      api.get('/admin/ops/metrics/catalog', { params }),
    )
  },
  getMetricsSummary: async (window: string = '24h', layer?: string) => {
    return unwrapEnvelope<{ window: string; generated_at: string; metrics: MetricSummaryItem[] }>(
      api.get('/admin/ops/metrics/summary', { params: { window, layer } }),
    )
  },
  getMetricsTimeseries: async (name: string, window: string = '24h', labelsHash?: string) => {
    return unwrapEnvelope<{
      name: string
      window: string
      generated_at: string
      points: Array<{
        ts: string; bucket_seconds: number; count: number; sum: number; avg: number; p50: number | null; p95: number | null
      }>
      total: number
    }>(api.get('/admin/ops/metrics/timeseries', { params: { name, window, labels_hash: labelsHash } }))
  },
  getLlmReliability: async (
    window: string = '24h',
    groupBy?: string,
  ): Promise<LlmReliabilityResponse> => {
    return unwrapEnvelope<LlmReliabilityResponse>(
      api.get('/admin/ops/llm/reliability', {
        params: { window, group_by: groupBy },
      }),
    )
  },
  getLlmFailures: async (
    window: string,
    outcome: string,
    opts: { limit?: number; sampleLimit?: number } = {},
  ): Promise<LlmFailureDrilldownResponse> => {
    return unwrapEnvelope<LlmFailureDrilldownResponse>(
      api.get('/admin/ops/llm/failures', {
        params: {
          window,
          outcome,
          limit: opts.limit,
          sample_limit: opts.sampleLimit,
        },
      }),
    )
  },
  getLlmAdvice: async (status?: string) => {
    return unwrapEnvelope<{ items: LlmWeeklyAdviceItem[]; total: number }>(
      api.get('/admin/ops/llm/advice', { params: status ? { status } : {} }),
    )
  },
  generateLlmAdvice: async () => {
    return unwrapEnvelope<{ week_start: string; created: number; updated: number }>(
      api.post('/admin/ops/llm/advice/generate'),
    )
  },
  previewLlmAdvice: async (adviceId: number) => {
    return unwrapEnvelope<LlmAdvicePreview>(
      api.get(`/admin/ops/llm/advice/${adviceId}/preview`),
    )
  },
  applyLlmAdvice: async (adviceId: number, expectedFingerprint: string) => {
    return unwrapEnvelope<Record<string, unknown>>(
      api.post(`/admin/ops/llm/advice/${adviceId}/apply`, {
        expected_fingerprint: expectedFingerprint,
      }),
    )
  },
  rollbackLlmAdvice: async (adviceId: number) => {
    return unwrapEnvelope<Record<string, unknown>>(
      api.post(`/admin/ops/llm/advice/${adviceId}/rollback`),
    )
  },
  getMetricsHotspots: async (window: string = '24h', limit: number = 10) => {
    const resp = await unwrapEnvelope<HotspotsResponse>(api.get('/admin/ops/metrics/hotspots', { params: { window, limit } }))
    return normalizeHotspotsResponse(resp)
  },
  triggerRegression: async (flow?: string) => {
    return unwrapEnvelope<{ job_id: string | null; flow: string }>(
      api.post('/admin/ops/regression/trigger', undefined, { params: flow ? { flow } : {} }),
    )
  },
  getWorkerHealth: async () => {
    const resp = await unwrapEnvelope<WorkerHealthResponse>(api.get('/admin/ops/workers/health'))
    return normalizeWorkerHealthResponse(resp)
  },
  getMetricRules: async (params: { enabled_only?: boolean; metric_name?: string } = {}) => {
    return unwrapEnvelope<{ items: MetricRuleItem[]; total: number }>(
      api.get('/admin/ops/metrics/rules', { params }),
    )
  },
  getProjectLogs: async (
    flow: OpsBusinessFlow,
    params: {
      page?: number
      page_size?: number
      keyword?: string
      days?: number
      only_unresolved?: boolean
    } = {},
  ) => {
    return unwrapEnvelope<ProjectLogsResponse>(
      api.get(`/admin/ops/project-logs/${flow}`, { params }),
    )
  },
  batchResolveProjectLogs: async (flow: OpsBusinessFlow, payload: ProjectLogBatchPayload) => {
    return unwrapEnvelope<{ resolved: number; projects: number }>(
      api.post(`/admin/ops/project-logs/${flow}/batch-resolve`, payload),
    )
  },
  batchDeleteProjectLogs: async (flow: OpsBusinessFlow, payload: ProjectLogBatchPayload) => {
    return unwrapEnvelope<{ deleted: number; projects: number }>(
      api.post(`/admin/ops/project-logs/${flow}/batch-delete`, payload),
    )
  },
}
