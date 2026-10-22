import { useState } from 'react'
import type { ClauseCoverage } from '@/services/writingApi'
import { SUB_TYPE_LABELS } from './writingClauseTabs'
import { getCoverageAction, type CoverageBindingFocus } from './writingClauseCoverageAction'

interface WritingClauseCoverageOverviewProps {
  coverage: ClauseCoverage | null
  onNavigateBinding?: (focus?: CoverageBindingFocus) => void
  onRefresh?: () => Promise<void> | void
}

interface ReasonMeta {
  key: 'pendingAssignment' | 'unmatched' | 'pendingGeneration'
  label: string
  hint: string
}

// 未消费原因的用户可读文案与行动指引(与后端 clause_coverage_diagnostics 三类对齐)
const REASON_META: ReasonMeta[] = [
  { key: 'pendingAssignment', label: '待人工分配', hint: '匹配未找到合适章节，去绑定页手动指定归属' },
  { key: 'unmatched', label: '未匹配', hint: '未关联任何章节，检查约束表述或补充素材' },
  { key: 'pendingGeneration', label: '待生成', hint: '已绑定待生成，生成对应章节后自动覆盖' },
]

export default function WritingClauseCoverageOverview({
  coverage,
  onNavigateBinding,
  onRefresh,
}: WritingClauseCoverageOverviewProps) {
  const [open, setOpen] = useState(false)
  if (!coverage) return null

  const { counted, covered, uncovered } = coverage
  const hasUncovered = uncovered > 0
  const action = getCoverageAction({
    pendingAssignment: coverage.pendingAssignment.count,
    unmatched: coverage.unmatched.count,
  })
  const subTypeRows = Object.entries(coverage.bySubType || {})
    .filter(([, stats]) => (
      stats.counted + stats.reference + stats.checkOnly > 0
    ))
    .sort(([a], [b]) => a.localeCompare(b))

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => {
          if (!hasUncovered) return
          if (open) {
            setOpen(false)
            return
          }
          Promise.resolve(onRefresh?.()).finally(() => setOpen(true))
        }}
        className={[
          'flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors',
          hasUncovered
            ? 'border-amber-200 bg-amber-50 text-amber-800 hover:bg-amber-100 cursor-pointer'
            : 'border-teal-200 bg-teal-50 text-teal-700 cursor-default',
        ].join(' ')}
        title="约束消费概览"
      >
        <span className="font-medium">约束消费</span>
        <span>{covered}/{counted}</span>
        {hasUncovered && <span>· {uncovered} 条未消费 ▾</span>}
      </button>

      {open && hasUncovered && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-20 mt-1 w-80 rounded-lg border bg-white p-3 shadow-lg">
            <div className="mb-2 text-xs font-medium text-slate-800">
              {uncovered} 条约束尚未进入正文，原因如下
            </div>
            <ul className="space-y-2">
              {REASON_META.map((meta) => {
                const group = coverage[meta.key]
                if (group.count <= 0) return null
                return (
                  <li key={meta.key} className="text-xs">
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-slate-700">{meta.label}</span>
                      <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">
                        {group.count} 条
                      </span>
                    </div>
                    <p className="mt-0.5 text-slate-500">{meta.hint}</p>
                  </li>
                )
              })}
            </ul>
            {subTypeRows.length > 0 && (
              <div className="mt-3 border-t pt-2">
                <div className="mb-1 text-xs font-medium text-slate-700">按类型消费率</div>
                <ul className="max-h-40 space-y-1 overflow-y-auto pr-1">
                  {subTypeRows.map(([subType, stats]) => (
                    <li key={subType} className="flex items-center justify-between gap-2 text-xs text-slate-600">
                      <span className="truncate">{SUB_TYPE_LABELS[subType] || subType}</span>
                      <span className="shrink-0 text-slate-500">
                        {stats.covered}/{stats.counted}
                        {stats.reference > 0 ? ` · 参考${stats.reference}` : ''}
                        {stats.checkOnly > 0 ? ` · 检查${stats.checkOnly}` : ''}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {action && onNavigateBinding && (
              <button
                type="button"
                onClick={() => {
                  setOpen(false)
                  onNavigateBinding(action.focus)
                }}
                className="mt-3 w-full rounded-md bg-teal-600 px-2 py-1.5 text-xs text-white hover:bg-teal-700"
              >
                {action.label}
              </button>
            )}
          </div>
        </>
      )}
    </div>
  )
}
