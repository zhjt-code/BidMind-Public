import React from 'react'
import { useNavigate } from 'react-router-dom'
import { Activity } from 'lucide-react'
import type { ProjectDashboardRecentProject } from '@/services/projectDashboardApi'
import { getStatusConfig, getTargetRoute } from '../types'
import { PROJECT_TYPE_STYLE } from './projectDashboardConfig'

interface RecentProjectsPanelProps {
  projects: ProjectDashboardRecentProject[]
}

const RecentProjectsPanel: React.FC<RecentProjectsPanelProps> = ({ projects }) => {
  const navigate = useNavigate()

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center gap-2">
        <Activity className="h-4 w-4 text-cyan-600" />
        <h3 className="text-sm font-semibold text-gray-900">最近更新</h3>
      </div>
      {projects.length === 0 ? (
        <div className="rounded-lg bg-gray-50 py-8 text-center text-sm text-gray-400">暂无最近更新项目</div>
      ) : (
        <div className="space-y-1 border-l border-slate-200 pl-3">
          {projects.map((project) => {
            const typeStyle = PROJECT_TYPE_STYLE[project.projectType as keyof typeof PROJECT_TYPE_STYLE]
            const statusConfig = getStatusConfig(project.status, project.projectType)
            return (
              <button
                key={project.id}
                type="button"
                onClick={() => navigate(getTargetRoute(project))}
                className="relative w-full rounded-lg px-3 py-2 text-left transition hover:bg-blue-50/50"
              >
                <span className="absolute -left-[17px] top-4 h-2 w-2 rounded-full border border-white bg-cyan-500 shadow-[0_0_0_3px_rgba(6,182,212,.16)]" />
                <div className="flex items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="truncate text-sm font-medium text-gray-900">{project.name}</span>
                    <span className="shrink-0 whitespace-nowrap rounded-full border border-cyan-100 bg-cyan-50 px-2 py-0.5 text-[11px] text-cyan-700">
                      {statusConfig.label}
                    </span>
                  </div>
                  <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] ${typeStyle?.softClass ?? 'bg-gray-50 text-gray-600 border-gray-200'}`}>
                    {typeStyle?.label ?? '项目'}
                  </span>
                </div>
                <p className="mt-1 text-xs text-gray-400">
                  {formatRelativeTime(project.updatedAt)}
                </p>
              </button>
            )
          })}
        </div>
      )}
    </section>
  )
}

function formatRelativeTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '未知时间'
  const minutes = Math.round((Date.now() - date.getTime()) / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.round(hours / 24)
  if (days < 30) return `${days} 天前`
  return date.toLocaleString('zh-CN')
}

export default RecentProjectsPanel
