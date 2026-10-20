/**
 * 招标要求来源页码渲染（可点击跳转招标原文）。
 *
 * 职责: 把单条要求的 sourcePages 渲染为统一的可点页码 chip（单页直达 / 超量折叠到
 *       AllSourcePagesDialog），是"来源页码→PDF 预览"成熟方案的唯一展示出口。
 * 不做: 不持有预览弹窗状态（由 useRequirementPdfPreview 统管），不做页码归一化。
 *
 * 抽取自 RequirementTable.renderSourcePages：原为单组件内部 helper，现因
 * FoldedRequirementsSection（参考条款折叠区）需复用同一交互而上提为共享组件，
 * 保证主审查表与折叠区行为一致、避免第二份会漂移的副本。
 */
import React from 'react'
import SourcePageBadge from '@/components/SourcePageBadge'
import type { Requirement } from '../../requirementReviewTransforms'
import { SOURCE_PAGES_INLINE_LIMIT } from '../constants'

export interface RequirementSourcePagesProps {
  requirement: Requirement
  /** 项目级招标文档 ID，用于无 documentId 字段的历史 requirement 兜底 */
  tenderDocId: number | null
  /** 来源页码 chip 点击 → 打开主 PDF 预览 */
  onOpenPdfPreview: (req: Requirement, page: number) => void
  /** "全部 N 页" 文字按钮 → 打开 AllSourcePagesDialog */
  onShowAllPages: (req: Requirement) => void
}

export const RequirementSourcePages: React.FC<RequirementSourcePagesProps> = ({
  requirement: req,
  tenderDocId,
  onOpenPdfPreview,
  onShowAllPages,
}) => {
  if (req.sourcePages.length === 0) {
    return <div className="mt-2 text-xs text-gray-400">暂无来源页码</div>
  }
  const docId = req.documentId ?? tenderDocId
  const canPreview = docId != null
  const inlinePages = req.sourcePages.slice(0, SOURCE_PAGES_INLINE_LIMIT)
  const overflowCount = req.sourcePages.length - inlinePages.length
  const isMerged = (req.mergedCount ?? 0) > 1 || req.sourcePages.length > 1
  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-gray-500">
      <span className="text-gray-500">来源页码:</span>
      {inlinePages.map((page) => (
        <SourcePageBadge
          key={page}
          label={`P${page}`}
          onClick={
            canPreview
              ? (e) => {
                  e.stopPropagation()
                  onOpenPdfPreview(req, page)
                }
              : undefined
          }
          title={
            canPreview
              ? `打开招标文件第 ${page} 页`
              : '招标文档未就绪，无法打开预览'
          }
        />
      ))}
      {overflowCount > 0 && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            onShowAllPages(req)
          }}
          className="px-1.5 py-0.5 rounded text-primary hover:bg-primary/10 hover:underline transition-colors"
          title="查看全部来源页码"
        >
          全部 {req.sourcePages.length} 页
        </button>
      )}
      {isMerged && (
        <span
          className="ml-1 text-amber-600"
          title="该要求由多处原文合并，建议逐页核对"
        >
          · 多处合并，请逐页核对
        </span>
      )}
    </div>
  )
}

export default RequirementSourcePages
