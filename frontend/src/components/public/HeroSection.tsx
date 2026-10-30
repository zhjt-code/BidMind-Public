import React from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, CheckCircle2, FileSearch, PenTool, ShieldAlert } from 'lucide-react'
import { APP_HOME, REGISTER_PATH } from '@/config/routes'
import { publicHomeContent, type PublicCapabilityKey } from '@/content/publicHome'
import { useAuthStore } from '@/stores'
import HeroAiDemo from './HeroAiDemo'

interface HeroPainPointTone {
  icon: React.ElementType
  iconBox: string
  solutionIcon: string
}

const HERO_PAIN_POINT_TONES: Record<PublicCapabilityKey, HeroPainPointTone> = {
  writing: {
    icon: PenTool,
    iconBox: 'border-teal-100 bg-teal-50 text-teal-600',
    solutionIcon: 'text-teal-700',
  },
  review: {
    icon: FileSearch,
    iconBox: 'border-indigo-100 bg-indigo-50 text-indigo-600',
    solutionIcon: 'text-indigo-700',
  },
  collusion: {
    icon: ShieldAlert,
    iconBox: 'border-[#E3D5F5] bg-[#F6F1FC] text-[#6C40A2]',
    solutionIcon: 'text-[#6C40A2]',
  },
}

interface HeroJourneyStepTone {
  cardBorder: string
  stepBadge: string
  stepNumber: string
  reviewNote: string
}

const HERO_JOURNEY_STEP_TONES: Record<PublicCapabilityKey, HeroJourneyStepTone> = {
  collusion: {
    cardBorder: 'border-white/20 hover:border-white/40',
    stepBadge: 'bg-[#7165EA]/30 text-[#F6F1FC]',
    stepNumber: 'text-[#E3D5F5]',
    reviewNote: 'text-[#E3D5F5]',
  },
  review: {
    cardBorder: 'border-white/20 hover:border-white/40',
    stepBadge: 'bg-indigo-300/25 text-indigo-100',
    stepNumber: 'text-indigo-200',
    reviewNote: 'text-indigo-100',
  },
  writing: {
    cardBorder: 'border-white/20 hover:border-white/40',
    stepBadge: 'bg-teal-400/20 text-teal-100',
    stepNumber: 'text-teal-200',
    reviewNote: 'text-teal-100',
  },
}

const proofItems = [
  { label: '来源页码', container: 'border-cyan-100 bg-cyan-50', icon: 'text-teal-600' },
  { label: '结构化结果', container: 'border-indigo-100 bg-indigo-50', icon: 'text-indigo-600' },
  { label: 'PDF 报告', container: 'border-blue-100 bg-blue-50', icon: 'text-blue-600' },
  { label: 'Word 文档', container: 'border-teal-100 bg-teal-50', icon: 'text-teal-600' },
  { label: '风险线索', container: 'border-orange-100 bg-orange-50', icon: 'text-orange-600' },
] as const

const HeroSection: React.FC = () => {
  const { isAuthenticated } = useAuthStore()
  const primaryPath = isAuthenticated ? APP_HOME : REGISTER_PATH
  const primaryLabel = isAuthenticated ? '进入工作台' : publicHomeContent.hero.primaryCta
  const titleAccentParts = publicHomeContent.hero.titleAccent.split('，')

  return (
    <section className="stripe-grid-section relative overflow-hidden border-b border-slate-200">
      <div className="relative z-10 mx-auto grid max-w-7xl items-start gap-9 px-4 pb-12 pt-14 sm:px-6 md:grid-cols-[minmax(0,0.98fr)_minmax(0,1.02fr)] md:pb-14 md:pt-16 lg:px-8 xl:gap-12">
        <div>
          <p className="text-sm font-semibold tracking-wide text-primary">{publicHomeContent.hero.eyebrow}</p>
          <h1 className="mt-5 max-w-2xl text-4xl font-semibold leading-[1.08] tracking-tight text-[#0A2540] sm:text-5xl xl:text-[3.35rem]">
            <span className="block">{publicHomeContent.hero.title}</span>
            <span className="gradient-text block">{titleAccentParts[0]}，</span>
            <span className="gradient-text block">{titleAccentParts[1]}</span>
          </h1>
          <ul className="mt-7 max-w-2xl space-y-3" aria-label="三类任务刚需">
            {publicHomeContent.hero.painPoints.map(item => {
              const tone = HERO_PAIN_POINT_TONES[item.key]
              const Icon = tone.icon
              return (
                <li
                  key={item.key}
                  className="flex gap-4 rounded-2xl border border-white/60 bg-white/60 p-4 shadow-lg backdrop-blur-xl sm:p-5"
                >
                  <span className={`flex h-11 w-11 flex-none items-center justify-center rounded-xl border ${tone.iconBox}`}>
                    <Icon className="h-5 w-5" aria-hidden="true" />
                  </span>
                  <div className="min-w-0">
                    <p className="text-base font-semibold text-[#0A2540]">{item.label}</p>
                    <p className="mt-1.5 text-sm font-semibold leading-7 text-blue-700">{item.pain}</p>
                    <p className="mt-2.5 flex gap-2 text-sm leading-7">
                      <CheckCircle2 className={`mt-1 h-4 w-4 flex-none ${tone.solutionIcon}`} aria-hidden="true" />
                      <span className="text-[#425466]">{item.solution}</span>
                    </p>
                  </div>
                </li>
              )
            })}
          </ul>
          <div className="mt-8 flex flex-col gap-3 sm:flex-row">
            <Link
              to={primaryPath}
              className="inline-flex h-12 items-center justify-center rounded-xl bg-primary px-5 text-sm font-semibold text-white transition-colors hover:bg-primary/90 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
            >
              {primaryLabel}
              <ArrowRight className="ml-2 h-4 w-4" aria-hidden="true" />
            </Link>
            <a
              href="#capabilities"
              className="inline-flex h-12 items-center justify-center rounded-xl border border-slate-300 bg-white px-5 text-sm font-semibold text-slate-700 transition-colors hover:bg-slate-50 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
            >
              {publicHomeContent.hero.secondaryCta}
            </a>
          </div>
        </div>

        <HeroAiDemo />
      </div>

      <div className="relative z-10 mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8 lg:px-8">
        <div className="rounded-[1.75rem] bg-[linear-gradient(135deg,#1677FF,#635BFF_62%,#8B5CF6)] p-5 text-white shadow-[0_24px_70px_rgba(99,91,255,0.24)] sm:p-6">
          <div className="mb-4 flex items-baseline justify-between gap-3">
            <p className="text-sm font-semibold text-white sm:text-base">上手极简，最少 1 步出结果</p>
            <p className="hidden text-xs text-white/70 sm:block">按业务复杂度排列，步数即人工节点数</p>
          </div>
          <ol className="grid gap-3 sm:grid-cols-3 sm:gap-4" aria-label="三大流程步骤简介">
            {publicHomeContent.heroJourneySteps.map(step => {
              const tone = HERO_JOURNEY_STEP_TONES[step.key]
              return (
                <li
                  key={step.key}
                  className={`group flex items-center gap-4 rounded-2xl border bg-white/10 p-4 backdrop-blur-xs transition-colors sm:p-5 ${tone.cardBorder}`}
                >
                  <div className="flex flex-col items-center gap-1">
                    <span className={`text-[2.25rem] font-semibold leading-none ${tone.stepNumber}`}>{step.stepNumber}</span>
                    <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${tone.stepBadge}`}>步</span>
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-base font-semibold text-white">{step.title}</p>
                    <p className="mt-1.5 text-sm leading-6 text-white/75">{step.summary}</p>
                    <p className={`mt-2 text-xs font-semibold ${tone.reviewNote}`}>· {step.reviewNote}</p>
                  </div>
                </li>
              )
            })}
          </ol>
        </div>
      </div>

      <div className="relative z-10 mx-auto flex max-w-7xl gap-3 overflow-x-auto border-t border-slate-200 px-4 py-3 sm:grid sm:grid-cols-5 sm:overflow-visible sm:px-6 lg:px-8">
        {proofItems.map(item => (
          <div
            key={item.label}
            className={`flex min-h-14 min-w-36 flex-1 items-center gap-2 rounded-xl border px-3 text-sm font-semibold text-[#0A2540] sm:min-w-0 ${item.container}`}
          >
            <CheckCircle2 className={`h-4 w-4 flex-none ${item.icon}`} aria-hidden="true" />
            {item.label}
          </div>
        ))}
      </div>
    </section>
  )
}

export default HeroSection
