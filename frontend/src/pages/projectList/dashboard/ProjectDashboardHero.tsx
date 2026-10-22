import React from 'react'
import { RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { ProjectDashboardData } from '@/services/projectDashboardApi'
import ProjectDashboardActionQueue from './ProjectDashboardActionQueue'
import type { DashboardFilterPatch } from './projectDashboardFilters'

interface ProjectDashboardHeroProps {
  data: ProjectDashboardData
  isFetching: boolean
  onRefresh: () => void
  onSelect: (patch: DashboardFilterPatch) => void
}

const ProjectDashboardHero: React.FC<ProjectDashboardHeroProps> = ({ data, isFetching, onRefresh, onSelect }) => {
  const generatedAt = new Date(data.generatedAt)
  const generatedText = Number.isNaN(generatedAt.getTime())
    ? '未知时间'
    : generatedAt.toLocaleString('zh-CN')

  return (
    <section className="h-full overflow-hidden rounded-2xl border border-cyan-200/40 bg-slate-950 text-white shadow-[0_22px_60px_rgba(15,23,42,0.22)]">
      <div className="relative flex min-h-[260px] flex-col justify-between px-5 py-4 sm:px-6">
        <div className="absolute inset-0 bg-[linear-gradient(135deg,rgba(15,23,42,0.95),rgba(15,23,42,0.82)),radial-gradient(circle_at_18%_18%,rgba(37,99,235,0.55),transparent_32%),radial-gradient(circle_at_85%_20%,rgba(20,184,166,0.35),transparent_28%),radial-gradient(circle_at_70%_90%,rgba(139,92,246,0.25),transparent_34%)]" />
        <div className="absolute inset-0 opacity-[0.16] [background-image:linear-gradient(rgba(255,255,255,.22)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,.22)_1px,transparent_1px)] [background-size:28px_28px]" />
        <div className="relative flex flex-col gap-4">
          <div>
            <h2 className="text-xl font-semibold tracking-tight sm:text-2xl">运行总览</h2>
            <p className="mt-1 text-xs text-cyan-100/70">规模与健康</p>
          </div>
          <MetricStrip data={data} onSelect={onSelect} />
          <ProjectDashboardActionQueue items={data.actionQueue} onSelect={onSelect} />
        </div>
        <div className="relative mt-3 flex items-center justify-between gap-3 border-t border-white/10 pt-3 text-xs text-cyan-50/75">
          <span>更新：{generatedText}</span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={onRefresh}
            disabled={isFetching}
            className="h-7 border-cyan-100/20 bg-white/[0.04] px-2 text-xs text-cyan-50 hover:bg-white/[0.10]"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? 'animate-spin' : ''}`} />
            刷新
          </Button>
        </div>
      </div>
    </section>
  )
}

interface MetricProps {
  label: string
  value: number
  danger?: boolean
  primary?: boolean
  onClick: () => void
}

const MetricStrip: React.FC<{
  data: ProjectDashboardData
  onSelect: (patch: DashboardFilterPatch) => void
}> = ({ data, onSelect }) => (
  <div className="grid grid-cols-2 gap-2 rounded-2xl border border-cyan-300/20 bg-white/[0.07] p-2 shadow-[inset_0_1px_0_rgba(255,255,255,.12)] backdrop-blur sm:grid-cols-5">
    <SecondaryMetric primary label="全部项目" value={data.summary.total} onClick={() => onSelect({ clear: true })} />
    <SecondaryMetric label="待处理" value={data.summary.pending} onClick={() => onSelect({ status: 'pending' })} />
    <SecondaryMetric label="处理中" value={data.summary.processing} onClick={() => onSelect({ status: 'processing' })} />
    <SecondaryMetric label="已完成" value={data.summary.completed} onClick={() => onSelect({ status: 'completed' })} />
    <SecondaryMetric label="失败" value={data.summary.failed} danger onClick={() => onSelect({ status: 'failed' })} />
  </div>
)

const SecondaryMetric: React.FC<MetricProps> = ({ label, value, danger = false, primary = false, onClick }) => (
  <button
    type="button"
    onClick={onClick}
    className={`rounded-xl px-3 py-2 text-left transition hover:bg-white/[0.10] focus:outline-none focus:ring-2 focus:ring-cyan-200/50 ${primary ? 'bg-cyan-300/10' : 'bg-white/[0.04]'}`}
  >
    <p className={`${primary ? 'text-3xl' : 'text-2xl'} font-semibold leading-none tabular-nums ${danger && value > 0 ? 'text-red-300' : 'text-cyan-100'}`}>
      {value}
    </p>
    <p className={`${primary ? 'mt-1.5 text-cyan-100/70' : 'mt-1 text-slate-400'} text-xs`}>{label}</p>
  </button>
)

export default ProjectDashboardHero
