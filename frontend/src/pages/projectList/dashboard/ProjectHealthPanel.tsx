import React from 'react'
import type { ProjectDashboardHealth, ProjectTypeCompletionDuration } from '@/services/projectDashboardApi'
import { getProjectTypeColor } from './projectDashboardConfig'
import WindowDaysToggle from './WindowDaysToggle'

interface ProjectHealthPanelProps {
  health: ProjectDashboardHealth
  windowDays: number
  onWindowDaysChange: (days: number) => void
}

const HEALTH_GLASS_CARD =
  'relative rounded-xl border border-slate-200/40 bg-white/50 px-3 py-3 shadow-xs backdrop-blur-xs transition duration-200 ease-out hover:z-10 hover:-translate-y-1 hover:shadow-[0_10px_24px_rgba(15,23,42,0.14)]'

const ProjectHealthPanel: React.FC<ProjectHealthPanelProps> = ({ health, windowDays, onWindowDaysChange }) => (
  <section className="flex h-full flex-col rounded-2xl border border-slate-200 bg-white p-4 shadow-xs">
    <div className="mb-3 flex items-start justify-between gap-3">
      <h3 className="text-sm font-semibold text-gray-900">运行健康</h3>
      <WindowDaysToggle value={windowDays} onChange={onWindowDaysChange} />
    </div>
    <div className="flex flex-1 flex-col gap-3">
      <div className={HEALTH_GLASS_CARD}>
        <p className="text-xs text-slate-500">办结成功率</p>
        <p className="mt-1.5 text-2xl font-semibold leading-none tabular-nums text-slate-900">
          {formatRate(health.successRate)}
        </p>
        <p className="mt-2 text-[11px] text-slate-400">
          {health.successRate == null ? '窗口内还没有已上传文件的办结' : '已上传文件的已完成 /（已完成 + 失败）'}
        </p>
      </div>

      <div className={HEALTH_GLASS_CARD}>
        <p className="text-xs text-slate-500">单项目平均耗时</p>
        <ul className="mt-2 space-y-1.5">
          {health.completionByType.map((item) => (
            <DurationRow key={item.projectType} item={item} />
          ))}
        </ul>
      </div>

      <div className={HEALTH_GLASS_CARD}>
        <p className="text-xs text-slate-500">未办结项目</p>
        <p className="mt-1.5 text-2xl font-semibold leading-none tabular-nums text-slate-900">
          {health.backlog}
          <span className="ml-1 text-sm font-medium text-slate-500">个</span>
        </p>
        <p className="mt-2 text-[11px] text-slate-400">待处理与处理中</p>
        <p className="mt-0.5 text-[11px]">
          <NetChange value={health.netChange} windowDays={windowDays} />
        </p>
      </div>
    </div>
  </section>
)

const DurationRow: React.FC<{ item: ProjectTypeCompletionDuration }> = ({ item }) => (
  <li className="flex items-center justify-between gap-2">
    <span className="flex min-w-0 items-center gap-1.5 text-xs text-slate-700">
      <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: getProjectTypeColor(item.projectType) }} />
      <span className="truncate">{item.label}</span>
    </span>
    <span className={`shrink-0 text-sm font-semibold tabular-nums ${item.avgDurationMs == null ? 'text-slate-400' : 'text-slate-900'}`}>
      {formatTaskDuration(item.avgDurationMs)}
    </span>
  </li>
)

const NetChange: React.FC<{ value: number; windowDays: number }> = ({ value, windowDays }) => {
  const tone = value > 0 ? 'text-amber-700' : value < 0 ? 'text-emerald-700' : 'text-slate-500'
  const sign = value > 0 ? '+' : ''
  return <span className={tone}>近{windowDays}天净增 {sign}{value} 个</span>
}

function formatRate(rate: number | null): string {
  if (rate == null || Number.isNaN(rate)) return '—'
  return `${(rate * 100).toFixed(1)}%`
}

function formatTaskDuration(ms: number | null): string {
  if (ms == null || Number.isNaN(ms) || ms < 0) return '暂无'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60_000).toFixed(1)}min`
}

export default ProjectHealthPanel
