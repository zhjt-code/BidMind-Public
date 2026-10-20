import React from 'react'
import { ChevronRight, Target, AlertTriangle, ShieldCheck } from 'lucide-react'
import { HelpFigureView } from './figures'
import type {
  HelpActionItem,
  HelpBlock,
  HelpCardItem,
  HelpGuideStep,
  HelpQualityItem,
  HelpStepItem,
} from './types'

/**
 * 职责: 按 HelpBlock.kind 渲染帮助正文（含嵌套步骤手风琴）。
 * 不做: 不持有顶层「打开哪个指南」状态，不读写项目数据。
 */

interface BlockRendererProps {
  block: HelpBlock
  expandedStepId: string | null
  onToggleStep: (id: string) => void
}

export const BlockRenderer: React.FC<BlockRendererProps> = ({ block, expandedStepId, onToggleStep }) => {
  switch (block.kind) {
    case 'paragraph':
      return <p className="text-sm text-gray-600 leading-relaxed">{block.text}</p>
    case 'callout':
      return <CalloutBlock tone={block.tone} text={block.text} />
    case 'cards':
      return (
        <CardsBlock
          items={block.items}
          expandedStepId={expandedStepId}
          onToggleStep={onToggleStep}
        />
      )
    case 'steps':
      return <StepsBlock items={block.items} />
    case 'quality':
      return <QualityBlock items={block.items} />
    case 'notes':
      return <NotesBlock items={block.items} />
    case 'guideSteps':
      return (
        <GuideStepsBlock
          items={block.items}
          expandedStepId={expandedStepId}
          onToggleStep={onToggleStep}
        />
      )
  }
}

const CALLOUT_STYLES = {
  warning: { wrap: 'bg-amber-50 border-amber-200', text: 'text-amber-800', icon: 'text-amber-500', Icon: AlertTriangle },
  info: { wrap: 'bg-blue-50 border-blue-200', text: 'text-blue-800', icon: 'text-blue-500', Icon: AlertTriangle },
  success: { wrap: 'bg-emerald-50 border-emerald-200', text: 'text-emerald-800', icon: 'text-emerald-500', Icon: ShieldCheck },
} as const

const CalloutBlock: React.FC<{ tone: 'info' | 'warning' | 'success'; text: string }> = ({ tone, text }) => {
  const style = CALLOUT_STYLES[tone]
  const Icon = style.Icon
  return (
    <div className={`flex items-start gap-2 p-3 rounded-lg border ${style.wrap}`}>
      <Icon className={`w-4 h-4 ${style.icon} mt-0.5 flex-shrink-0`} />
      <p className={`text-xs leading-relaxed ${style.text}`}>{text}</p>
    </div>
  )
}

const CardHeader: React.FC<{ item: HelpCardItem }> = ({ item }) => {
  const Icon = item.icon
  return (
    <>
      <Icon className={`w-4 h-4 ${item.color} mt-0.5 flex-shrink-0`} />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-gray-800">{item.title}</p>
        <p className="text-xs text-gray-500 mt-0.5 leading-relaxed">{item.description}</p>
      </div>
    </>
  )
}

const CardsBlock: React.FC<{
  items: HelpCardItem[]
  expandedStepId: string | null
  onToggleStep: (id: string) => void
}> = ({ items, expandedStepId, onToggleStep }) => (
  <div className="space-y-2">
    {items.map(item => {
      if (!item.id) {
        return (
          <div key={item.title} className={`flex items-start gap-2.5 p-2.5 rounded-md ${item.bgColor}`}>
            <CardHeader item={item} />
          </div>
        )
      }
      const stepId = item.id
      const isOpen = expandedStepId === stepId
      return (
        <div key={item.title} className={`rounded-md ${item.bgColor} overflow-hidden`}>
          <button
            type="button"
            onClick={() => onToggleStep(stepId)}
            className="w-full flex items-start gap-2.5 p-2.5 text-left hover:bg-black/[0.02] transition-colors"
          >
            <CardHeader item={item} />
            <ChevronRight
              className={`w-3.5 h-3.5 text-gray-400 shrink-0 mt-1 transition-transform duration-200 ${isOpen ? 'rotate-90' : ''}`}
            />
          </button>
          {isOpen && (
            <div className="px-2.5 pb-2.5 space-y-2.5">
              <StepDetails
                summary={item.summary}
                figures={item.figures}
                actions={item.actions}
                notes={item.notes}
              />
            </div>
          )}
        </div>
      )
    })}
  </div>
)

const StepsBlock: React.FC<{ items: HelpStepItem[] }> = ({ items }) => (
  <div className="space-y-3">
    {items.map(item => {
      const Icon = item.icon
      return (
        <div key={item.title} className="flex gap-3">
          <div className="w-6 h-6 rounded-full bg-gray-100 flex items-center justify-center flex-shrink-0 mt-0.5">
            <Icon className="w-3.5 h-3.5 text-gray-500" />
          </div>
          <div className="flex-1 text-sm">
            <p className="font-medium text-gray-800">{item.title}</p>
            <p className="text-gray-500 mt-0.5 leading-relaxed">{item.description}</p>
            {item.details && (
              <ul className="mt-1.5 space-y-1">
                {item.details.map(detail => (
                  <li key={detail} className="text-xs text-gray-500 flex items-start gap-1.5 leading-relaxed">
                    <span className="w-1 h-1 rounded-full bg-gray-300 mt-1.5 flex-shrink-0" />
                    {detail}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )
    })}
  </div>
)

const QualityBlock: React.FC<{ items: HelpQualityItem[]; heading?: string }> = ({
  items,
  heading = '影响结果质量的关键',
}) => (
  <div className="rounded-lg border border-amber-200 bg-amber-50/40 p-3">
    <div className="flex items-center gap-1.5 mb-2.5">
      <Target className="w-4 h-4 text-amber-600" />
      <span className="text-sm font-semibold text-amber-800">{heading}</span>
    </div>
    <div className="space-y-2">
      {items.map(item => (
        <div
          key={item.title}
          className={`rounded-md p-2.5 ${item.emphasis ? 'bg-white border border-amber-200' : 'bg-white/60'}`}
        >
          <div className="flex items-center gap-1.5">
            <p className="text-xs font-semibold text-gray-800">{item.title}</p>
            {item.emphasis && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 font-medium flex-shrink-0">
                重点
              </span>
            )}
          </div>
          <p className="text-xs text-gray-600 mt-1 leading-relaxed">{item.description}</p>
        </div>
      ))}
    </div>
  </div>
)

const NotesBlock: React.FC<{ items: string[] }> = ({ items }) => (
  <div className="rounded-lg bg-gray-50 p-3">
    <div className="flex items-center gap-1.5 mb-2">
      <AlertTriangle className="w-3.5 h-3.5 text-gray-400" />
      <span className="text-xs font-semibold text-gray-600">注意事项</span>
    </div>
    <ul className="space-y-1.5">
      {items.map(text => (
        <li key={text} className="text-xs text-gray-500 flex items-start gap-1.5 leading-relaxed">
          <span className="w-1 h-1 rounded-full bg-gray-300 mt-1.5 flex-shrink-0" />
          {text}
        </li>
      ))}
    </ul>
  </div>
)

const StepDetails: React.FC<{
  summary?: string
  figures?: HelpGuideStep['figures']
  actions?: HelpActionItem[]
  quality?: HelpQualityItem[]
  notes?: string[]
}> = ({ summary, figures, actions, quality, notes }) => (
  <>
    {summary ? (
      <p className="text-xs text-gray-600 leading-relaxed">{summary}</p>
    ) : null}
    {figures?.map(fig => (
      <HelpFigureView key={fig.id} id={fig.id} caption={fig.caption} />
    ))}
    {actions && actions.length > 0 ? (
      <div className="space-y-2">
        <p className="text-[11px] font-semibold text-gray-700">本步操作</p>
        {actions.map(action => (
          <ActionItemView key={action.name} item={action} />
        ))}
      </div>
    ) : null}
    {quality && quality.length > 0 ? (
      <QualityBlock items={quality} heading="本步影响结果的要点" />
    ) : null}
    {notes && notes.length > 0 ? (
      <NotesBlock items={notes} />
    ) : null}
  </>
)

const ActionItemView: React.FC<{ item: HelpActionItem }> = ({ item }) => (
  <div className="rounded-md border border-gray-200 bg-white p-2.5 space-y-1">
    <p className="text-xs font-semibold text-gray-800">{item.name}</p>
    <p className="text-[11px] text-gray-600 leading-relaxed">
      <span className="font-medium text-gray-700">何时用：</span>{item.when}
    </p>
    <p className="text-[11px] text-gray-600 leading-relaxed">
      <span className="font-medium text-gray-700">怎么用：</span>{item.how}
    </p>
    {item.after && (
      <p className="text-[11px] text-gray-600 leading-relaxed">
        <span className="font-medium text-gray-700">点完会怎样：</span>{item.after}
      </p>
    )}
    {item.risk && (
      <p className="text-[11px] text-amber-800 leading-relaxed bg-amber-50 border border-amber-200 rounded px-2 py-1">
        {item.risk}
      </p>
    )}
  </div>
)

const GuideStepsBlock: React.FC<{
  items: HelpGuideStep[]
  expandedStepId: string | null
  onToggleStep: (id: string) => void
}> = ({ items, expandedStepId, onToggleStep }) => (
  <div className="space-y-2">
    {items.map(step => {
      const isOpen = expandedStepId === step.id
      return (
        <div key={step.id} className="rounded-md border border-gray-200 overflow-hidden">
          <button
            type="button"
            onClick={() => onToggleStep(step.id)}
            className="w-full flex items-center gap-2 px-3 py-2 hover:bg-gray-50 transition-colors text-left"
          >
            <ChevronRight
              className={`w-3.5 h-3.5 text-gray-400 shrink-0 transition-transform duration-200 ${isOpen ? 'rotate-90' : ''}`}
            />
            <span className="text-xs font-semibold text-gray-800">{step.title}</span>
          </button>
          {isOpen && (
            <div className="px-3 pb-3 space-y-2.5 border-t border-gray-100 pt-2.5">
              <StepDetails
                summary={step.summary}
                figures={step.figures}
                actions={step.actions}
                quality={step.quality}
                notes={step.notes}
              />
            </div>
          )}
        </div>
      )
    })}
  </div>
)
