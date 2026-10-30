import React from 'react'
import { publicHomeContent } from '@/content/publicHome'

/**
 * 职责: 在应用场景之后专场展示知识库如何沉淀稳定企业资料并供三类任务复用。
 * 不做: 不重复首屏「一个平台」收口、不请求真实知识库数据、不承诺资料自动生效。
 */

const LIBRARY_TONES = {
  enterprise: { container: 'border-teal-100 bg-teal-50', caption: 'text-teal-700' },
  standards: { container: 'border-indigo-100 bg-indigo-50', caption: 'text-indigo-700' },
  experience: { container: 'border-orange-100 bg-orange-50', caption: 'text-orange-700' },
} as const

const PlatformKnowledgeSection: React.FC = () => {
  const content = publicHomeContent.platformKnowledge

  return (
    <section id="knowledge" className="stripe-grid-section relative overflow-hidden border-y border-slate-200 py-14 sm:py-16">
      <div className="relative mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="max-w-3xl">
          <p className="text-sm font-semibold text-[#635BFF]">{content.eyebrow}</p>
          <h2 className="gradient-text mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">{content.title}</h2>
          <p className="mt-4 text-base leading-7 text-slate-600">{content.summary}</p>
        </div>

        <div className="gradient-hairline-frame mt-10 rounded-4xl bg-white">
          <div className="rounded-[1.9rem] bg-white p-6 sm:p-8">
            <div className="grid gap-8 lg:grid-cols-[0.92fr_1.08fr] lg:items-center">
              <div>
                <ul>
                  {content.deposits.map((item, index) => (
                    <li
                      key={item.label}
                      className={index === 0 ? 'pb-6' : 'border-t border-slate-200 py-6 last:pb-0'}
                    >
                      <p className="text-xs font-semibold tracking-[0.18em] text-[#635BFF]">
                        稳定资料 0{index + 1}
                      </p>
                      <h3 className="mt-2 text-2xl font-semibold text-[#0A2540]">{item.label}</h3>
                      <p className="mt-3 max-w-xl text-base leading-7 text-slate-600">{item.detail}</p>
                    </li>
                  ))}
                </ul>
              </div>

              <div className="flex h-full min-h-128 flex-col rounded-[1.75rem] bg-[linear-gradient(135deg,#1677FF,#635BFF_62%,#8B5CF6)] p-6 text-white shadow-[0_24px_70px_rgba(99,91,255,0.24)] sm:p-8">
                <div className="flex items-center justify-between border-b border-white/20 pb-4">
                  <p className="text-sm font-semibold text-white">企业知识沉淀</p>
                  <span className="text-xs font-semibold text-white/80">界面示意</span>
                </div>
                <p className="mt-4 text-sm leading-6 text-white/85">入库一次，写标、审标、查串标复用稳定资料</p>
                <div className="mt-5 flex flex-1 flex-col justify-evenly gap-5">
                  {content.libraries.map(item => {
                    const tone = LIBRARY_TONES[item.key]
                    return (
                      <div key={item.key} className={`rounded-xl border px-4 py-5 ${tone.container}`}>
                        <p className={`text-xs font-semibold ${tone.caption}`}>
                          {item.title}
                          <span className="ml-2 font-medium text-[#425466]">{item.use}</span>
                        </p>
                        <p className="mt-2 text-sm font-semibold text-[#425466]">{item.description}</p>
                      </div>
                    )
                  })}
                </div>
              </div>
            </div>
            <p className="mt-6 border-t border-slate-200 pt-4 text-sm leading-6 text-slate-500">{content.footnote}</p>
          </div>
        </div>
      </div>
    </section>
  )
}

export default PlatformKnowledgeSection
