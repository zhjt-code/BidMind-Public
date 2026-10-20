import { useEffect, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import type { SectionDraft } from '@/services/writingApi'
import type { WritingClause } from '@/services/writingClauseTransforms'
import { shortUserFacingClauseText } from './writingClauseDisplay'
import { isCoverageCountedClause } from './writingClauseTabs'

interface WritingClauseCoveragePanelProps {
  clauses: WritingClause[]
  drafts: SectionDraft[]
  currentNodeId: number | null
}

// 节点级面板: 仅展示"本章约束来源"。全局覆盖统计与未消费原因由
// WritingClauseCoverageOverview 在页面顶部统一展示, 避免逐节点重复的黑箱数字。
export default function WritingClauseCoveragePanel({
  clauses,
  drafts,
  currentNodeId,
}: WritingClauseCoveragePanelProps) {
  const [collapsed, setCollapsed] = useState(true)
  const countedClauses = clauses.filter(
    (clause) => clause.confirmed === 1 && isCoverageCountedClause(clause),
  )
  const clauseMap = new Map(countedClauses.map((clause) => [clause.id, clause]))

  const selectedDraft = drafts.find((draft) => draft.outlineNodeId === currentNodeId)
  const currentClauses = (selectedDraft?.sourceClauseIds || [])
    .map((clauseId) => clauseMap.get(clauseId))
    .filter((clause): clause is WritingClause => Boolean(clause))

  useEffect(() => {
    setCollapsed(true)
  }, [currentNodeId])

  return (
    <div className="mb-3 rounded-lg border bg-slate-50 p-3 text-sm">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2 text-left"
        aria-expanded={!collapsed}
        onClick={() => setCollapsed((value) => !value)}
      >
        <span className="text-xs font-medium text-slate-800">
          本章约束来源 · {currentClauses.length}条
        </span>
        <ChevronDown
          className={`h-4 w-4 shrink-0 text-slate-500 transition-transform ${collapsed ? '-rotate-90' : ''}`}
        />
      </button>
      {!collapsed && (
      <div className="mt-2">
        {currentClauses.length > 0 ? (
          <ul className="space-y-1">
            {currentClauses.slice(0, 5).map((clause, index) => (
              <li key={clause.id} className="text-xs text-slate-700">
                <span className="font-medium">{index + 1}.</span>
                {' '}
                {shortUserFacingClauseText(clause.content)}
              </li>
            ))}
            {currentClauses.length > 5 && (
              <li className="text-xs text-slate-500">
                另有 {currentClauses.length - 5} 条约束已绑定到本章
              </li>
            )}
          </ul>
        ) : (
          <p className="text-xs text-slate-500">
            当前章节暂无约束来源记录，请检查绑定或重新生成章节。
          </p>
        )}
      </div>
      )}
    </div>
  )
}
