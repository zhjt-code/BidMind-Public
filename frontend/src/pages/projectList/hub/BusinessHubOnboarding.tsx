/**
 * 职责: 业务域 Hub 空态引导（能力说明、流程步骤、新建 CTA）
 * 不做: 不渲染项目列表、不管理筛选状态、不请求后端数据
 */
import React from 'react'
import { ChevronDown, ChevronRight, ClipboardCheck, PenTool, Plus, ShieldAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  type BusinessHubConfig,
  type BusinessHubIconName,
} from './businessHubConfig'

interface BusinessHubOnboardingProps {
  config: BusinessHubConfig
  onCreateProject: () => void
}

const HUB_ICONS: Record<BusinessHubIconName, React.ElementType> = {
  PenTool,
  ClipboardCheck,
  ShieldAlert,
}

const BusinessHubOnboarding: React.FC<BusinessHubOnboardingProps> = ({
  config,
  onCreateProject,
}) => {
  const Icon = HUB_ICONS[config.iconName]
  const { theme } = config

  return (
    <section className={`relative overflow-hidden rounded-2xl border shadow-sm ${theme.cardClass}`}>
      <div className={`pointer-events-none absolute inset-0 ${theme.glowClass}`} />
      <div className="relative space-y-6 p-5 sm:p-6">
        <div className="flex items-start gap-3">
          <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl shadow-sm ${theme.iconWrapClass}`}>
            <Icon className={`h-5 w-5 ${theme.iconClass}`} />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-slate-500">开始第一个{config.title}项目</p>
            <h1 className="mt-1 text-xl font-semibold tracking-tight text-slate-900 sm:text-2xl">
              {config.title}
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">
              {config.tagline}
            </p>
          </div>
        </div>

        <div className="rounded-xl border border-white/70 bg-white/70 p-4 shadow-sm backdrop-blur-sm">
          <p className="mb-3 text-xs font-medium tracking-wide text-slate-500">
            {config.flowSteps.length} 步快速上手
          </p>
          <div className="flex flex-col lg:flex-row lg:items-stretch">
            {config.flowSteps.map((step, index) => (
              <React.Fragment key={step.label}>
                {index > 0 && (
                  <>
                    <ChevronDown
                      aria-hidden
                      className={`mx-auto my-1 h-4 w-4 shrink-0 lg:hidden ${theme.stepConnectorClass}`}
                    />
                    <ChevronRight
                      aria-hidden
                      className={`mx-1 hidden h-4 w-4 shrink-0 self-center lg:block ${theme.stepConnectorClass}`}
                    />
                  </>
                )}
                <div
                  className={`flex min-w-0 flex-1 items-center gap-3 rounded-xl border px-3 py-2 shadow-sm ${theme.stepCardClass}`}
                >
                  <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${theme.stepDotClass}`}>
                    {index + 1}
                  </div>
                  <p className="text-sm font-medium text-slate-800">{step.label}</p>
                </div>
              </React.Fragment>
            ))}
          </div>
        </div>

        <Button
          type="button"
          className={theme.primaryButtonClass}
          onClick={onCreateProject}
        >
          <Plus className="h-4 w-4" />
          新建{config.title}项目
        </Button>
      </div>
    </section>
  )
}

export default BusinessHubOnboarding
