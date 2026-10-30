import React from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { publicHomeContent } from '@/content/publicHome'

const PLATFORM_INPUTS = [
  { label: '写标项目', container: 'border-teal-200 bg-teal-50', marker: 'bg-[#08A6A6]' },
  { label: '审标项目', container: 'border-indigo-200 bg-indigo-50', marker: 'bg-[#635BFF]' },
  { label: '风险项目', container: 'border-orange-200 bg-orange-50', marker: 'bg-[#FF7A59]' },
] as const

const PLATFORM_OUTPUTS = [
  { label: 'Word 文档', container: 'border-teal-200 bg-teal-50', marker: 'bg-[#08A6A6]' },
  { label: 'PDF 报告', container: 'border-indigo-200 bg-indigo-50', marker: 'bg-[#635BFF]' },
  { label: '风险证据', container: 'border-orange-200 bg-orange-50', marker: 'bg-[#FF7A59]' },
] as const

const FlowConnector: React.FC = () => (
  <div className="flex items-center justify-center" aria-hidden="true">
    <div className="flex flex-col items-center lg:hidden">
      <span className="h-3 w-px bg-linear-to-b from-blue-200/20 to-indigo-400/80" />
      <span className="flex h-7 w-7 items-center justify-center rounded-full border border-indigo-200 bg-white text-[#635BFF] shadow-[0_6px_18px_rgba(99,91,255,0.22)]">
        <ChevronDown className="h-4 w-4" />
      </span>
      <span className="h-3 w-px bg-linear-to-b from-indigo-400/80 to-violet-200/20" />
    </div>
    <div className="hidden w-full items-center lg:flex">
      <span className="h-px flex-1 bg-linear-to-r from-blue-200/20 to-indigo-400/80" />
      <span className="flex h-7 w-7 flex-none items-center justify-center rounded-full border border-indigo-200 bg-white text-[#635BFF] shadow-[0_6px_18px_rgba(99,91,255,0.22)]">
        <ChevronRight className="h-4 w-4" />
      </span>
      <span className="h-px flex-1 bg-linear-to-r from-indigo-400/80 to-violet-200/20" />
    </div>
  </div>
)

const UnifiedPlatform: React.FC = () => {
  return (
    <section id="platform" className="stripe-grid-section relative overflow-hidden border-b border-slate-200 py-14 sm:py-16">
      <div className="relative mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="max-w-3xl">
          <p className="text-sm font-semibold text-[#635BFF]">统一证据底座 · 平台能力</p>
          <h2 className="gradient-text mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">
            三项能力分别运行，证据在平台内统一沉淀
          </h2>
          <p className="mt-5 text-base leading-7 text-[#425466]">
            一体化不等于一次上传自动完成三件事，同一账号、项目中心、知识库和任务状态服务于三类独立项目
          </p>
        </div>

        <div className="gradient-hairline-frame mt-12 rounded-4xl bg-white">
          <div className="rounded-[1.9rem] bg-white p-4 sm:p-6 lg:p-8">
            <div className="grid gap-5 lg:grid-cols-[minmax(0,0.78fr)_3rem_minmax(0,1.7fr)_3rem_minmax(0,0.78fr)] lg:items-center">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#425466]">三类独立项目</p>
                <div className="mt-4 space-y-3">
                  {PLATFORM_INPUTS.map(item => (
                    <div key={item.label} className={`flex min-h-16 items-center gap-3 rounded-2xl border px-4 ${item.container}`}>
                      <span className={`h-2.5 w-2.5 flex-none rounded-full ${item.marker}`} />
                      <span className="text-sm font-semibold text-[#0A2540]">{item.label}</span>
                    </div>
                  ))}
                </div>
              </div>

              <FlowConnector />

              <div className="rounded-[1.75rem] bg-[linear-gradient(135deg,#1677FF,#635BFF_62%,#8B5CF6)] p-5 text-white shadow-[0_24px_70px_rgba(99,91,255,0.24)] sm:p-6">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-white/70">共享平台核心</p>
                <h3 className="mt-2 text-2xl font-bold">统一证据与任务底座</h3>
                <div className="mt-5 grid gap-3 sm:grid-cols-2">
                  {publicHomeContent.platform.map(item => (
                    <div key={item.label} className="rounded-2xl border border-white/20 bg-white/10 p-4 backdrop-blur-xs">
                      <p className="text-sm font-semibold text-white">{item.label}</p>
                      <p className="mt-2 text-xs leading-5 text-white/75">{item.description}</p>
                    </div>
                  ))}
                </div>
              </div>

              <FlowConnector />

              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#425466]">三类交付结果</p>
                <div className="mt-4 space-y-3">
                  {PLATFORM_OUTPUTS.map(item => (
                    <div key={item.label} className={`flex min-h-16 items-center gap-3 rounded-2xl border px-4 ${item.container}`}>
                      <span className={`h-2.5 w-2.5 flex-none rounded-full ${item.marker}`} />
                      <span className="text-sm font-semibold text-[#0A2540]">{item.label}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

export default UnifiedPlatform
