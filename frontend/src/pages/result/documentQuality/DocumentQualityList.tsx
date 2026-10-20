import React from 'react'
import { Badge } from '@/components/ui/badge'
import { bidPartLabel } from '@/lib/constants'
import type { QaFinding } from './types'
import {
  QA_CATEGORY_LABELS,
  QA_CERTAINTY_LABELS,
  QA_DISPOSITION_LABELS,
  QA_SEVERITY_LABELS,
  findingBidParts,
  firstEvidencePage,
} from './documentQualityTransforms'

/**
 * 职责: 文档质检发现项列表行渲染(中栏)
 * 不做: 不做筛选排序(由父级传入已处理列表)
 */

interface DocumentQualityListProps {
  findings: QaFinding[]
  selectedId: number | null
  onSelect: (id: number) => void
}

const SEVERITY_BADGE: Record<string, string> = {
  P0: 'bg-red-600 text-white hover:bg-red-600',
  P1: 'bg-orange-500 text-white hover:bg-orange-500',
  P2: 'bg-amber-400 text-amber-950 hover:bg-amber-400',
  P3: 'bg-gray-200 text-gray-700 hover:bg-gray-200',
}

const DISPOSITION_CLASS: Record<string, string> = {
  pending: 'text-amber-700 border-amber-300',
  confirmed: 'text-red-700 border-red-300',
  false_positive: 'text-green-700 border-green-300',
  ignored: 'text-gray-500 border-gray-300',
}

const DocumentQualityList: React.FC<DocumentQualityListProps> = ({
  findings, selectedId, onSelect,
}) => {
  if (findings.length === 0) {
    return (
      <div className="p-8 text-center text-gray-400 text-sm">
        当前筛选条件下无发现项
      </div>
    )
  }
  return (
    <div className="divide-y">
      {findings.map(finding => {
        const page = firstEvidencePage(finding)
        const parts = findingBidParts(finding)
        return (
          <div
            key={finding.id}
            role="button"
            tabIndex={0}
            onClick={() => onSelect(finding.id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(finding.id) }
            }}
            className={`w-full text-left pl-4 pr-4 py-3 transition-colors cursor-pointer hover:bg-gray-50 ${
              selectedId === finding.id ? 'bg-primary/5 border-l-2 border-l-primary' : ''
            }`}
          >
            <div className="flex items-start justify-between mb-1 gap-2">
              <div className="flex items-center gap-1.5 min-w-0 flex-wrap">
                <Badge className={`text-[10px] px-1.5 py-0 whitespace-nowrap ${SEVERITY_BADGE[finding.severity] || SEVERITY_BADGE.P3}`}>
                  {finding.severity} {QA_SEVERITY_LABELS[finding.severity] || ''}
                </Badge>
                <Badge variant="outline" className="text-[10px] px-1.5 py-0 whitespace-nowrap">
                  {QA_CATEGORY_LABELS[finding.category] || finding.category}
                </Badge>
                <Badge
                  variant="outline"
                  className={`text-[10px] px-1.5 py-0 whitespace-nowrap ${DISPOSITION_CLASS[finding.disposition] || ''}`}
                >
                  {QA_DISPOSITION_LABELS[finding.disposition] || finding.disposition}
                </Badge>
                {parts.map(part => (
                  <Badge
                    key={part}
                    variant="outline"
                    className="text-[10px] px-1.5 py-0 whitespace-nowrap text-gray-600"
                    title={`质检证据来自${bidPartLabel(part)}`}
                  >
                    {bidPartLabel(part)}
                  </Badge>
                ))}
              </div>
              <div className="flex items-center gap-2 flex-shrink-0 text-xs text-gray-400">
                <span>{QA_CERTAINTY_LABELS[finding.certainty] || finding.certainty}</span>
                {page !== Number.MAX_SAFE_INTEGER && <span>P{page}</span>}
              </div>
            </div>
            <div className="text-sm font-medium text-gray-800">{finding.title}</div>
            <div className="text-xs text-gray-500 line-clamp-2 mt-0.5">{finding.message}</div>
          </div>
        )
      })}
    </div>
  )
}

export default DocumentQualityList
