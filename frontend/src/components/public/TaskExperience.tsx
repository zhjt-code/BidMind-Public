import React, { useEffect, useState } from 'react'
import { FileSearch, PenTool, ShieldAlert } from 'lucide-react'
import { publicHomeContent, type PublicCapabilityKey } from '@/content/publicHome'

/**
 * 职责: 展示三大任务的证据轨迹切换演示。
 * 不做: 不请求真实业务数据、不承诺指标或自动替代人工结论。
 */

interface TaskDemo {
  source: string
  focus: string
  evidence: string
  output: string
}

const TASK_ORDER: PublicCapabilityKey[] = ['writing', 'review', 'collusion']
const TASK_ROTATE_INTERVAL_MS = 4200

// 三条旅程步骤数不等（6/6/5），轮播切换会让 grid 行高跳变并推动下方全部内容，故按最大步数补不可见占位撑平
const MAX_JOURNEY_STEP_COUNT = Math.max(...publicHomeContent.journeys.map(journey => journey.steps.length))

const TASK_DEMOS: Record<PublicCapabilityKey, TaskDemo> = {
  writing: {
    source: '招标文件来源页码 P42 · 技术评分重点',
    focus: '项目实施方案需覆盖组织、进度与质量保障',
    evidence: '已绑定企业素材 2 份 · 章节 3.2',
    output: 'Word 草稿 · 待人工复核',
  },
  review: {
    source: '招标文件来源页码 P36 · 资格要求',
    focus: '近三年类似项目业绩与证明材料',
    evidence: '投标文件 P18 / P42 / P56',
    output: '结构化审查结果 · PDF 报告',
  },
  collusion: {
    source: '投标文件 A / B · 文件对',
    focus: '文本、人员与元数据异常相似线索',
    evidence: '来源段落与维度证据已归集',
    output: '风险证据报告 · 待人工研判',
  },
}

const TASK_ICONS: Record<PublicCapabilityKey, React.ElementType> = {
  writing: PenTool,
  review: FileSearch,
  collusion: ShieldAlert,
}

const PROJECT_BADGE_TONES: Record<PublicCapabilityKey, string> = {
  writing: 'border-teal-100 bg-teal-50 text-teal-700',
  review: 'border-blue-100 bg-blue-50 text-blue-700',
  collusion: 'border-[#E3D5F5] bg-[#F6F1FC] text-[#6C40A2]',
}

const TASK_TAB_TONES: Record<PublicCapabilityKey, string> = {
  writing: 'border-teal-300 bg-teal-50 text-[#0A2540]',
  review: 'border-[#635BFF] bg-indigo-50 text-[#0A2540]',
  collusion: 'border-[#9056D8] bg-[#F6F1FC] text-[#0A2540]',
}

const TaskExperience: React.FC = () => {
  const [activeKey, setActiveKey] = useState<PublicCapabilityKey>('writing')
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false)
  const activeJourney = publicHomeContent.journeys.find(item => item.key === activeKey) ?? publicHomeContent.journeys[0]
  const activeCapability = publicHomeContent.capabilities.find(item => item.key === activeKey) ?? publicHomeContent.capabilities[0]
  const activeDemo = TASK_DEMOS[activeKey]

  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
    const syncMotionPreference = () => setPrefersReducedMotion(mediaQuery.matches)
    syncMotionPreference()
    mediaQuery.addEventListener('change', syncMotionPreference)
    return () => mediaQuery.removeEventListener('change', syncMotionPreference)
  }, [])

  useEffect(() => {
    if (prefersReducedMotion) return

    const intervalId = window.setInterval(() => {
      setActiveKey(currentKey => {
        const currentIndex = TASK_ORDER.indexOf(currentKey)
        return TASK_ORDER[(currentIndex + 1) % TASK_ORDER.length]
      })
    }, TASK_ROTATE_INTERVAL_MS)
    return () => window.clearInterval(intervalId)
  }, [activeKey, prefersReducedMotion])

  const selectTask = (key: PublicCapabilityKey) => {
    setActiveKey(key)
  }

  const handleTabKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, currentKey: PublicCapabilityKey) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp' && event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return
    event.preventDefault()
    const currentIndex = TASK_ORDER.indexOf(currentKey)
    const direction = event.key === 'ArrowDown' || event.key === 'ArrowRight' ? 1 : -1
    const nextKey = TASK_ORDER[(currentIndex + direction + TASK_ORDER.length) % TASK_ORDER.length]
    selectTask(nextKey)
    document.getElementById(`task-tab-${nextKey}`)?.focus()
  }

  return (
    <section id="capabilities" className="relative border-b border-slate-200 bg-white py-14 sm:py-16">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="max-w-6xl">
              <p className="text-sm font-semibold text-[#635BFF]">三类任务 · 一个证据工作台</p>
              <h2 className="gradient-text mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">
            选择任务，沿着证据轨迹看到真实工作方式
          </h2>
          <p className="mt-4 text-base leading-7 text-slate-600 lg:whitespace-nowrap">
            写标、审标和风险识别分别作为独立项目运行，但都遵循同一原则：输入来自文件，关键节点可确认，输出能够回到证据
          </p>
        </div>

        <div className="mt-10 grid gap-8 lg:grid-cols-[300px_minmax(0,1fr)]">
          <div className="self-start lg:sticky lg:top-24">
            <div className="space-y-3" role="tablist" aria-label="三大任务演示">
              {publicHomeContent.capabilities.map(item => {
                const Icon = TASK_ICONS[item.key]
                const selected = activeKey === item.key
                return (
                  <button
                    key={item.key}
                    id={`task-tab-${item.key}`}
                    type="button"
                    role="tab"
                    aria-selected={selected}
                    aria-controls={selected ? `task-panel-${item.key}` : undefined}
                    tabIndex={selected ? 0 : -1}
                    onClick={() => selectTask(item.key)}
                    onKeyDown={event => handleTabKeyDown(event, item.key)}
                    className={`w-full rounded-xl border px-4 py-3 text-left transition-colors ${
                      selected
                        ? TASK_TAB_TONES[item.key]
                        : 'border-slate-200 bg-slate-50 text-[#425466] hover:border-slate-300 hover:bg-white'
                    }`}
                  >
                    <div className="flex items-center gap-3">
                      <span className={`inline-flex h-10 w-10 items-center justify-center rounded-xl ${
                        item.key === 'writing' ? 'bg-teal-50 text-teal-700' : item.key === 'collusion' ? 'bg-[#F6F1FC] text-[#6C40A2]' : 'bg-blue-50 text-blue-700'
                      }`}>
                        <Icon className="h-5 w-5" aria-hidden="true" />
                      </span>
                      <span>
                        <span className="block font-bold">{item.title}</span>
                        <span className="mt-1 block text-xs">{item.output}</span>
                      </span>
                    </div>
                  </button>
                )
              })}
            </div>
          </div>

          <article
            key={activeKey}
            id={`task-panel-${activeKey}`}
            role="tabpanel"
            aria-labelledby={`task-tab-${activeKey}`}
            className={`gradient-hairline-frame task-panel-motion task-canvas task-canvas--${activeKey} overflow-hidden rounded-3xl`}
          >
            <div className="bg-white p-5 sm:p-6">
              <div className="flex flex-wrap items-start justify-between gap-4 border-b border-slate-200 pb-5">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">证据轨迹 · 产品界面示意</p>
                  <h3 className="mt-2 text-2xl font-bold text-slate-950">{activeJourney.title}</h3>
                  <p className="mt-2 text-sm leading-6 text-slate-600">{activeCapability.summary}</p>
                </div>
                <span className={`rounded-full border px-3 py-1.5 text-xs font-semibold ${PROJECT_BADGE_TONES[activeKey]}`}>
                  独立项目
                </span>
              </div>

              <div className="mt-6 grid gap-5 xl:grid-cols-[0.92fr_1.08fr]">
                <ol className="space-y-2">
                  {activeJourney.steps.map((step, index) => (
                    <li key={step} className="flex items-center gap-3 rounded-xl bg-slate-50 px-3 py-2.5 text-sm text-slate-700">
                      <span className="flex h-6 w-6 flex-none items-center justify-center rounded-full bg-[#635BFF] text-xs font-bold text-white">{index + 1}</span>
                      {step}
                    </li>
                  ))}
                  {Array.from({ length: MAX_JOURNEY_STEP_COUNT - activeJourney.steps.length }).map((_, index) => (
                    <li
                      key={`journey-step-spacer-${index}`}
                      aria-hidden="true"
                      className="invisible flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm"
                    >
                      <span className="flex h-6 w-6 flex-none items-center justify-center rounded-full text-xs font-bold">0</span>
                    </li>
                  ))}
                </ol>

                <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                  <p className="text-xs font-semibold uppercase tracking-[0.14em] text-primary">证据轨迹</p>
                  {[activeDemo.source, activeDemo.focus, activeDemo.evidence, activeDemo.output].map((item, index) => (
                    <div key={item} className="relative mt-3 rounded-xl border border-slate-200 bg-white p-3 pl-10 text-sm text-slate-700">
                      {index < 3 && <span className="absolute left-[1.18rem] top-8 h-8 w-px bg-primary/25" aria-hidden="true" />}
                      <span className="absolute left-3 top-3.5 h-4 w-4 rounded-full border-2 border-primary bg-white" aria-hidden="true" />
                      {item}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </article>
        </div>
      </div>
    </section>
  )
}

export default TaskExperience
