import type {
  CostFlowKey,
  CostFlowRow,
  FlowFunnelItem,
  FlowFunnelResponse,
  HotspotItem,
  HotspotsResponse,
  OpsMetricItem,
  OpsOverviewResponse,
  WorkerActionItem,
  WorkerHealthItem,
  WorkerHealthLevel,
  WorkerHealthResponse,
  WorkerResourceSnapshot,
} from './opsApi.ts'

const COST_FLOW_ORDER: readonly CostFlowKey[] = ['bid_writing', 'bid_review', 'collusion_check']

type RecordLike = Record<string, unknown>

function isRecord(value: unknown): value is RecordLike {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : []
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function asBooleanOrNull(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null
}

function asNumberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function normalizeHealth(value: unknown): WorkerHealthLevel {
  return value === 'healthy' || value === 'degraded' || value === 'unhealthy' || value === 'offline'
    ? value
    : 'offline'
}

function normalizeDirection(value: unknown): OpsMetricItem['direction'] {
  return value === 'higher_better' || value === 'lower_better' || value === 'neutral'
    ? value
    : 'neutral'
}

function normalizeMetricKind(value: unknown): OpsMetricItem['kind'] {
  return value === 'counter' || value === 'histogram' || value === 'gauge' ? value : 'gauge'
}

function normalizeMetricVerdict(value: unknown): OpsMetricItem['verdict'] {
  return value === 'improving' || value === 'stable' || value === 'warn' || value === 'critical' || value === 'neutral'
    ? value
    : 'neutral'
}

function normalizeMetric(value: unknown): OpsMetricItem {
  const metric = isRecord(value) ? value : {}
  return {
    name: asString(metric.name, 'unknown'),
    layer: asString(metric.layer),
    pillar: asString(metric.pillar),
    direction: normalizeDirection(metric.direction),
    kind: normalizeMetricKind(metric.kind),
    description: asString(metric.description),
    value: asNumber(metric.value),
    baseline: asNumber(metric.baseline),
    verdict: normalizeMetricVerdict(metric.verdict),
    delta_pct: asNumber(metric.delta_pct),
    reason: asString(metric.reason),
    breakdown: metric.breakdown,
  }
}

function normalizeMetricLayers(value: unknown): Record<string, OpsMetricItem[]> {
  if (!isRecord(value)) return {}
  return Object.fromEntries(
    Object.entries(value).map(([key, items]) => [key, asArray<unknown>(items).map(normalizeMetric)]),
  )
}

function normalizeNumberRecord(value: unknown): Record<string, number> {
  if (!isRecord(value)) return {}
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, asNumber(item)]))
}

function normalizePillarSummary(value: unknown): Record<string, Record<string, number>> {
  if (!isRecord(value)) return {}
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [key, normalizeNumberRecord(item)]),
  )
}

function normalizeBusinessHealth(value: unknown): OpsOverviewResponse['business_health'] {
  if (!isRecord(value)) return {}
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => {
      const row = isRecord(item) ? item : {}
      const status = row.status === 'healthy' || row.status === 'warn' || row.status === 'critical'
        ? row.status
        : 'healthy'
      return [key, {
        flow: asString(row.flow, key),
        label: asString(row.label, key),
        active: asNumber(row.active),
        failed_in_window: asNumber(row.failed_in_window),
        status,
      }]
    }),
  )
}

function normalizeDispatcher(value: unknown): OpsOverviewResponse['dispatcher'] {
  if (!isRecord(value)) return {}
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => {
      const row = isRecord(item) ? item : {}
      return [key, {
        channel_count: asNumber(row.channel_count),
        channels: asArray<unknown>(row.channels),
        metrics: row.metrics,
      }]
    }),
  )
}

function emptyCostFlowRow(flow: CostFlowKey): CostFlowRow {
  return {
    flow,
    project_count: 0,
    input_tokens: 0,
    output_tokens: 0,
    total_credits: 0,
    project_avg_credits: 0,
  }
}

function normalizeByFlow(value: unknown): CostFlowRow[] {
  const incoming = new Map<string, CostFlowRow>()
  for (const item of asArray<unknown>(value)) {
    if (!isRecord(item)) continue
    const flow = asString(item.flow)
    if (!COST_FLOW_ORDER.includes(flow as CostFlowKey)) continue
    incoming.set(flow, {
      flow: flow as CostFlowKey,
      project_count: asNumber(item.project_count),
      input_tokens: asNumber(item.input_tokens),
      output_tokens: asNumber(item.output_tokens),
      total_credits: asNumber(item.total_credits),
      project_avg_credits: asNumber(item.project_avg_credits),
    })
  }
  return COST_FLOW_ORDER.map((flow) => incoming.get(flow) ?? emptyCostFlowRow(flow))
}

function normalizeCost(value: unknown): OpsOverviewResponse['cost'] {
  const cost = isRecord(value) ? value : {}
  return {
    page_count: asNumber(cost.page_count),
    input_tokens: asNumber(cost.input_tokens),
    output_tokens: asNumber(cost.output_tokens),
    project_count: asNumber(cost.project_count),
    tokens_per_project: asNumber(cost.tokens_per_project),
    tokens_per_page: asNumber(cost.tokens_per_page),
    project_avg_credits: asNumber(cost.project_avg_credits),
    total_credits: asNumber(cost.total_credits),
    credit_yuan_rate: asNumber(cost.credit_yuan_rate),
    estimated_yuan: asNumber(cost.estimated_yuan),
    unknown_project_count: asNumber(cost.unknown_project_count),
    by_flow: normalizeByFlow(cost.by_flow),
  }
}

export function normalizeOpsOverviewResponse(input: unknown): OpsOverviewResponse {
  const raw = isRecord(input) ? input : {}
  return {
    window: asString(raw.window),
    window_start: asString(raw.window_start),
    baseline_start: asString(raw.baseline_start),
    generated_at: asString(raw.generated_at),
    layers: normalizeMetricLayers(raw.layers),
    pillar_summary: normalizePillarSummary(raw.pillar_summary),
    business_health: normalizeBusinessHealth(raw.business_health),
    cost: normalizeCost(raw.cost),
    dispatcher: normalizeDispatcher(raw.dispatcher),
  }
}

function normalizeWorker(value: unknown): WorkerHealthItem {
  const worker = isRecord(value) ? value : {}
  return {
    worker_id: asString(worker.worker_id, 'unknown'),
    host: typeof worker.host === 'string' ? worker.host : null,
    pid: asNumberOrNull(worker.pid),
    functions: asArray<string>(worker.functions),
    state: asString(worker.state, 'unknown'),
    current_function: typeof worker.current_function === 'string' ? worker.current_function : null,
    current_resource: typeof worker.current_resource === 'string' ? worker.current_resource : null,
    event_loop_lag_ms: asNumberOrNull(worker.event_loop_lag_ms),
    gpu_admission_enabled: asBooleanOrNull(worker.gpu_admission_enabled),
    gpu_inflight: asNumberOrNull(worker.gpu_inflight),
    gpu_free_mb: asNumberOrNull(worker.gpu_free_mb),
    gpu_snapshot_source: typeof worker.gpu_snapshot_source === 'string' ? worker.gpu_snapshot_source : null,
    cuda_model_loaded: asBooleanOrNull(worker.cuda_model_loaded),
    cpu_fallback_model_loaded: asBooleanOrNull(worker.cpu_fallback_model_loaded),
  }
}

function normalizeResource(value: unknown): WorkerResourceSnapshot {
  const resource = isRecord(value) ? value : {}
  return {
    resource_type: asString(resource.resource_type, 'unknown'),
    inflight: asNumber(resource.inflight),
    limit: asNumber(resource.limit),
  }
}

function normalizeAction(value: unknown): WorkerActionItem {
  const action = isRecord(value) ? value : {}
  const severity = action.severity === 'critical' || action.severity === 'warn' || action.severity === 'info'
    ? action.severity
    : 'info'
  return {
    code: asString(action.code, 'unknown'),
    title: asString(action.title, '待确认动作'),
    severity,
    owner: asString(action.owner),
    description: asString(action.description),
    command: asString(action.command),
    requires_supervisor: Boolean(action.requires_supervisor),
  }
}

export function normalizeWorkerHealthResponse(input: unknown): WorkerHealthResponse {
  const raw = isRecord(input) ? input : {}
  const reasons = isRecord(raw.reasons) ? raw.reasons : {}
  const thresholds = isRecord(raw.thresholds) ? raw.thresholds : {}

  return {
    health: normalizeHealth(raw.health),
    worker_count: asNumber(raw.worker_count),
    workers: asArray<unknown>(raw.workers).map(normalizeWorker),
    expected_functions: asArray<string>(raw.expected_functions),
    missing_functions: asArray<string>(raw.missing_functions),
    covered_functions: asArray<string>(raw.covered_functions),
    resources: asArray<unknown>(raw.resources).map(normalizeResource),
    reasons: {
      unhealthy: asArray<string>(reasons.unhealthy),
      degraded: asArray<string>(reasons.degraded),
    },
    thresholds: {
      event_loop_lag_warn_ms: asNumber(thresholds.event_loop_lag_warn_ms),
      gpu_probe_min_free_mb: asNumber(thresholds.gpu_probe_min_free_mb),
    },
    recommended_actions: asArray<string>(raw.recommended_actions),
    action_items: asArray<unknown>(raw.action_items).map(normalizeAction),
  }
}

function normalizeHotspot(value: unknown): HotspotItem {
  const item = isRecord(value) ? value : {}
  const source = isRecord(item.source) ? item.source : {}
  const related = isRecord(item.related) ? item.related : {}

  return {
    name: asString(item.name, 'unknown'),
    layer: asString(item.layer),
    pillar: asString(item.pillar),
    business: asString(item.business, 'all'),
    direction: asString(item.direction),
    kind: asString(item.kind),
    description: asString(item.description),
    verdict: asString(item.verdict, 'neutral'),
    algorithm: asString(item.algorithm),
    reason: asString(item.reason),
    score: asNumber(item.score),
    change_points: asArray<{ index: number; direction: string }>(item.change_points),
    detail: isRecord(item.detail) ? item.detail : {},
    samples: asNumber(item.samples),
    source: {
      module: asString(source.module),
      qualname: asString(source.qualname),
      file: asString(source.file),
      line: asNumber(source.line),
    },
    related: {
      llm_execution_trace: asArray<unknown>(related.llm_execution_trace).map(normalizeLlmTrace),
      system_error_log: asArray<unknown>(related.system_error_log).map(normalizeSystemErrorLog),
      prompt_version_changes: asArray<unknown>(related.prompt_version_changes).map(normalizePromptVersionChange),
    },
  }
}

export function normalizeHotspotsResponse(input: unknown): HotspotsResponse {
  const raw = isRecord(input) ? input : {}
  return {
    window: asString(raw.window),
    generated_at: asString(raw.generated_at),
    total: asNumber(raw.total),
    items: asArray<unknown>(raw.items).map(normalizeHotspot),
  }
}

type HotspotTraceItem = HotspotItem['related']['llm_execution_trace'][number]
type HotspotErrorItem = HotspotItem['related']['system_error_log'][number]
type HotspotPromptItem = HotspotItem['related']['prompt_version_changes'][number]

function normalizeLlmTrace(value: unknown): HotspotTraceItem {
  const trace = isRecord(value) ? value : {}
  return {
    id: asNumber(trace.id),
    created_at: typeof trace.created_at === 'string' ? trace.created_at : null,
    project_id: asNumberOrNull(trace.project_id),
  }
}

function normalizeSystemErrorLog(value: unknown): HotspotErrorItem {
  const log = isRecord(value) ? value : {}
  return {
    id: asNumber(log.id),
    created_at: typeof log.created_at === 'string' ? log.created_at : null,
    level: asString(log.level),
    module: typeof log.module === 'string' ? log.module : null,
    message: asString(log.message),
  }
}

function normalizePromptVersionChange(value: unknown): HotspotPromptItem {
  const change = isRecord(value) ? value : {}
  return {
    id: asNumber(change.id),
    purpose: asString(change.purpose),
    version: asNumber(change.version),
    name: typeof change.name === 'string' ? change.name : null,
    is_active: Boolean(change.is_active),
    updated_at: typeof change.updated_at === 'string' ? change.updated_at : null,
  }
}

function normalizeFunnelItem(value: unknown): FlowFunnelItem {
  const item = isRecord(value) ? value : {}
  return {
    task_type: asString(item.task_type),
    started: asNumber(item.started),
    ended: asNumber(item.ended),
    success: asNumber(item.success),
    failure: asNumber(item.failure),
    other: asNumber(item.other),
    success_ratio: asNumber(item.success_ratio),
    avg_duration_ms: asNumber(item.avg_duration_ms),
  }
}

function normalizeOutcomeDistribution(value: unknown): Record<string, number> {
  return normalizeNumberRecord(value)
}

function normalizeTimeSeriesPoint(value: unknown): FlowFunnelResponse['time_series'][number] {
  const point = isRecord(value) ? value : {}
  return {
    ts: asString(point.ts),
    counts: normalizeNumberRecord(point.counts),
    total: asNumber(point.total),
  }
}

export function normalizeFlowFunnelResponse(input: unknown): FlowFunnelResponse {
  const raw = isRecord(input) ? input : {}
  return {
    flow: asString(raw.flow),
    window: asString(raw.window),
    window_start: asString(raw.window_start),
    generated_at: asString(raw.generated_at),
    funnel: asArray<unknown>(raw.funnel).map(normalizeFunnelItem),
    outcome_distribution: normalizeOutcomeDistribution(raw.outcome_distribution),
    time_series: asArray<unknown>(raw.time_series).map(normalizeTimeSeriesPoint),
  }
}
