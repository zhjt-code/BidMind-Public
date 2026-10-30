import React from 'react'
import { AlertTriangle, ArrowRight, ClipboardCheck, Clock3, Upload } from 'lucide-react'
import type { ProjectDashboardActionFilter, ProjectDashboardActionItem } from '@/services/projectDashboardApi'
import type { DashboardFilterPatch } from './projectDashboardFilters'
import type { StatusFilter } from '../types'

interface ProjectDashboardActionQueueProps {
  items: ProjectDashboardActionItem[]
  onSelect: (patch: DashboardFilterPatch) => void
}

type ActionTone = ProjectDashboardActionItem['tone']

const TONE_CLASS: Record<ActionTone, { card: string; accent: string; icon: string; value: string; button: string }> = {
  red: {
    card: 'border-red-300/25 bg-red-300/8',
    accent: 'bg-red-300',
    icon: 'bg-red-300/15 text-red-200',
    value: 'text-red-200',
    button: 'text-red-100 group-hover:bg-red-300/10',
  },
  orange: {
    card: 'border-orange-300/25 bg-orange-300/8',
    accent: 'bg-orange-300',
    icon: 'bg-orange-300/15 text-orange-100',
    value: 'text-orange-100',
    button: 'text-orange-50 group-hover:bg-orange-300/10',
  },
  amber: {
    card: 'border-amber-300/25 bg-amber-300/8',
    accent: 'bg-amber-300',
    icon: 'bg-amber-300/15 text-amber-200',
    value: 'text-amber-200',
    button: 'text-amber-100 group-hover:bg-amber-300/10',
  },
  cyan: {
    card: 'border-cyan-200/20 bg-cyan-200/6',
    accent: 'bg-cyan-200',
    icon: 'bg-cyan-200/15 text-cyan-100',
    value: 'text-cyan-100',
    button: 'text-cyan-100 group-hover:bg-cyan-200/10',
  },
}

const ICONS: Record<string, React.ReactNode> = {
  failed: <AlertTriangle className="h-4 w-4" />,
  stalled: <Clock3 className="h-4 w-4" />,
  awaiting_confirm: <ClipboardCheck className="h-4 w-4" />,
  awaiting_upload: <Upload className="h-4 w-4" />,
}

const ProjectDashboardActionQueue: React.FC<ProjectDashboardActionQueueProps> = ({ items, onSelect }) => (
  <section className="rounded-2xl border border-white/10 bg-white/[0.07] p-3 shadow-[inset_0_1px_0_rgba(255,255,255,.10)] backdrop-blur-sm">
    <div className="mb-2 flex items-center justify-between gap-3">
      <h3 className="text-sm font-semibold text-white">需要立即处理</h3>
      <span className="text-[11px] font-medium text-cyan-100/80">点击定位列表</span>
    </div>
    <div className="grid gap-2 sm:grid-cols-2">
      {items.map((item) => {
        const toneClass = TONE_CLASS[item.tone]
        return (
          <button
            key={item.id}
            type="button"
            onClick={() => onSelect(toFilterPatch(item.filter))}
            className={`group relative overflow-hidden rounded-xl border px-3 py-2 text-left transition hover:-translate-y-0.5 hover:bg-white/10 hover:shadow-md focus:outline-hidden focus:ring-2 focus:ring-cyan-300/70 ${toneClass.card}`}
          >
            <span className={`absolute inset-y-0 left-0 w-1 ${toneClass.accent}`} />
            <div className="flex items-center justify-between gap-2 pl-1">
              <div className="flex min-w-0 items-center gap-2">
                <span className={`rounded-lg p-1.5 ${toneClass.icon}`}>{ICONS[item.id] ?? <Clock3 className="h-4 w-4" />}</span>
                <div className="min-w-0">
                  <p className={`text-2xl font-semibold leading-none tabular-nums ${toneClass.value}`}>{item.count}</p>
                  <p className="mt-1 truncate text-xs font-medium text-white">{item.label}</p>
                </div>
              </div>
              <div className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-1 text-xs font-medium ${toneClass.button}`}>
                {item.actionLabel}
                <ArrowRight className="h-3 w-3 transition group-hover:translate-x-0.5" />
              </div>
            </div>
          </button>
        )
      })}
    </div>
  </section>
)

function toFilterPatch(filter: ProjectDashboardActionFilter): DashboardFilterPatch {
  const patch: DashboardFilterPatch = {}
  if (isStatusFilter(filter.status)) patch.status = filter.status
  if (filter.stages.length > 0) patch.stages = filter.stages
  if (filter.staleHours) patch.staleHours = filter.staleHours
  return patch
}

function isStatusFilter(status: string | null): status is StatusFilter {
  return status === 'pending' || status === 'processing' || status === 'completed' || status === 'failed'
}

export default ProjectDashboardActionQueue
