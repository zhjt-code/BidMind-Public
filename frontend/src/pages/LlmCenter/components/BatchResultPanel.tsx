import React from 'react'
import { X, CheckCircle2, XCircle, AlertTriangle, MapPin } from 'lucide-react'
import type { BatchRunResponse, BatchRunItem } from '@/services/llmCenterApi'

interface BatchResultPanelProps {
  result: BatchRunResponse
  onLocateNode: (item: BatchRunItem) => void
  onPublishPassed: () => void
  publishingPassed: boolean
  onDismiss: () => void
}

export const BatchResultPanel: React.FC<BatchResultPanelProps> = ({
  result, onLocateNode, onPublishPassed, publishingPassed, onDismiss,
}) => {
  const { summary, items } = result
  const failedItems = items.filter(i => i.status === 'validation_failed' || i.status === 'publish_failed')
  const passedItems = items.filter(i => i.status === 'validated' || i.status === 'published')

  const allPublished = summary.published > 0 && summary.validated_failed === 0

  return (
    <div className={`rounded-lg border p-4 mb-4 ${
      allPublished ? 'bg-green-50 border-green-200' : failedItems.length > 0 ? 'bg-amber-50 border-amber-200' : 'bg-blue-50 border-blue-200'
    }`}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          {allPublished ? (
            <CheckCircle2 className="h-5 w-5 text-green-600" />
          ) : failedItems.length > 0 ? (
            <AlertTriangle className="h-5 w-5 text-amber-600" />
          ) : (
            <CheckCircle2 className="h-5 w-5 text-blue-600" />
          )}
          <span className="text-sm font-medium text-gray-900">
            批量操作结果
          </span>
        </div>
        <button onClick={onDismiss} className="p-1 rounded hover:bg-black/5">
          <X className="h-4 w-4 text-gray-400" />
        </button>
      </div>

      <div className="flex flex-wrap gap-3 text-xs mb-3">
        <span className="px-2 py-1 rounded bg-gray-100 text-gray-700">
          共 {summary.total} 个节点
        </span>
        {summary.validated_passed > 0 && (
          <span className="px-2 py-1 rounded bg-green-100 text-green-700">
            {summary.validated_passed} 通过
          </span>
        )}
        {summary.validated_failed > 0 && (
          <span className="px-2 py-1 rounded bg-red-100 text-red-700">
            {summary.validated_failed} 失败
          </span>
        )}
        {summary.published > 0 && (
          <span className="px-2 py-1 rounded bg-blue-100 text-blue-700">
            {summary.published} 已发布
          </span>
        )}
      </div>

      {allPublished && (
        <p className="text-sm text-green-700">
          {summary.published} 个节点全部校验通过并已发布。
        </p>
      )}

      {failedItems.length > 0 && (
        <div className="space-y-2 mb-3">
          <p className="text-sm text-amber-700">
            {passedItems.filter(i => i.status === 'validated').length > 0
              ? `${passedItems.filter(i => i.status === 'validated').length} 个节点已通过校验，${failedItems.length} 个节点失败。可先处理失败项，或直接发布已通过节点。`
              : `${failedItems.length} 个节点校验失败：`
            }
          </p>
          {failedItems.map(item => (
            <div key={item.node_key} className="flex items-start gap-2 p-2 bg-white rounded border border-red-200">
              <XCircle className="h-4 w-4 text-red-500 mt-0.5 shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-gray-900 truncate">{item.path_text}</span>
                  <button
                    onClick={() => onLocateNode(item)}
                    className="shrink-0 flex items-center gap-1 px-1.5 py-0.5 text-[10px] text-primary hover:bg-primary/5 rounded font-medium"
                  >
                    <MapPin className="h-3 w-3" /> 定位
                  </button>
                </div>
                {item.checks.filter(c => !c.passed).map((c, ci) => (
                  <p key={ci} className="text-[11px] text-red-600 mt-0.5">{c.message}</p>
                ))}
                {item.error_message && (
                  <p className="text-[11px] text-red-600 mt-0.5">{item.error_message}</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {result.can_publish_passed && (
        <button
          onClick={onPublishPassed}
          disabled={publishingPassed}
          className="px-4 py-2 text-xs font-medium text-white bg-green-600 rounded-md hover:bg-green-700 disabled:opacity-50"
        >
          {publishingPassed ? '发布中...' : `发布已通过的 ${passedItems.filter(i => i.status === 'validated').length} 个节点`}
        </button>
      )}
    </div>
  )
}
