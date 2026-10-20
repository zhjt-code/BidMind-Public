/**
 * 折叠条款单行（风险告知区与普通参考条款区共用）。
 *
 * 职责: 渲染单条非 actionable 条款的徽标、描述、来源页码与升为审查项/删除操作。
 * 不做: 不持有展开态、不拆分风险/参考、不打开 PDF 弹窗状态。
 */
import React from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { Requirement, ReviewRelevance } from '../../requirementReviewTransforms'
import { RequirementSourcePages } from './RequirementSourcePages'

export interface FoldedRequirementRowProps {
  req: Requirement
  risk: boolean
  tenderDocId: number | null
  onPromote: (id: number) => void
  onDelete: (id: number) => void
  onOpenPdfPreview: (req: Requirement, page: number) => void
  onShowAllPages: (req: Requirement) => void
  /** 可选：行内交互（升为审查项 / 打开原文）时上报，供风险区记录已查看 */
  onInteract?: () => void
}

const RELEVANCE_LABEL: Record<Exclude<ReviewRelevance, 'actionable'>, string> = {
  informational: '信息告知',
  procedural: '程序/履约',
}

/** 评审意义归因 → 展示文案（P2.3；展示层映射，对齐后端 relevance_reason 枚举） */
const REASON_LABEL: Record<string, string> = {
  llm_locus: 'AI判定',
  rule_floor: '规则命中',
  contract_section: '合同区段',
  behavioral_mandatory: '行为性否决',
  manual: '人工降级',
}

export const FoldedRequirementRow: React.FC<FoldedRequirementRowProps> = ({
  req,
  risk,
  tenderDocId,
  onPromote,
  onDelete,
  onOpenPdfPreview,
  onShowAllPages,
  onInteract,
}) => (
  <li className={`px-4 py-3 pl-10 ${risk ? 'bg-red-50/40' : ''}`}>
    <div className="grid grid-cols-12 gap-3 items-start">
      <div className="col-span-2 space-y-1">
        {risk && (
          <Badge variant="destructive" className="text-[10px]">
            否决语义
          </Badge>
        )}
        {req.reviewRelevance !== 'actionable' && (
          <Badge variant="secondary" className="text-[10px]">
            {RELEVANCE_LABEL[req.reviewRelevance]}
          </Badge>
        )}
        {req.relevanceReason && REASON_LABEL[req.relevanceReason] && (
          <Badge
            variant="outline"
            className="text-[10px] text-gray-500"
            title="该条款被折叠为参考条款的判定来源"
          >
            {REASON_LABEL[req.relevanceReason]}
          </Badge>
        )}
      </div>
      <div className="col-span-7">
        <span className={`text-sm leading-relaxed ${risk ? 'text-gray-700' : 'text-gray-600'}`}>
          {req.description}
        </span>
        <RequirementSourcePages
          requirement={req}
          tenderDocId={tenderDocId}
          onOpenPdfPreview={(item, page) => {
            onInteract?.()
            onOpenPdfPreview(item, page)
          }}
          onShowAllPages={(item) => {
            onInteract?.()
            onShowAllPages(item)
          }}
        />
      </div>
      <div className="col-span-3 text-center pt-0.5">
        <div className="flex justify-center space-x-3">
          <Button
            variant="link"
            size="sm"
            className="text-primary h-auto p-0"
            onClick={() => {
              onInteract?.()
              onPromote(req.id)
            }}
            title="将该条款重新纳入 AI 审查打分"
          >
            升为审查项
          </Button>
          <Button
            variant="link"
            size="sm"
            className="text-destructive h-auto p-0"
            onClick={() => onDelete(req.id)}
          >
            删除
          </Button>
        </div>
      </div>
    </div>
  </li>
)
