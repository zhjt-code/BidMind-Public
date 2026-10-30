/**
 * 风险告知独立容器（不参与审查，建议核对）。
 *
 * 职责: 展示 isMandatory 折叠条款（含否决/程序风险语义），位于主审查区与普通参考条款之间；
 *       展开或行内交互时通过 onViewed 上报本会话已查看。
 * 不做: 不参与主审查筛选/计数；不混入普通参考条款；不持有 PDF 弹窗状态。
 */
import React, { useState } from 'react'
import type { Requirement } from '../../requirementReviewTransforms'
import { FoldedRequirementRow } from './FoldedRequirementRow'

export interface RiskNoticeSectionProps {
  items: Requirement[]
  onViewed: () => void
  onPromote: (id: number) => void
  onDelete: (id: number) => void
  tenderDocId: number | null
  onOpenPdfPreview: (req: Requirement, page: number) => void
  onShowAllPages: (req: Requirement) => void
  /** 外部请求展开（如确认弹窗「返回查看」） */
  expandRequest?: boolean
  onExpandRequestConsumed?: () => void
}

export const RiskNoticeSection: React.FC<RiskNoticeSectionProps> = ({
  items,
  onViewed,
  onPromote,
  onDelete,
  tenderDocId,
  onOpenPdfPreview,
  onShowAllPages,
  expandRequest = false,
  onExpandRequestConsumed,
}) => {
  const [collapsed, setCollapsed] = useState(true)

  React.useEffect(() => {
    if (!expandRequest) return
    setCollapsed(false)
    onViewed()
    onExpandRequestConsumed?.()
    window.requestAnimationFrame(() => {
      document.getElementById('risk-notice-section')?.scrollIntoView({
        behavior: 'smooth',
        block: 'start',
      })
    })
  }, [expandRequest, onViewed, onExpandRequestConsumed])

  if (items.length === 0) return null

  const markViewedAndToggle = () => {
    const next = !collapsed
    setCollapsed(next)
    if (!next) onViewed()
  }

  return (
    <div id="risk-notice-section" className="mt-4 bg-white rounded-lg shadow-sm border border-red-100">
      <button
        type="button"
        onClick={markViewedAndToggle}
        className="w-full px-4 py-3 flex items-center justify-between bg-red-50/60 hover:bg-red-50 transition-colors text-left rounded-t-lg"
      >
        <div className="flex items-center gap-2">
          <svg
            className={`w-4 h-4 text-red-400 transition-transform ${collapsed ? '-rotate-90' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
          <span aria-hidden className="inline-block w-2.5 h-2.5 rounded-full bg-red-500" />
          <span className="font-medium text-red-700 text-sm">风险告知（不参与审查，建议核对）</span>
          <span className="text-xs text-red-400">{items.length} 条</span>
        </div>
        <span className="text-xs text-gray-500">
          行为/程序性废标条款，需现场或多文件核验；串通围标类可通过串标审查功能交叉核验
        </span>
      </button>

      {!collapsed && (
        <ul className="divide-y divide-gray-100 bg-gray-50/40">
          {items.map((req) => (
            <FoldedRequirementRow
              key={req.id}
              req={req}
              risk
              tenderDocId={tenderDocId}
              onPromote={onPromote}
              onDelete={onDelete}
              onOpenPdfPreview={onOpenPdfPreview}
              onShowAllPages={onShowAllPages}
              onInteract={onViewed}
            />
          ))}
        </ul>
      )}
    </div>
  )
}
