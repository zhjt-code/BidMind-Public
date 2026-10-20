import React, { useEffect, useRef } from 'react'
import type { ProjectDashboardProjectFilter } from './projectDashboardFilters'
import { PROJECT_TYPE_STYLE, STATUS_GROUP_STYLE } from './projectDashboardConfig'
import { getStatusConfig } from '../types'

interface ProjectDashboardFilterBarProps {
  filter: ProjectDashboardProjectFilter
  onClear: () => void
}

const ProjectDashboardFilterBar: React.FC<ProjectDashboardFilterBarProps> = ({ filter, onClear }) => {
  const ref = useRef<HTMLButtonElement | null>(null)
  const stageKey = filter.exactStatuses.join(',')

  useEffect(() => {
    if (filter.hasDashboardFilter) {
      ref.current?.focus()
    }
  }, [filter.hasDashboardFilter, filter.activeTab, filter.statusFilter, stageKey, filter.staleHours, filter.createdDate])

  if (!filter.hasDashboardFilter) return null

  const stageLabel = filter.exactStatuses
    .map((status) => getStatusConfig(status).label)
    .join('、')

  const labels = [
    filter.activeTab !== 'all'
      ? PROJECT_TYPE_STYLE[filter.activeTab as keyof typeof PROJECT_TYPE_STYLE]?.label
      : null,
    filter.statusFilter !== 'all'
      ? STATUS_GROUP_STYLE[filter.statusFilter as keyof typeof STATUS_GROUP_STYLE]?.label
      : null,
    stageLabel || null,
    filter.staleHours ? `超过${filter.staleHours}小时未推进` : null,
    filter.createdDate ? `创建日期：${filter.createdDate}` : null,
  ].filter(Boolean)

  return (
    <button
      type="button"
      ref={ref}
      onClick={onClear}
      className="inline-flex h-8 max-w-full items-center gap-2 rounded-md border border-blue-200 bg-blue-50 px-3 text-sm text-blue-800 outline-none transition hover:bg-blue-100 focus:ring-2 focus:ring-blue-300"
      title="取消当前筛选"
    >
      <span className="truncate">{labels.join(' / ')}</span>
      <span className="shrink-0 text-xs text-blue-600">取消筛选</span>
    </button>
  )
}

export default ProjectDashboardFilterBar
