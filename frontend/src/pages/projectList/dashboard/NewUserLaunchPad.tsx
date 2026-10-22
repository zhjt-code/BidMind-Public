import React from 'react'
import { ClipboardCheck, PenTool, ShieldAlert, Upload, Sparkles, FileCheck, Download } from 'lucide-react'
import { useCreateDialogStore } from '@/stores/createDialogStore'
import { Button } from '@/components/ui/button'

const FLOW_STEPS = [
  { icon: Upload, label: '创建项目' },
  { icon: FileCheck, label: '上传文件' },
  { icon: Sparkles, label: 'AI处理' },
  { icon: Download, label: '查看结果' },
]

const BUSINESS_CARDS = [
  {
    type: 'bid_writing' as const,
    title: '标书编写',
    description: '上传招标文件，生成目录、章节正文与投标文件初稿。',
    icon: PenTool,
    accent: 'from-teal-500 to-teal-600',
  },
  {
    type: 'bid_review' as const,
    title: '投标审查',
    description: '对标招标要求，逐项核验投标文件响应情况。',
    icon: ClipboardCheck,
    accent: 'from-blue-500 to-cyan-500',
  },
  {
    type: 'collusion_check' as const,
    title: '串标审查',
    description: '交叉对比多份投标文件，识别相似与关联线索。',
    icon: ShieldAlert,
    accent: 'from-[#7165EA] to-[#9056D8]',
  },
]

const NewUserLaunchPad: React.FC = () => {
  const open = useCreateDialogStore((state) => state.open)
  return (
    <section className="overflow-hidden rounded-2xl border border-slate-200 bg-slate-950 text-white shadow-lg">
      <div className="relative p-6 sm:p-8">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(34,211,238,0.25),transparent_35%),radial-gradient(circle_at_bottom_left,rgba(139,92,246,0.22),transparent_35%)]" />
        <div className="relative space-y-7">
          <div className="max-w-2xl">
            <p className="text-sm font-medium text-cyan-200">AI 标书业务控制台</p>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight sm:text-3xl">
              选择一个业务类型，开始创建您的第一个项目
            </h2>
            <p className="mt-3 text-sm leading-6 text-slate-300">
              从招标文件到审查、编写、串标分析，创建项目即可进入自动化流程。
            </p>
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            {BUSINESS_CARDS.map((card) => {
              const Icon = card.icon
              return (
                <button
                  key={card.type}
                  type="button"
                  onClick={() => open(card.type)}
                  className="group rounded-xl border border-white/10 bg-white/8 p-4 text-left transition hover:-translate-y-0.5 hover:border-cyan-300/50 hover:bg-white/12 motion-reduce:transform-none"
                >
                  <div className="mb-4 flex items-start justify-between gap-2">
                    <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br ${card.accent}`}>
                      <Icon className="h-5 w-5 text-white" />
                    </div>
                  </div>
                  <h3 className="text-base font-semibold">{card.title}</h3>
                  <p className="mt-2 min-h-[44px] text-sm leading-5 text-slate-300">{card.description}</p>
                  <span className="mt-4 inline-flex text-sm font-medium text-cyan-200 group-hover:text-white">
                    创建{card.title}项目
                  </span>
                </button>
              )
            })}
          </div>

          <div className="rounded-xl border border-white/10 bg-white/8 p-4">
            <div className="grid gap-3 sm:grid-cols-4">
              {FLOW_STEPS.map((step, index) => {
                const Icon = step.icon
                return (
                  <div key={step.label} className="flex items-center gap-3">
                    <div className="flex h-8 w-8 items-center justify-center rounded-full bg-cyan-400/15 text-cyan-200">
                      <Icon className="h-4 w-4" />
                    </div>
                    <div>
                      <p className="text-xs text-slate-400">步骤 {index + 1}</p>
                      <p className="text-sm font-medium text-slate-100">{step.label}</p>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          <Button onClick={() => open()} className="bg-cyan-500 text-white hover:bg-cyan-400">
            新建项目
          </Button>
        </div>
      </div>
    </section>
  )
}

export default NewUserLaunchPad
