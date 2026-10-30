/**
 * 职责: 业务域 Hub 有数据态紧凑工具条（标题、统计条+动作指引、新建/继续 CTA）
 * 不做: 不渲染营销说明文案、不请求数据、不管理列表筛选、不渲染项目列表
 */
import React from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AlertCircle,
  ArrowRight,
  CheckCircle,
  ClipboardCheck,
  FolderOpen,
  Loader,
  PenTool,
  Plus,
  ShieldAlert,
  Upload,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { Project } from '@/stores/projectStore'
import type { ProjectListStats } from '../projectListDerivations'
import type { StatusFilter } from '../types'
import { getTargetRoute } from '../types'
import {
  HUB_METRIC_TONE_CLASS,
  resolveHubMetricValue,
  type BusinessHubConfig,
  type BusinessHubIconName,
  type BusinessHubMetricIconName,
} from './businessHubConfig'

interface BusinessHubHeroProps {
  config: BusinessHubConfig
  stats: ProjectListStats
  recentProject?: Project | null
  onCreateProject: () => void
  onStatClick: (filter: StatusFilter) => void
}

const HUB_ICONS: Record<BusinessHubIconName, React.ElementType> = {
  PenTool,
  ClipboardCheck,
  ShieldAlert,
}

const METRIC_ICONS: Record<BusinessHubMetricIconName, React.ElementType> = {
  FolderOpen,
  Upload,
  Loader,
  CheckCircle,
  AlertCircle,
}

const BusinessHubHero: React.FC<BusinessHubHeroProps> = ({
  config,
  stats,
  recentProject,
  onCreateProject,
  onStatClick,
}) => {
  const navigate = useNavigate()
  const Icon = HUB_ICONS[config.iconName]
  const { theme } = config

  return (
    <section className={`relative overflow-hidden rounded-2xl border shadow-xs ${theme.cardClass}`}>
      <div className={`pointer-events-none absolute inset-0 ${theme.glowClass}`} />
      <div className="relative flex flex-col gap-2.5 p-3.5 sm:p-4">
        <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-center gap-2.5">
            <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl shadow-xs ${theme.iconWrapClass}`}>
              <Icon className={`h-4 w-4 ${theme.iconClass}`} />
            </div>
            <div className="min-w-0">
              <h1 className="text-lg font-semibold tracking-tight text-slate-900 sm:text-xl">
                {config.title}
              </h1>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 sm:justify-end">
            {recentProject && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                title={recentProject.name}
                className="max-w-56 border-slate-200 bg-white/80 text-slate-700 hover:bg-white sm:max-w-[18rem]"
                onClick={() => navigate(getTargetRoute(recentProject))}
              >
                <span className="min-w-0 truncate">
                  继续“{recentProject.name}”
                </span>
                <ArrowRight className="h-4 w-4 shrink-0" />
              </Button>
            )}
            <Button
              type="button"
              size="sm"
              className={theme.primaryButtonClass}
              onClick={onCreateProject}
            >
              <Plus className="h-4 w-4" />
              新建项目
            </Button>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          {config.metrics.map((metric) => {
            const value = resolveHubMetricValue(metric.key, stats)
            const toneClass = HUB_METRIC_TONE_CLASS[metric.tone]
            const MetricIcon = METRIC_ICONS[metric.iconName]
            return (
              <button
                key={metric.key}
                type="button"
                onClick={() => onStatClick(metric.statusFilter)}
                className={`group flex items-center gap-2.5 rounded-xl border px-2.5 py-2 text-left shadow-xs transition ${toneClass.hover}`}
              >
                <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${toneClass.iconWrap}`}>
                  <MetricIcon className={`h-3.5 w-3.5 ${toneClass.icon}`} />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-lg font-semibold tabular-nums leading-none text-slate-900">
                    {value}
                  </p>
                  <p className="mt-0.5 whitespace-nowrap text-xs text-slate-500">{metric.label}</p>
                </div>
                <span className={`hidden shrink-0 items-center gap-0.5 text-[11px] font-medium transition group-hover:translate-x-0.5 lg:inline-flex ${toneClass.action}`}>
                  {metric.actionLabel}
                  <ArrowRight className="h-3 w-3" />
                </span>
              </button>
            )
          })}
        </div>
      </div>
    </section>
  )
}

export default BusinessHubHero
