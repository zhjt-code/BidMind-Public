import type { VerdictType } from './components/HealthBadge'
import type { FlowFunnelItem, OpsMetricItem, WorkerHealthLevel } from '@/services/opsApi'

export type OpsOverviewPane = 'duty' | 'lab'

export const OPS_OVERVIEW_PANE_KEY = 'ops-overview-pane'

export interface FlowHealthSummary {
  started: number
  failed: number
  ended: number
  success: number
  successRatio: number | null
  verdict: VerdictType
  label: '无流量' | '恶化' | '预警' | '平稳'
}

export interface DutyKpiStatus {
  visible: boolean
  verdict: VerdictType
  label: string
  valueText: string
}

const VERDICT_LABEL: Record<VerdictType, string> = {
  improving: '向好',
  stable: '平稳',
  warn: '预警',
  critical: '恶化',
  neutral: '中性',
}

function verdictLabel(verdict: VerdictType): string {
  return VERDICT_LABEL[verdict]
}

export function readOverviewPane(storage?: Pick<Storage, 'getItem'> | null): OpsOverviewPane {
  try {
    const raw = storage?.getItem(OPS_OVERVIEW_PANE_KEY)
    return raw === 'lab' ? 'lab' : 'duty'
  } catch {
    return 'duty'
  }
}

export function writeOverviewPane(
  pane: OpsOverviewPane,
  storage?: Pick<Storage, 'setItem'> | null,
): void {
  try {
    storage?.setItem(OPS_OVERVIEW_PANE_KEY, pane)
  } catch {
    // sessionStorage 不可用时保持调用方内存态
  }
}

export function summarizeFlowFunnel(
  funnel: Pick<FlowFunnelItem, 'started' | 'failure' | 'ended' | 'success'>[],
): FlowHealthSummary {
  const started = funnel.reduce((n, row) => n + row.started, 0)
  const failed = funnel.reduce((n, row) => n + row.failure, 0)
  const ended = funnel.reduce((n, row) => n + row.ended, 0)
  const success = funnel.reduce((n, row) => n + row.success, 0)
  const successRatio = ended === 0 ? null : success / ended
  if (started === 0 && failed === 0) {
    return { started, failed, ended, success, successRatio, verdict: 'neutral', label: '无流量' }
  }
  if (failed >= 3) {
    return { started, failed, ended, success, successRatio, verdict: 'critical', label: '恶化' }
  }
  if (failed >= 1) {
    return { started, failed, ended, success, successRatio, verdict: 'warn', label: '预警' }
  }
  return { started, failed, ended, success, successRatio, verdict: 'stable', label: '平稳' }
}

export function findLayerMetric(
  layers: Record<string, OpsMetricItem[]>,
  layer: string,
  name: string,
): OpsMetricItem | undefined {
  return (layers[layer] || []).find((item) => item.name === name)
}

export function unresolvedErrorKpi(
  metric?: Pick<OpsMetricItem, 'value' | 'verdict'>,
): DutyKpiStatus {
  if (!metric) return { visible: false, verdict: 'neutral', label: '中性', valueText: '' }
  if (metric.value === 0) {
    return { visible: true, verdict: 'stable', label: '平稳', valueText: '0' }
  }
  return {
    visible: true,
    verdict: metric.verdict,
    label: verdictLabel(metric.verdict),
    valueText: String(metric.value),
  }
}

export function failedTaskKpi(
  metric?: Pick<OpsMetricItem, 'value' | 'verdict'>,
): DutyKpiStatus {
  return unresolvedErrorKpi(metric)
}

export function workerKpi(
  health: WorkerHealthLevel | undefined,
  requestFailed: boolean,
): DutyKpiStatus {
  if (requestFailed) {
    return { visible: true, verdict: 'critical', label: '加载失败', valueText: '—' }
  }
  if (health === 'healthy') {
    return { visible: true, verdict: 'stable', label: '平稳', valueText: '健康' }
  }
  if (health === 'degraded') {
    return { visible: true, verdict: 'warn', label: '预警', valueText: '降级' }
  }
  if (health === 'unhealthy' || health === 'offline') {
    return { visible: true, verdict: 'critical', label: '恶化', valueText: health === 'offline' ? '离线' : '异常' }
  }
  return { visible: true, verdict: 'neutral', label: '中性', valueText: '—' }
}

export function llmFinalValidKpi(
  logicalCalls: number | undefined,
  finalValidRate: number | undefined,
  requestFailed = false,
): DutyKpiStatus {
  if (requestFailed) {
    return { visible: true, verdict: 'critical', label: '加载失败', valueText: '—' }
  }
  if (!logicalCalls) {
    return { visible: true, verdict: 'neutral', label: '无样本', valueText: '—' }
  }
  const rate = finalValidRate ?? 0
  const valueText = `${(rate * 100).toFixed(1)}%`
  if (rate >= 0.95) {
    return { visible: true, verdict: 'stable', label: '平稳', valueText }
  }
  return { visible: true, verdict: 'critical', label: '恶化', valueText }
}

export function isLayerCardMuted(metric: Pick<OpsMetricItem, 'verdict' | 'reason'>): boolean {
  return metric.verdict === 'neutral' || metric.reason.includes('暂无')
}

export function formatSuccessRatio(ratio: number | null): string {
  if (ratio === null) return '—'
  return `${(ratio * 100).toFixed(1)}%`
}
