import React, { useState } from 'react'
import { FileSearch } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import SourcePageBadge from '@/components/SourcePageBadge'
import { documentService } from '@/services/documentApi'
import { bidPartLabel } from '@/lib/constants'
import type { PdfHighlight } from '../pdfPreview/PdfBBoxOverlay'
import PdfEvidencePreviewHost from '../pdfPreview/PdfEvidencePreviewHost'
import { usePdfEvidencePreview } from '../pdfPreview/usePdfEvidencePreview'
import type { QaEvidenceItem, QaFinding } from './types'
import {
  QA_CATEGORY_LABELS,
  QA_CERTAINTY_LABELS,
  QA_DISPOSITION_LABELS,
  QA_SEVERITY_LABELS,
  findingBidParts,
} from './documentQualityTransforms'

/**
 * 职责: 文档质检发现项详情(右栏)——规则说明/证据定位/PDF高亮跳转/人工处置
 * 不做: 不做列表筛选、不直接改数据(处置回调交给父级)
 */

interface DocumentQualityDetailProps {
  finding: QaFinding | null
  projectId: string
  disposing: boolean
  onDispose: (finding: QaFinding, disposition: string, note: string) => void
}

const ROLE_LABELS: Record<string, string> = { primary: '证据A', comparison: '证据B' }

const DocumentQualityDetail: React.FC<DocumentQualityDetailProps> = ({
  finding, projectId, disposing, onDispose,
}) => {
  const [note, setNote] = useState('')
  const preview = usePdfEvidencePreview()

  if (!finding) {
    return (
      <>
        <div className="flex flex-col items-center justify-center h-full text-gray-400 px-6">
          <FileSearch className="h-12 w-12 mb-3 opacity-40" />
          <p className="text-sm text-center">点击中间列表的发现项<br />查看完整详情与证据</p>
        </div>
        <PdfEvidencePreviewHost {...preview.hostProps} />
      </>
    )
  }

  const openEvidence = (item: QaEvidenceItem) => {
    if (!finding || item.documentId == null || item.page == null) return
    const highlights: PdfHighlight[] = finding.evidence.items
      .filter(entry => entry.documentId === item.documentId && entry.page != null && entry.bbox)
      .map(entry => ({ page: entry.page as number, bbox: entry.bbox as PdfHighlight['bbox'] }))
    const excerpts = finding.evidence.items
      .filter(entry => entry.documentId === item.documentId && entry.page === item.page && entry.excerpt)
      .map(entry => entry.excerpt)
    preview.openPreview({
      pdfUrl: documentService.getPreviewUrl(parseInt(projectId), item.documentId),
      page: item.page,
      highlights,
      excerpts,
    })
  }

  const isPair = finding.evidence.kind === 'pair'
  const parts = findingBidParts(finding)

  return (
    <ScrollArea className="h-full min-h-0">
      <div className="p-4 space-y-4">
        <div>
          <div className="flex items-center gap-1.5 flex-wrap mb-2">
            <Badge variant="destructive" className="text-[11px]">
              {finding.severity} {QA_SEVERITY_LABELS[finding.severity] || ''}
            </Badge>
            <Badge variant="outline" className="text-[11px]">
              {QA_CATEGORY_LABELS[finding.category] || finding.category}
            </Badge>
            <Badge variant="secondary" className="text-[11px]">
              {QA_CERTAINTY_LABELS[finding.certainty] || finding.certainty}
            </Badge>
            {parts.map(part => (
              <Badge
                key={part}
                variant="outline"
                className="text-[11px] text-gray-600"
                title={`质检证据来自${bidPartLabel(part)}`}
              >
                {bidPartLabel(part)}
              </Badge>
            ))}
          </div>
          <h3 className="text-base font-semibold text-gray-900">{finding.title}</h3>
          <p className="text-sm text-gray-700 mt-1 whitespace-pre-wrap">{finding.message}</p>
          {finding.suggestion && (
            <p className="text-sm text-blue-700 mt-2 bg-blue-50 border border-blue-100 rounded px-2.5 py-1.5">
              建议：{finding.suggestion}
            </p>
          )}
        </div>

        {(finding.evidence.expected || finding.evidence.actual) && (
          <div className="text-sm rounded border bg-gray-50 px-3 py-2 space-y-0.5">
            {finding.evidence.expected && (
              <div><span className="text-gray-500">取值一：</span>{finding.evidence.expected}</div>
            )}
            {finding.evidence.actual && (
              <div><span className="text-gray-500">取值二：</span>{finding.evidence.actual}</div>
            )}
          </div>
        )}

        <div>
          <p className="text-xs font-semibold text-gray-500 mb-2">证据定位</p>
          <div className={isPair ? 'grid grid-cols-1 lg:grid-cols-2 gap-2' : 'space-y-2'}>
            {finding.evidence.items.map((item, index) => (
              <div key={index} className="rounded border bg-white px-3 py-2">
                <div className="flex items-center justify-between gap-2 mb-1">
                  <span className="text-[11px] text-gray-500">
                    {isPair ? (ROLE_LABELS[item.role] || `证据${index + 1}`) : `证据${index + 1}`}
                    {item.bidPart && ` · ${bidPartLabel(item.bidPart)}`}
                  </span>
                  {item.page != null && (
                    <SourcePageBadge
                      label={`P${item.page}`}
                      onClick={item.documentId != null ? () => openEvidence(item) : undefined}
                      title={item.documentId != null ? '打开 PDF 并高亮该证据' : '缺少文档定位'}
                    />
                  )}
                </div>
                <p className="text-sm text-gray-700 whitespace-pre-wrap break-all">
                  {item.excerpt || '—'}
                </p>
              </div>
            ))}
          </div>
        </div>

        <div className="border-t pt-3">
          <p className="text-xs font-semibold text-gray-500 mb-2">
            人工处置
            <span className="ml-2 font-normal text-gray-400">
              当前：{QA_DISPOSITION_LABELS[finding.disposition] || finding.disposition}
              {finding.dispositionNote && `（${finding.dispositionNote}）`}
            </span>
          </p>
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="处置备注(可选)"
            maxLength={200}
            className="w-full mb-2 px-2.5 py-1.5 text-sm border rounded focus:outline-hidden focus:ring-1 focus:ring-primary"
          />
          <div className="flex items-center gap-2 flex-wrap">
            <Button
              size="sm"
              variant="destructive"
              disabled={disposing || finding.disposition === 'confirmed'}
              onClick={() => onDispose(finding, 'confirmed', note)}
            >
              确认问题
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={disposing || finding.disposition === 'false_positive'}
              onClick={() => onDispose(finding, 'false_positive', note)}
            >
              标记误报
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={disposing || finding.disposition === 'ignored'}
              onClick={() => onDispose(finding, 'ignored', note)}
            >
              忽略
            </Button>
            {finding.disposition !== 'pending' && (
              <Button
                size="sm"
                variant="ghost"
                disabled={disposing}
                onClick={() => onDispose(finding, 'pending', note)}
              >
                恢复待处理
              </Button>
            )}
          </div>
        </div>
      </div>

        <PdfEvidencePreviewHost {...preview.hostProps} />
    </ScrollArea>
  )
}

export default DocumentQualityDetail
