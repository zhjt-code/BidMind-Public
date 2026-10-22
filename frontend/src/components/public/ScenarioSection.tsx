import React from 'react'
import { publicHomeContent } from '@/content/publicHome'

const SCENARIO_OUTCOMES = {
  投标团队: ['评分重点响应', '企业素材复用', 'Word 定稿'],
  审查人员: ['要求逐项核对', '来源页码回溯', 'PDF 报告'],
  风控与监督: ['多文件对比', '异常维度归集', '人工研判线索'],
} as const

const SCENARIO_STEP_TONES = [
  { container: 'border-cyan-100 bg-cyan-50', label: 'text-cyan-700' },
  { container: 'border-indigo-100 bg-indigo-50', label: 'text-indigo-700' },
  { container: 'border-[#E3D5F5] bg-[#F6F1FC]', label: 'text-[#6C40A2]' },
] as const

const ScenarioSection: React.FC = () => {
  return (
    <section id="scenarios" className="bg-white py-14 sm:py-16">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="max-w-3xl">
          <p className="text-sm font-semibold text-primary">应用场景</p>
          <h2 className="gradient-text mt-3 text-3xl font-bold tracking-tight sm:text-4xl">平台能力最终要回到每个真实角色的任务</h2>
          <p className="mt-4 text-base leading-7 text-slate-600">不使用空泛行业标签，每个场景都对应明确输入、复核动作和交付产物</p>
        </div>

        <div className="mt-8 space-y-4">
          {publicHomeContent.scenarios.map((item, index) => (
            <article key={item.title} className="grid gap-8 border-t border-slate-200 py-7 lg:grid-cols-[0.85fr_1.15fr] lg:items-center">
              <div className={index % 2 === 1 ? 'lg:order-2' : ''}>
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">角色任务 0{index + 1}</p>
                <h3 className="mt-3 text-2xl font-bold text-slate-950">{item.title}</h3>
                <p className="mt-4 max-w-xl text-base leading-7 text-slate-600">{item.description}</p>
              </div>

              <div className={`gradient-hairline-frame rounded-3xl ${index % 2 === 1 ? 'lg:order-1' : ''}`}>
                <div className="rounded-[1.45rem] bg-[linear-gradient(135deg,#1677FF,#635BFF_62%,#8B5CF6)] p-5 text-white shadow-[0_24px_70px_rgba(99,91,255,0.24)]">
                  <div className="flex items-center justify-between border-b border-white/20 pb-3">
                    <p className="text-sm font-semibold text-white">{item.title}任务视图</p>
                    <span className="text-xs font-semibold text-white/80">界面示意</span>
                  </div>
                  <div className="mt-4 grid gap-3 sm:grid-cols-3">
                    {SCENARIO_OUTCOMES[item.title].map((outcome, outcomeIndex) => (
                      <div key={outcome} className={`rounded-xl border p-3 ${SCENARIO_STEP_TONES[outcomeIndex].container}`}>
                        <p className={`text-xs font-semibold ${SCENARIO_STEP_TONES[outcomeIndex].label}`}>步骤 {outcomeIndex + 1}</p>
                        <p className="mt-2 text-sm font-semibold text-[#0A2540]">{outcome}</p>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  )
}

export default ScenarioSection
