import React, { useEffect, useState } from 'react'
import { CheckCircle2, FileSearch, PenTool, ShieldAlert } from 'lucide-react'
import { publicHomeContent, type PublicCapabilityKey } from '@/content/publicHome'

/**
 * 职责: 首屏展示 AI 读标书、提证据、出结果的工作过程。
 * 不做: 不请求真实业务数据、不承诺客户指标、处理时长或自动替代人工结论。
 */

interface HeroDemoFrame {
  title: string
  description: string
  evidence: string
}

interface HeroDemoScene {
  key: PublicCapabilityKey
  label: string
  title: string
  summary: string
  activeLabel: string
  frames: HeroDemoFrame[]
}

interface HeroDemoTone {
  selectedTab: string
  badge: string
  progress: string
  scanLine: string
  stackLabel: string
  currentPanel: string
  completedFrame: string
  activeIcon: string
  evidencePill: string
}

const PHASE_INTERVAL_MS = 1800

const HERO_DEMO_DOC_PARAGRAPHS = [
  ['w-full', 'w-11/12', 'w-full', 'w-9/12', 'w-10/12'],
  ['w-full', 'w-10/12', 'w-full', 'w-11/12', 'w-8/12'],
  ['w-11/12', 'w-full', 'w-9/12', 'w-full', 'w-7/12'],
] as const

const HERO_DEMO_DOC_LINE_TOTAL = HERO_DEMO_DOC_PARAGRAPHS.reduce((total, lines) => total + lines.length, 0)

const STACK_CARD_OFFSET_PX = 34
const STACK_CARD_MAX_DEPTH = 3

const HERO_DEMO_SCENES: HeroDemoScene[] = [
  {
    key: 'writing',
    label: '写标',
    title: '从招标约束生成章节响应',
    summary: '把要求、评分点和企业素材放到同一条写作链路里。',
    activeLabel: 'AI 对齐约束与素材',
    frames: [
      {
        title: '识别招标约束',
        description: '扫描服务范围、评分重点与提交格式。',
        evidence: '招标文件 P42 · 技术评分重点',
      },
      {
        title: '组织章节响应',
        description: '把约束绑定到实施方案、人员安排和质量保障章节。',
        evidence: '章节 3.2 · 企业素材已匹配',
      },
      {
        title: '提示内容缺口',
        description: '发现进度保障材料不足，提醒补充对应素材。',
        evidence: '待补素材 · 进度计划',
      },
      {
        title: '生成 Word 草稿',
        description: '输出可预览的标书草稿，保留人工复核入口。',
        evidence: 'Word 草稿 · 待复核',
      },
    ],
  },
  {
    key: 'review',
    label: '审标',
    title: '逐条核对响应是否有证据',
    summary: '先看招标要求，再把投标响应、原文页码和结论串起来。',
    activeLabel: 'AI 正在核对招标要求与投标响应',
    frames: [
      {
        title: '抽取招标要求',
        description: '识别资格、商务与技术条款，保留人工确认节点。',
        evidence: '招标文件 P36 · 资格要求',
      },
      {
        title: '匹配投标响应',
        description: '在投标文件中查找对应承诺、附件和证明材料。',
        evidence: '投标文件 P18 / P42 · 响应段落',
      },
      {
        title: '标注来源页码',
        description: '把每条判断回链到原文位置，避免只给空泛结论。',
        evidence: '来源页码 P18 · P42',
      },
      {
        title: '生成审查报告',
        description: '输出结构化结果与 PDF 报告，供提交前复核。',
        evidence: 'PDF 报告 · 带证据',
      },
    ],
  },
  {
    key: 'collusion',
    label: '查串标',
    title: '提前归集异常相似证据',
    summary: '把多份文件之间的相似线索整理出来，交给人工研判。',
    activeLabel: 'AI 正在归集相似线索',
    frames: [
      {
        title: '抽取文件特征',
        description: '读取文本、人员、目录与元数据中的可比信息。',
        evidence: '投标文件 A / B · 文件对',
      },
      {
        title: '交叉比对片段',
        description: '定位措辞、格式与关键字段的异常相似线索。',
        evidence: '相似线索 · 文本片段',
      },
      {
        title: '形成风险分级',
        description: '按维度汇总相似证据，避免直接替代人工定性。',
        evidence: '风险维度 · 待研判',
      },
      {
        title: '输出证据报告',
        description: '把文件对、来源段落和风险说明汇总为报告。',
        evidence: '风险证据 · PDF 报告',
      },
    ],
  },
]

const HERO_DEMO_ICONS: Record<PublicCapabilityKey, React.ElementType> = {
  writing: PenTool,
  review: FileSearch,
  collusion: ShieldAlert,
}

const HERO_DEMO_TONES: Record<PublicCapabilityKey, HeroDemoTone> = {
  writing: {
    selectedTab: 'border-teal-300 bg-teal-50 text-[#0A2540]',
    badge: 'border-teal-100 bg-teal-50 text-teal-700',
    progress: 'bg-[linear-gradient(90deg,#08A6A6,#1677FF)]',
    scanLine: 'bg-[#08A6A6]',
    stackLabel: 'text-teal-700',
    currentPanel: 'border-teal-100',
    completedFrame: 'border-teal-100 shadow-xs',
    activeIcon: 'border-teal-500 bg-teal-500 text-white',
    evidencePill: 'border-teal-100 bg-teal-50 text-teal-700',
  },
  review: {
    selectedTab: 'border-[#635BFF] bg-indigo-50 text-[#0A2540]',
    badge: 'border-indigo-100 bg-indigo-50 text-indigo-700',
    progress: 'bg-[linear-gradient(90deg,#1677FF,#635BFF)]',
    scanLine: 'bg-[#635BFF]',
    stackLabel: 'text-indigo-700',
    currentPanel: 'border-indigo-100',
    completedFrame: 'border-indigo-100 shadow-xs',
    activeIcon: 'border-[#635BFF] bg-[#635BFF] text-white',
    evidencePill: 'border-indigo-100 bg-indigo-50 text-indigo-700',
  },
  collusion: {
    selectedTab: 'border-[#9056D8] bg-[#F6F1FC] text-[#0A2540]',
    badge: 'border-[#E3D5F5] bg-[#F6F1FC] text-[#6C40A2]',
    progress: 'bg-[linear-gradient(90deg,#7165EA,#9056D8)]',
    scanLine: 'bg-[#9056D8]',
    stackLabel: 'text-[#6C40A2]',
    currentPanel: 'border-[#E3D5F5]',
    completedFrame: 'border-[#E3D5F5] shadow-xs',
    activeIcon: 'border-[#9056D8] bg-[#9056D8] text-white',
    evidencePill: 'border-[#E3D5F5] bg-[#F6F1FC] text-[#6C40A2]',
  },
}

const findSceneIndex = (key: PublicCapabilityKey) => HERO_DEMO_SCENES.findIndex(scene => scene.key === key)

const HeroAiDemo: React.FC = () => {
  const [activeKey, setActiveKey] = useState<PublicCapabilityKey>('writing')
  const [activePhase, setActivePhase] = useState(0)
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false)
  const activeScene = HERO_DEMO_SCENES.find(scene => scene.key === activeKey) ?? HERO_DEMO_SCENES[0]
  const activeTone = HERO_DEMO_TONES[activeKey]
  const scannedLineCount = prefersReducedMotion
    ? HERO_DEMO_DOC_LINE_TOTAL
    : Math.round(((activePhase + 1) / activeScene.frames.length) * HERO_DEMO_DOC_LINE_TOTAL)
  const revealedFrames = activeScene.frames.slice(0, activePhase + 1)

  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
    const syncMotionPreference = () => setPrefersReducedMotion(mediaQuery.matches)
    syncMotionPreference()
    mediaQuery.addEventListener('change', syncMotionPreference)
    return () => mediaQuery.removeEventListener('change', syncMotionPreference)
  }, [])

  useEffect(() => {
    if (prefersReducedMotion) {
      setActivePhase(activeScene.frames.length - 1)
      return
    }

    const intervalId = window.setInterval(() => {
      setActivePhase(current => {
        if (current + 1 >= activeScene.frames.length) {
          advanceHeroDemoScene()
          return 0
        }
        return current + 1
      })
    }, PHASE_INTERVAL_MS)
    return () => window.clearInterval(intervalId)
  }, [activeScene.frames.length, activeKey, prefersReducedMotion])

  const selectScene = (key: PublicCapabilityKey) => {
    setActiveKey(key)
    setActivePhase(0)
  }

  function advanceHeroDemoScene() {
    setActiveKey(currentKey => {
      const currentIndex = findSceneIndex(currentKey)
      const nextScene = HERO_DEMO_SCENES[(currentIndex + 1) % HERO_DEMO_SCENES.length]
      return nextScene.key
    })
    setActivePhase(0)
  }

  const handleTabKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, currentKey: PublicCapabilityKey) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp' && event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return
    event.preventDefault()
    const currentIndex = findSceneIndex(currentKey)
    const direction = event.key === 'ArrowDown' || event.key === 'ArrowRight' ? 1 : -1
    const nextScene = HERO_DEMO_SCENES[(currentIndex + direction + HERO_DEMO_SCENES.length) % HERO_DEMO_SCENES.length]
    selectScene(nextScene.key)
    document.getElementById(`hero-demo-tab-${nextScene.key}`)?.focus()
  }

  return (
    <figure className={`gradient-hairline-frame task-canvas task-canvas--${activeKey} overflow-hidden rounded-3xl bg-white`}>
      <div className="border-b border-slate-200 bg-white p-4 sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold tracking-[0.18em] text-[#635BFF]">{publicHomeContent.hero.proofLine}</p>
            <h2 className="mt-1 text-lg font-semibold text-[#0A2540]">{activeScene.title}</h2>
          </div>
          <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${activeTone.badge}`}>
            {activeScene.activeLabel}
          </span>
        </div>

        <div className="mt-4 grid grid-cols-3 gap-2" role="tablist" aria-label="首屏 AI 工作演示">
          {HERO_DEMO_SCENES.map(scene => {
            const Icon = HERO_DEMO_ICONS[scene.key]
            const selected = activeKey === scene.key
            return (
              <button
                key={scene.key}
                id={`hero-demo-tab-${scene.key}`}
                type="button"
                role="tab"
                aria-selected={selected}
                aria-controls={`hero-demo-panel-${scene.key}`}
                tabIndex={selected ? 0 : -1}
                onClick={() => selectScene(scene.key)}
                onKeyDown={event => handleTabKeyDown(event, scene.key)}
                className={`rounded-xl border px-3 py-2 text-left text-xs font-semibold transition-colors ${
                  selected
                    ? HERO_DEMO_TONES[scene.key].selectedTab
                    : 'border-slate-200 bg-slate-50 text-[#425466] hover:border-slate-300 hover:bg-white'
                }`}
              >
                <span className="flex items-center gap-2">
                  <Icon className="h-4 w-4" aria-hidden="true" />
                  {scene.label}
                </span>
              </button>
            )
          })}
        </div>
      </div>

      <div
        id={`hero-demo-panel-${activeKey}`}
        role="tabpanel"
        aria-labelledby={`hero-demo-tab-${activeKey}`}
        className="grid gap-4 bg-white p-4 sm:p-5 lg:grid-cols-[0.86fr_1.14fr]"
      >
        <div className="flex flex-col rounded-2xl border border-slate-200 bg-slate-50 p-4">
          <div className="flex items-center justify-between border-b border-slate-200 pb-3">
            <span className="text-xs font-semibold text-slate-500">招投标文件</span>
            <span className="rounded-full bg-white px-2 py-1 text-[11px] font-semibold text-[#635BFF]">AI 读取中</span>
          </div>
          <div className="mt-4 flex flex-1 flex-col gap-3" aria-hidden="true">
            {HERO_DEMO_DOC_PARAGRAPHS.map((paragraph, paragraphIndex) => {
              const linesBefore = HERO_DEMO_DOC_PARAGRAPHS.slice(0, paragraphIndex).reduce(
                (total, lines) => total + lines.length,
                0,
              )
              return (
                <div key={`doc-paragraph-${paragraphIndex}`} className="space-y-1.5">
                  {paragraph.map((widthClass, lineIndex) => (
                    <span
                      key={`doc-line-${paragraphIndex}-${lineIndex}`}
                      className={`block h-1.5 rounded-full ${
                        linesBefore + lineIndex < scannedLineCount ? activeTone.scanLine : 'bg-slate-200'
                      } ${widthClass}`}
                    />
                  ))}
                </div>
              )
            })}
          </div>

          <div className="relative mt-4 h-55">
            <p className="absolute inset-x-0 top-0 text-xs font-semibold text-slate-500">已识别</p>
            {revealedFrames.map((frame, index) => {
              if (index > STACK_CARD_MAX_DEPTH) return null
              const isNewest = index === revealedFrames.length - 1
              return (
                <div
                  key={frame.title}
                  className={`hero-demo-stack-card absolute inset-x-0 top-6 h-19 rounded-xl border bg-white p-3 shadow-xs ${activeTone.currentPanel}`}
                  style={{
                    transform: `translateY(${index * STACK_CARD_OFFSET_PX}px)`,
                    zIndex: index + 1,
                  }}
                >
                  <p className={`truncate text-sm font-semibold leading-6 ${isNewest ? 'text-[#0A2540]' : 'text-slate-400'}`}>
                    {frame.title}
                  </p>
                  <p
                    className={`mt-1.5 truncate text-xs font-semibold leading-5 ${
                      isNewest ? activeTone.stackLabel : 'text-slate-300'
                    }`}
                  >
                    {frame.evidence}
                  </p>
                </div>
              )
            })}
          </div>
        </div>

        <ol className="space-y-3">
          {activeScene.frames.map((frame, index) => {
            const completed = prefersReducedMotion || index <= activePhase
            const current = !prefersReducedMotion && index === activePhase
            return (
              <li
                key={frame.title}
                className={`relative rounded-2xl border bg-white p-3 pl-11 text-sm transition-colors ${
                  completed ? activeTone.completedFrame : 'border-slate-200 text-slate-400'
                } ${current ? 'hero-demo-frame-motion' : ''}`}
              >
                <span
                  className={`absolute left-3 top-3.5 flex h-6 w-6 items-center justify-center rounded-full border ${
                    completed ? activeTone.activeIcon : 'border-slate-300 bg-white text-slate-300'
                  }`}
                  aria-hidden="true"
                >
                  <CheckCircle2 className="h-4 w-4" />
                </span>
                <span className="block font-semibold text-[#0A2540]">{frame.title}</span>
                <span className="mt-1 block leading-5 text-[#425466]">{frame.description}</span>
                <span className={`mt-2 inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${activeTone.evidencePill}`}>
                  {frame.evidence}
                </span>
              </li>
            )
          })}
        </ol>
      </div>
    </figure>
  )
}

export default HeroAiDemo
