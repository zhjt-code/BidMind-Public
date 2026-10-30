import React from 'react'
import { Cloud, Cpu, KeyRound, ScrollText, Server, ShieldCheck, UserPlus, Users } from 'lucide-react'
import { publicHomeContent, type PublicDeploymentPathKey } from '@/content/publicHome'

/**
 * 职责: 公开首页数据与部署区，按统一卡片展示三条落地路径与共用能力。
 * 不做: 不承诺未核验安全资质，不链到仓库内部署文档，不在本区放注册 CTA。
 */

interface TrustDimension {
  label: string
  icon: React.ElementType
  iconBox: string
}

const TRUST_DIMENSIONS: TrustDimension[] = [
  { label: '身份认证', icon: KeyRound, iconBox: 'border-indigo-100 bg-indigo-50 text-indigo-600' },
  { label: '项目访问', icon: Users, iconBox: 'border-blue-100 bg-blue-50 text-blue-600' },
  { label: '部署方式', icon: Server, iconBox: 'border-teal-100 bg-teal-50 text-teal-600' },
  { label: 'AI 模式', icon: Cpu, iconBox: 'border-violet-100 bg-violet-50 text-violet-600' },
  { label: '传输保护', icon: ShieldCheck, iconBox: 'border-cyan-100 bg-cyan-50 text-cyan-600' },
  { label: '操作记录', icon: ScrollText, iconBox: 'border-orange-100 bg-orange-50 text-orange-600' },
]

const DEPLOYMENT_PATH_TONES: Record<PublicDeploymentPathKey, { icon: React.ElementType; iconBox: string }> = {
  local: { icon: Server, iconBox: 'border-teal-100 bg-teal-50 text-teal-600' },
  cloud: { icon: Cloud, iconBox: 'border-indigo-100 bg-indigo-50 text-indigo-600' },
  hosted: { icon: UserPlus, iconBox: 'border-violet-100 bg-violet-50 text-violet-600' },
}

const TrustAndDataBoundary: React.FC = () => {
  return (
    <section id="trust" className="border-y border-slate-200 bg-white py-14 sm:py-16">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="max-w-3xl">
          <p className="text-sm font-semibold text-[#635BFF]">数据与部署</p>
          <h2 className="gradient-text mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">灵活适配不同的数据和部署要求</h2>
          <p className="mt-4 text-base leading-7 text-slate-600">{publicHomeContent.trustIntro}</p>
        </div>

        <div className="mt-10 rounded-4xl border border-slate-200 bg-white p-6 shadow-[0_20px_50px_rgba(10,37,64,0.08)] sm:p-8">
          <div className="grid gap-4 md:grid-cols-3">
            {publicHomeContent.deploymentPaths.map(item => {
              const tone = DEPLOYMENT_PATH_TONES[item.key]
              const Icon = tone.icon
              return (
                <article key={item.key} className="flex h-full flex-col rounded-2xl border border-slate-200 bg-white p-5">
                  <p className="text-sm font-semibold leading-6 text-[#0A2540]">{item.fit}</p>
                  <div className="mt-4 flex items-start gap-3">
                    <span className={`flex h-11 w-11 flex-none items-center justify-center rounded-xl border ${tone.iconBox}`}>
                      <Icon className="h-5 w-5" aria-hidden="true" />
                    </span>
                    <div className="min-w-0">
                      <p className="text-xs font-semibold tracking-[0.16em] text-[#635BFF]">适用</p>
                      <h3 className="mt-1.5 text-lg font-semibold text-[#0A2540]">{item.title}</h3>
                    </div>
                  </div>
                  <p className="mt-4 text-xs font-semibold tracking-[0.16em] text-[#635BFF]">数据边界</p>
                  <p className="mt-1.5 text-sm leading-6 text-[#425466]">{item.boundary}</p>
                  <p className="mt-4 text-xs font-semibold tracking-[0.16em] text-[#635BFF]">选择前须知</p>
                  <p className="mt-1.5 flex-1 text-sm leading-6 text-[#425466]">{item.notice}</p>
                </article>
              )
            })}
          </div>
        </div>

        <div className="mt-12">
          <p className="text-xs font-semibold tracking-[0.18em] text-[#635BFF]">三种路径都具备</p>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {publicHomeContent.trust.map((item, index) => {
              const dimension = TRUST_DIMENSIONS[index]
              const Icon = dimension.icon
              return (
                <div key={item} className="flex items-start gap-4 rounded-2xl border border-slate-200 bg-white p-4">
                  <span className={`flex h-11 w-11 flex-none items-center justify-center rounded-xl border ${dimension.iconBox}`}>
                    <Icon className="h-5 w-5" aria-hidden="true" />
                  </span>
                  <div className="min-w-0">
                    <p className="text-xs font-semibold tracking-[0.16em] text-[#635BFF]">{dimension.label}</p>
                    <p className="mt-1.5 text-sm font-semibold leading-6 text-[#0A2540]">{item}</p>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </section>
  )
}

export default TrustAndDataBoundary
