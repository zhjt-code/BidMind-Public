import React from 'react'
import { AlertTriangle, Info } from 'lucide-react'
import { bidPartLabel } from '@/lib/constants'
import type { DocumentQualityData } from './types'
import {
  QA_IMAGE_PAGE_HINT,
  QA_SCAN_STATUS_LABELS,
  imagePageNotices,
} from './documentQualityTransforms'

/**
 * 职责: 文档质检概览条(问题数/严重项/待处理/扫描覆盖/质检范围说明; 无综合分)
 * 不做: 不计算质量评分、不承担筛选、不展示内部覆盖率计数
 */

interface DocumentQualityStatsProps {
  data: DocumentQualityData
}

const DocumentQualityStats: React.FC<DocumentQualityStatsProps> = ({ data }) => {
  const { scan, summary } = data
  const p0 = summary.bySeverity.P0 ?? 0
  const p1 = summary.bySeverity.P1 ?? 0
  const pending = summary.byDisposition.pending ?? 0
  const checkersFailed = scan.checkers.filter(c => c.status === 'failed')
  const scanAbnormal = !['completed', 'degraded'].includes(scan.status)
  const scopeNotices = imagePageNotices(scan.documents, bidPartLabel)

  return (
    <div className="bg-white rounded-lg shadow mb-4 px-4 py-3">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-4">
          <h2 className="font-medium text-gray-700">质检概览</h2>
          <div className="flex items-center gap-3 text-sm flex-wrap">
            <span className="text-gray-500">
              发现问题 <span className="font-semibold text-gray-800">{summary.total}</span>
            </span>
            <span className={p0 + p1 > 0 ? 'text-red-600 font-medium' : 'text-gray-500'}>
              严重/高 {p0 + p1}
            </span>
            <span className={pending > 0 ? 'text-amber-700 font-medium' : 'text-gray-500'}>
              待处理 {pending}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2 text-xs text-gray-500 flex-wrap">
          {scan.documents.map(doc => (
            <span
              key={doc.documentId}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-gray-50 border"
              title={doc.filename}
            >
              {doc.bidPart ? bidPartLabel(doc.bidPart) : '投标文件'}
              {doc.pageCount != null && <span>· {doc.pageCount}页</span>}
              <span className={doc.status === 'missing' ? 'text-red-500' : 'text-gray-400'}>
                {doc.status === 'completed' ? '已检' : doc.status === 'degraded' ? '已检(降级)' : doc.status === 'missing' ? '未检' : doc.status}
              </span>
            </span>
          ))}
        </div>
      </div>

      {scopeNotices.length > 0 && (
        <div className="mt-2 space-y-0.5">
          {scopeNotices.map(notice => (
            <p
              key={notice.documentId}
              className="flex items-center gap-1 text-xs text-gray-500"
              title={QA_IMAGE_PAGE_HINT}
            >
              <Info className="h-3.5 w-3.5 flex-shrink-0 text-gray-400" aria-hidden="true" />
              <span>{notice.text}</span>
            </p>
          ))}
        </div>
      )}

      {(scanAbnormal || checkersFailed.length > 0 || scan.status === 'degraded') && (
        <div className="mt-2 flex items-start gap-1.5 text-xs rounded border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-amber-800">
          {scanAbnormal ? (
            <AlertTriangle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
          ) : (
            <Info className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
          )}
          <span>
            {QA_SCAN_STATUS_LABELS[scan.status] || scan.status}
            {checkersFailed.length > 0 &&
              `；部分检查未执行: ${checkersFailed.map(c => c.code).join('、')}`}
            {scan.errorMessage && `；${scan.errorMessage}`}
          </span>
        </div>
      )}

      <p className="mt-2 text-[11px] text-gray-400">
        质检结果仅反映文档编制质量, 不参与响应审查评分与结论, 不构成废标判定。
      </p>
    </div>
  )
}

export default DocumentQualityStats
