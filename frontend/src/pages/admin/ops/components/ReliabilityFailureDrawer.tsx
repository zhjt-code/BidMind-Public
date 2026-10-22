import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2, X } from 'lucide-react'
import { opsService, type LlmFailureDrilldownResponse } from '@/services/opsApi'
import { formatLlmOutcomeLabel, formatOpsWindowLabel } from '../reliabilityLabels'

interface Props {
  window: string
  outcome: string
  onClose: () => void
}

const ReliabilityFailureDrawer: React.FC<Props> = ({ window, outcome, onClose }) => {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['llm-failures', window, outcome],
    queryFn: () => opsService.getLlmFailures(window, outcome),
    staleTime: 30_000,
  })

  const payload = data as LlmFailureDrilldownResponse | undefined

  return (
    <div
      className="fixed inset-0 z-50 bg-black/30 flex justify-end"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg bg-white h-full overflow-y-auto shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-gray-900">失败定位</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              {formatLlmOutcomeLabel(outcome)} · {formatOpsWindowLabel(window)}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-gray-400 hover:bg-gray-50 hover:text-gray-700"
            aria-label="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {isLoading && (
          <div className="flex items-center gap-2 text-gray-400 py-10 px-5 text-sm">
            <Loader2 className="h-4 w-4 animate-spin" />
            加载失败明细…
          </div>
        )}

        {isError && (
          <div className="px-5 py-8 text-sm text-rose-600">加载失败明细失败，请稍后重试。</div>
        )}

        {!isLoading && !isError && payload && (
          <div className="px-5 py-4 space-y-6">
            <section>
              <h3 className="text-sm font-medium text-gray-800 mb-2">按模型 × 节点聚合</h3>
              {(payload.aggregates || []).length === 0 ? (
                <div className="text-xs text-gray-400 py-3">当前窗口无该失败类型记录</div>
              ) : (
                <div className="overflow-x-auto border border-gray-100 rounded-md">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs text-gray-500 border-b border-gray-100 bg-gray-50">
                        <th className="py-1.5 px-2 font-medium">模型</th>
                        <th className="py-1.5 px-2 font-medium">节点</th>
                        <th className="py-1.5 px-2 font-medium text-right">次数</th>
                        <th className="py-1.5 px-2 font-medium text-right">P95</th>
                      </tr>
                    </thead>
                    <tbody>
                      {payload.aggregates.map((row, idx) => (
                        <tr key={`${row.model}-${row.node_key}-${idx}`} className="border-b border-gray-50 last:border-0">
                          <td className="py-1.5 px-2 text-gray-800">
                            <div className="truncate max-w-[140px]" title={row.model || undefined}>
                              {row.model_display}
                            </div>
                          </td>
                          <td className="py-1.5 px-2 text-gray-700">
                            <div
                              className="truncate max-w-[160px]"
                              title={row.node_key || row.node_display || undefined}
                            >
                              {row.node_display}
                            </div>
                          </td>
                          <td className="py-1.5 px-2 text-right tabular-nums">{row.attempts}</td>
                          <td className="py-1.5 px-2 text-right tabular-nums text-gray-600">
                            {row.latency_p95_ms > 0 ? `${(row.latency_p95_ms / 1000).toFixed(1)}s` : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <section>
              <h3 className="text-sm font-medium text-gray-800 mb-2">最近失败样本</h3>
              {(payload.samples || []).length === 0 ? (
                <div className="text-xs text-gray-400 py-3">暂无样本</div>
              ) : (
                <ul className="space-y-2">
                  {payload.samples.map((sample) => (
                    <li
                      key={sample.id}
                      className="rounded-md border border-gray-100 bg-gray-50/60 px-3 py-2 text-xs text-gray-700"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium text-rose-700">
                          {formatLlmOutcomeLabel(sample.outcome)}
                        </span>
                        <span className="text-gray-400 tabular-nums">
                          {sample.occurred_at
                            ? new Date(sample.occurred_at).toLocaleString('zh-CN')
                            : '—'}
                        </span>
                      </div>
                      <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-gray-600">
                        <span>模型: {sample.model_name || '未采集'}</span>
                        <span title={sample.provider || undefined}>
                          厂商: {sample.provider_display || sample.provider || '未采集'}
                        </span>
                        <span className="truncate" title={sample.node_key || undefined}>
                          节点: {sample.node_display || '未采集'}
                        </span>
                        <span>HTTP: {sample.http_status ?? '—'}</span>
                        <span className="truncate" title={sample.config_id || undefined}>
                          配置: {sample.config_display || '未采集'}
                        </span>
                        <span>耗时: {sample.latency_ms > 0 ? `${(sample.latency_ms / 1000).toFixed(1)}s` : '—'}</span>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        )}
      </div>
    </div>
  )
}

export default ReliabilityFailureDrawer
