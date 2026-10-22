/**
 * 参考条款折叠区（P1 商务降噪）。
 *
 * 职责: 把普通 informational / procedural 参考条款按分类折叠展示；
 *       支持外部跳转到指定分类并展开；提供「升为审查项」恢复审查。
 * 不做: 不展示风险告知（已独立到 RiskNoticeSection）；不参与主审查筛选；不持有 PDF 弹窗状态。
 */
import React, { useEffect, useState } from 'react'
import { getCategoryColorClasses } from '@/lib/constants'
import { groupRequirementsByCategory } from '../helpers'
import type { Requirement } from '../../requirementReviewTransforms'
import { FoldedRequirementRow } from './FoldedRequirementRow'

export interface FoldedRequirementsSectionProps {
  items: Requirement[]
  onPromote: (id: number) => void
  onDelete: (id: number) => void
  /** 项目级招标文档 ID，用于无 documentId 字段的历史 requirement 兜底 */
  tenderDocId: number | null
  /** 来源页码 chip 点击 → 打开主 PDF 预览（复用主表成熟方案） */
  onOpenPdfPreview: (req: Requirement, page: number) => void
  /** "全部 N 页" 文字按钮 → 打开 AllSourcePagesDialog */
  onShowAllPages: (req: Requirement) => void
  /** 外部请求跳转到指定分类标签；消费后应置空 */
  jumpToCategory?: string | null
  onJumpConsumed?: () => void
}

export const FoldedRequirementsSection: React.FC<FoldedRequirementsSectionProps> = ({
  items,
  onPromote,
  onDelete,
  tenderDocId,
  onOpenPdfPreview,
  onShowAllPages,
  jumpToCategory = null,
  onJumpConsumed,
}) => {
  const [collapsed, setCollapsed] = useState(true)
  // 分类子分组展开态：默认全部收起，用户按需点击展开（label 在集合中表示已展开）
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set())

  useEffect(() => {
    if (!jumpToCategory) return
    setCollapsed(false)
    setExpandedGroups((prev) => {
      const next = new Set(prev)
      next.add(jumpToCategory)
      return next
    })
    const targetId = `reference-group-${jumpToCategory}`
    window.requestAnimationFrame(() => {
      document.getElementById(targetId)?.scrollIntoView({
        behavior: 'smooth',
        block: 'start',
      })
    })
    onJumpConsumed?.()
  }, [jumpToCategory, onJumpConsumed])

  if (items.length === 0) return null

  const groups = groupRequirementsByCategory(items)

  const toggleGroup = (label: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev)
      if (next.has(label)) next.delete(label)
      else next.add(label)
      return next
    })
  }

  return (
    <div id="reference-requirements-section" className="mt-4 bg-white rounded-lg shadow">
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        className="w-full px-4 py-3 flex items-center justify-between bg-gray-50 hover:bg-gray-100 transition-colors text-left rounded-t-lg"
      >
        <div className="flex items-center gap-2">
          <svg
            className={`w-4 h-4 text-gray-400 transition-transform ${collapsed ? '-rotate-90' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
          <span className="font-medium text-gray-700 text-sm">参考条款（不参与审查）</span>
          <span className="text-xs text-gray-400">{items.length} 条</span>
        </div>
        <span className="text-xs text-gray-400">
          合同文本 / 信息告知 / 程序履约，已按类折叠
        </span>
      </button>

      {!collapsed && (
        <div className="divide-y divide-gray-100">
          {groups.map((group) => {
            const groupExpanded = expandedGroups.has(group.label)
            return (
              <div key={group.label} id={`reference-group-${group.label}`}>
                <button
                  type="button"
                  onClick={() => toggleGroup(group.label)}
                  className="w-full px-4 py-2.5 flex items-center gap-2 bg-white hover:bg-gray-50 transition-colors text-left"
                >
                  <svg
                    className={`w-3.5 h-3.5 text-gray-400 transition-transform ${groupExpanded ? '' : '-rotate-90'}`}
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                  <span
                    aria-hidden
                    className={`inline-block w-2.5 h-2.5 rounded-full ${getCategoryColorClasses(group.label).dot}`}
                  />
                  <span className="font-medium text-gray-600 text-sm">{group.label}</span>
                  <span className="text-xs text-gray-400">{group.items.length} 条</span>
                </button>

                {groupExpanded && (
                  <ul className="divide-y divide-gray-100 bg-gray-50/40">
                    {group.items.map((req) => (
                      <FoldedRequirementRow
                        key={req.id}
                        req={req}
                        risk={false}
                        tenderDocId={tenderDocId}
                        onPromote={onPromote}
                        onDelete={onDelete}
                        onOpenPdfPreview={onOpenPdfPreview}
                        onShowAllPages={onShowAllPages}
                      />
                    ))}
                  </ul>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
