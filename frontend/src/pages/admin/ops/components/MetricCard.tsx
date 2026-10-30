import React from 'react'
import HealthBadge, { VerdictType } from './HealthBadge'
import type { OpsMetricItem } from '@/services/opsApi'
import {
  formatMetricReason,
  formatMetricSubtitle,
  formatMetricTitle,
  shouldHideMetricValue,
} from '../labels'

interface Props {
  metric: OpsMetricItem
  onClick?: () => void
  muted?: boolean
}

const PILLAR_COLORS: Record<string, string> = {
  quality: 'border-l-indigo-400',
  reliability: 'border-l-emerald-400',
  efficiency: 'border-l-sky-400',
  cost: 'border-l-amber-400',
}

function formatValue(kind: string, value: number): string {
  if (Number.isNaN(value)) return '-'
  if (kind === 'gauge' && Math.abs(value) <= 1) {
    return `${(value * 100).toFixed(2)}%`
  }
  if (Math.abs(value) >= 10000) {
    return `${(value / 1000).toFixed(1)}k`
  }
  if (Number.isInteger(value)) {
    return String(value)
  }
  return value.toFixed(3)
}

function formatDelta(deltaPct: number): string {
  if (!Number.isFinite(deltaPct) || deltaPct === 0) return '—'
  const sign = deltaPct > 0 ? '+' : ''
  return `${sign}${deltaPct.toFixed(1)}%`
}

function renderMetricValue(metric: OpsMetricItem): string {
  if (shouldHideMetricValue(metric.name, metric.reason)) {
    return '—'
  }
  return formatValue(metric.kind, metric.value)
}

interface CriticCounterStats {
  total?: number
  agree?: number
  disagree?: number
  need_review?: number
  inconclusive?: number
  skipped?: number
  disagree_rate?: number
  inconclusive_rate?: number
}

interface CriticFlowBreakdown {
  flow?: string
  label?: string
  judge?: CriticCounterStats
  cove?: CriticCounterStats
}

function isCriticFlowBreakdown(value: unknown): value is CriticFlowBreakdown {
  return Boolean(value && typeof value === 'object' && ('judge' in value || 'cove' in value))
}

function formatRate(value?: number): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${(value * 100).toFixed(1)}%`
}

function renderCriticFlowBreakdown(metric: OpsMetricItem): React.ReactNode {
  if (!metric.name.startsWith('critic_flow_health_') || !isCriticFlowBreakdown(metric.breakdown)) {
    return null
  }

  const judge = metric.breakdown.judge || {}
  const cove = metric.breakdown.cove || {}

  return (
    <div className="mt-3 rounded-md bg-gray-50 border border-gray-100 p-2 space-y-1.5">
      <div className="flex items-center justify-between text-[11px] text-gray-600">
        <span className="font-medium">Judge</span>
        <span className="tabular-nums">
          total {judge.total ?? 0} · disagree {judge.disagree ?? 0} · inconclusive {judge.inconclusive ?? 0}
        </span>
      </div>
      <div className="flex items-center justify-between text-[11px] text-gray-500">
        <span>Judge rate</span>
        <span className="tabular-nums">
          disagree {formatRate(judge.disagree_rate)} / inconclusive {formatRate(judge.inconclusive_rate)}
        </span>
      </div>
      <div className="flex items-center justify-between text-[11px] text-gray-600">
        <span className="font-medium">CoVe</span>
        <span className="tabular-nums">
          total {cove.total ?? 0} · disagree {cove.disagree ?? 0} · inconclusive {cove.inconclusive ?? 0} · skipped {cove.skipped ?? 0}
        </span>
      </div>
      <div className="flex items-center justify-between text-[11px] text-gray-500">
        <span>CoVe rate</span>
        <span className="tabular-nums">
          disagree {formatRate(cove.disagree_rate)} / inconclusive {formatRate(cove.inconclusive_rate)}
        </span>
      </div>
    </div>
  )
}

const MetricCard: React.FC<Props> = ({ metric, onClick, muted }) => {
  const pillarBorder = PILLAR_COLORS[metric.pillar] || 'border-l-gray-300'
  const title = formatMetricTitle(metric.name, metric.description)
  const subtitle = formatMetricSubtitle(metric.name, metric.description)
  const displayReason = formatMetricReason(metric.name, metric.reason)
  const deltaColor =
    metric.verdict === 'critical'
      ? 'text-rose-600'
      : metric.verdict === 'warn'
      ? 'text-amber-600'
      : metric.verdict === 'improving'
      ? 'text-emerald-600'
      : 'text-gray-400'

  return (
    <button
      onClick={onClick}
      className={`group w-full text-left bg-white rounded-lg border border-gray-200 border-l-4 ${pillarBorder} p-4 shadow-xs hover:shadow-md hover:border-gray-300 transition-all ${muted ? 'opacity-60' : ''}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-gray-900 truncate" title={title}>
            {title}
          </div>
          {subtitle ? (
            <div className="mt-1 text-xs text-gray-500 truncate" title={subtitle}>
              {subtitle}
            </div>
          ) : null}
        </div>
        <HealthBadge verdict={metric.verdict as VerdictType} size="sm" />
      </div>

      <div className="mt-3 flex items-end justify-between">
        <div className="text-2xl font-semibold text-gray-900 tabular-nums">
          {renderMetricValue(metric)}
        </div>
        <div className={`text-xs font-medium tabular-nums ${deltaColor}`}>{formatDelta(metric.delta_pct)}</div>
      </div>

      {displayReason ? (
        <div className="mt-2 text-[11px] text-gray-400 truncate" title={displayReason}>
          {displayReason}
        </div>
      ) : null}

      {renderCriticFlowBreakdown(metric)}

      {metric.breakdown && Array.isArray(metric.breakdown) && metric.breakdown.length > 0 ? (
        <div className="mt-2 space-y-0.5">
          {(metric.breakdown as Array<{ module?: string; count?: number; label?: string; value?: number }>)
            .slice(0, 3)
            .map((row, idx) => (
              <div key={idx} className="flex items-center justify-between text-[11px] text-gray-500">
                <span className="truncate pr-2">{row.module || row.label || JSON.stringify(row)}</span>
                <span className="tabular-nums">{row.count ?? row.value ?? ''}</span>
              </div>
            ))}
        </div>
      ) : null}
    </button>
  )
}

export default MetricCard
