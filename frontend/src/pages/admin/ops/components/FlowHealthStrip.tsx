import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { FileSearch, PenLine, ShieldAlert } from 'lucide-react'
import { opsService, type FlowFunnelResponse } from '@/services/opsApi'
import { formatSuccessRatio, summarizeFlowFunnel } from '../dutyLogic'
import { FLOW_IDENTITY, verdictLabelClass } from '../dutyVisual'
import { OPS_BUSINESS_LABELS } from '../labels'
import type { VerdictType } from './HealthBadge'

const FLOWS: Array<{
  key: 'tender' | 'collusion' | 'writer'
  to: string
  Icon: typeof FileSearch
}> = [
  { key: 'tender', to: '/admin/ops/tender', Icon: FileSearch },
  { key: 'collusion', to: '/admin/ops/collusion', Icon: ShieldAlert },
  { key: 'writer', to: '/admin/ops/writer', Icon: PenLine },
]

interface Props {
  window: string
}

const FlowCard: React.FC<{ flow: (typeof FLOWS)[number]; window: string }> = ({ flow, window }) => {
  const { data, isError, isLoading } = useQuery({
    queryKey: ['flow-funnel', flow.key, window],
    queryFn: () => opsService.getFlowFunnel(flow.key, window),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })
  const summary = summarizeFlowFunnel((data as FlowFunnelResponse | undefined)?.funnel ?? [])
  const statusLabel = isError ? '加载失败' : isLoading ? '加载中…' : summary.label
  const statusVerdict: VerdictType = isError ? 'critical' : isLoading ? 'neutral' : summary.verdict
  const identity = FLOW_IDENTITY[flow.key]
  const Icon = flow.Icon

  return (
    <Link
      to={flow.to}
      className="block rounded-lg border border-gray-200 bg-white p-3 hover:border-gray-300"
    >
      <div className="flex items-start gap-3">
        <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border ${identity.well}`}>
          <Icon className={`h-[18px] w-[18px] ${identity.icon}`} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <div className="text-sm font-medium text-gray-800">{OPS_BUSINESS_LABELS[flow.key]}</div>
            <span className={`text-xs ${verdictLabelClass(statusVerdict)}`}>{statusLabel}</span>
          </div>
          <div className="mt-1 text-xs text-gray-500">
            启动 {summary.started} · 失败 {summary.failed} · 成功率 {formatSuccessRatio(summary.successRatio)}
          </div>
        </div>
      </div>
    </Link>
  )
}

const FlowHealthStrip: React.FC<Props> = ({ window }) => (
  <div className="bg-white rounded-lg border border-gray-200 p-4">
    <div className="text-sm font-medium text-gray-700 mb-3">业务链路</div>
    <div className="grid grid-cols-3 gap-4">
      {FLOWS.map((flow) => (
        <FlowCard key={flow.key} flow={flow} window={window} />
      ))}
    </div>
  </div>
)

export default FlowHealthStrip
