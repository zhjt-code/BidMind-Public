import React, { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { opsService, type LlmReliabilityResponse } from '@/services/opsApi'
import TrendLine from './TrendLine'
import ReliabilityFailureDrawer from './ReliabilityFailureDrawer'
import { Loader2, ShieldCheck } from 'lucide-react'
import {
  LLM_GROUP_OPTIONS,
  LLM_COVERAGE_DIM_LABELS,
  formatCoveragePct,
  formatLlmOutcomeLabel,
  isFailureOutcome,
} from '../reliabilityLabels'
import { llmFinalValidKpi } from '../dutyLogic'
import { KPI_FRAME, verdictBarClass } from '../dutyVisual'

/**
 * P0-A: LLM 可靠性面板
 *
 * 数据来自 llm_attempt_events 事实表(跨进程、跨重启、仅生产流量),
 * 明确区分"逻辑调用"与"物理请求"两个口径。
 */

interface Props {
  window: string
  compact?: boolean
}

function pct(value: number): string {
  return `${(value * 100).toFixed(1)}%`
}

function rateTone(value: number, warn: number, critical: number): string {
  if (value >= critical) return 'text-rose-600'
  if (value >= warn) return 'text-amber-600'
  return 'text-gray-900'
}

const LlmReliabilityPanel: React.FC<Props> = ({ window, compact = false }) => {
  const [groupBy, setGroupBy] = useState<string>('model')
  const [drillOutcome, setDrillOutcome] = useState<string | null>(null)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['llm-reliability', window, groupBy],
    queryFn: () => opsService.getLlmReliability(window, groupBy),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })

  const reliability = data as LlmReliabilityResponse | undefined
  const totals = reliability?.totals
  const coverage = reliability?.label_coverage

  const lowCoverageDims = useMemo(() => {
    if (!coverage?.dimensions) return []
    return Object.entries(coverage.dimensions)
      .filter(([, dim]) => dim.coverage_rate < 0.95)
      .map(([name, dim]) => ({
        name,
        label: LLM_COVERAGE_DIM_LABELS[name] || name,
        rate: dim.coverage_rate,
        notCollected: dim.not_collected,
        unrecognized: dim.unrecognized,
      }))
  }, [coverage])

  if (compact) {
    const compactKpi = llmFinalValidKpi(totals?.logical_calls, totals?.final_valid_rate, isError)
    return (
      <div className={`${KPI_FRAME} ${verdictBarClass(compactKpi.verdict)}`}>
        <div className="text-sm font-medium text-gray-700 mb-3">LLM 可靠性</div>
        {isError ? (
          <div className="text-sm text-rose-700">加载失败</div>
        ) : isLoading || !totals ? (
          <div className="flex items-center gap-2 text-gray-400 text-sm">
            <Loader2 className="h-4 w-4 animate-spin" />
            加载中…
          </div>
        ) : (
          <div className="grid grid-cols-4 gap-3">
            <div>
              <div className="text-xs text-gray-500">最终有效率</div>
              <div className={`text-lg font-semibold tabular-nums ${totals.logical_calls > 0 && totals.final_valid_rate < 0.95 ? 'text-rose-600' : 'text-gray-900'}`}>
                {totals.logical_calls > 0 ? pct(totals.final_valid_rate) : '—'}
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-500">重试率</div>
              <div className={`text-lg font-semibold tabular-nums ${rateTone(totals.retry_rate, 0.1, 0.3)}`}>
                {totals.logical_calls > 0 ? pct(totals.retry_rate) : '—'}
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-500">空响应率</div>
              <div className={`text-lg font-semibold tabular-nums ${rateTone(totals.empty_rate, 0.02, 0.1)}`}>
                {totals.physical_attempts > 0 ? pct(totals.empty_rate) : '—'}
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-500">调用 P95</div>
              <div className="text-lg font-semibold tabular-nums">
                {totals.latency_p95_ms > 0 ? `${(totals.latency_p95_ms / 1000).toFixed(1)}s` : '—'}
              </div>
            </div>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-primary" />
          <span className="text-sm font-medium text-gray-700">LLM 可靠性 (生产流量)</span>
          <span className="text-xs text-gray-400">
            事实表口径 · 跨进程 · 测试流量已隔离
          </span>
        </div>
        <div className="inline-flex rounded-md border border-gray-200 bg-white p-0.5 text-xs">
          {LLM_GROUP_OPTIONS.map((g) => (
            <button
              key={g.key}
              onClick={() => setGroupBy(g.key)}
              className={`px-2 py-0.5 rounded ${
                groupBy === g.key ? 'bg-primary text-white' : 'text-gray-600 hover:bg-gray-50'
              }`}
            >
              {g.label}
            </button>
          ))}
        </div>
      </div>

      {isLoading && (
        <div className="flex items-center gap-2 text-gray-400 py-6 text-sm">
          <Loader2 className="h-4 w-4 animate-spin" />
          加载可靠性数据…
        </div>
      )}

      {isError && !isLoading && (
        <div className="bg-rose-50 border border-rose-200 text-rose-700 p-4 rounded-md text-sm">
          加载失败
        </div>
      )}

      {!isLoading && totals && (
        <>
          {lowCoverageDims.length > 0 && (
            <div className="mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
              标注覆盖率告警：
              {lowCoverageDims.map((d) => (
                <span key={d.name} className="mr-2">
                  {d.label} {formatCoveragePct(d.rate)}
                  {d.notCollected > 0 ? `（未采集 ${d.notCollected}）` : ''}
                  {d.unrecognized > 0 ? `（未识别 ${d.unrecognized}）` : ''}
                </span>
              ))}
            </div>
          )}

          <div className="grid grid-cols-4 lg:grid-cols-8 gap-3 mb-4">
            <div>
              <div className="text-xs text-gray-500">逻辑调用</div>
              <div className="text-lg font-semibold tabular-nums">{totals.logical_calls}</div>
            </div>
            <div>
              <div className="text-xs text-gray-500">物理请求</div>
              <div className="text-lg font-semibold tabular-nums">{totals.physical_attempts}</div>
            </div>
            <div>
              <div className="text-xs text-gray-500">首次有效率</div>
              <div className={`text-lg font-semibold tabular-nums ${totals.logical_calls > 0 && totals.first_valid_rate < 0.9 ? 'text-amber-600' : 'text-gray-900'}`}>
                {totals.logical_calls > 0 ? pct(totals.first_valid_rate) : '—'}
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-500">最终有效率</div>
              <div className={`text-lg font-semibold tabular-nums ${totals.logical_calls > 0 && totals.final_valid_rate < 0.95 ? 'text-rose-600' : 'text-gray-900'}`}>
                {totals.logical_calls > 0 ? pct(totals.final_valid_rate) : '—'}
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-500">重试率</div>
              <div className={`text-lg font-semibold tabular-nums ${rateTone(totals.retry_rate, 0.1, 0.3)}`}>
                {totals.logical_calls > 0 ? pct(totals.retry_rate) : '—'}
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-500">空响应率</div>
              <div className={`text-lg font-semibold tabular-nums ${rateTone(totals.empty_rate, 0.02, 0.1)}`}>
                {totals.physical_attempts > 0 ? pct(totals.empty_rate) : '—'}
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-500">结构化失败率</div>
              <div className={`text-lg font-semibold tabular-nums ${rateTone(totals.structured_fail_rate, 0.02, 0.1)}`}>
                {totals.structured_attempts > 0 ? pct(totals.structured_fail_rate) : '—'}
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-500">调用 P95</div>
              <div className="text-lg font-semibold tabular-nums">
                {totals.latency_p95_ms > 0 ? `${(totals.latency_p95_ms / 1000).toFixed(1)}s` : '—'}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4 mb-4">
            <div>
              <div className="text-xs text-gray-500 mb-1">物理请求趋势</div>
              <TrendLine
                data={(reliability?.timeseries || []).map((b) => ({
                  ts: new Date(b.bucket).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
                  value: b.attempts,
                }))}
                height={120}
                showAxis
                yLabel="请求数"
                unit="次"
              />
            </div>
            <div>
              <div className="text-xs text-gray-500 mb-1">失败请求趋势</div>
              <TrendLine
                data={(reliability?.timeseries || []).map((b) => ({
                  ts: new Date(b.bucket).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
                  value: b.failed,
                }))}
                color="#f43f5e"
                height={120}
                showAxis
                yLabel="失败数"
                unit="次"
              />
            </div>
          </div>

          {Object.keys(totals.outcome_counts || {}).length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-4">
              {Object.entries(totals.outcome_counts)
                .sort((a, b) => b[1] - a[1])
                .map(([outcome, count]) => {
                  const failure = isFailureOutcome(outcome)
                  const chipClass = outcome === 'valid'
                    ? 'bg-emerald-50 border-emerald-200 text-emerald-700'
                    : 'bg-rose-50 border-rose-200 text-rose-700 hover:bg-rose-100 cursor-pointer'
                  const content = (
                    <>
                      {formatLlmOutcomeLabel(outcome)}
                      <span className="font-semibold tabular-nums">{count}</span>
                    </>
                  )
                  if (failure) {
                    return (
                      <button
                        key={outcome}
                        type="button"
                        onClick={() => setDrillOutcome(outcome)}
                        className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] border ${chipClass}`}
                        title="点击定位失败原因"
                      >
                        {content}
                      </button>
                    )
                  }
                  return (
                    <span
                      key={outcome}
                      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] border ${chipClass}`}
                    >
                      {content}
                    </span>
                  )
                })}
              {totals.degraded_count > 0 && (
                <span className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] border bg-amber-50 border-amber-200 text-amber-700">
                  降级 {totals.degraded_count}
                </span>
              )}
            </div>
          )}

          {(reliability?.breakdown || []).length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-gray-500 border-b border-gray-100">
                    <th className="py-1.5 pr-3 font-medium">
                      {LLM_GROUP_OPTIONS.find((g) => g.key === groupBy)?.label}
                    </th>
                    <th className="py-1.5 pr-3 font-medium text-right">物理请求</th>
                    <th className="py-1.5 pr-3 font-medium text-right">有效率</th>
                    <th className="py-1.5 pr-3 font-medium text-right">空响应</th>
                    <th className="py-1.5 pr-3 font-medium text-right">结构化失败</th>
                    <th className="py-1.5 font-medium text-right">P95</th>
                  </tr>
                </thead>
                <tbody>
                  {(reliability?.breakdown || []).map((row) => {
                    const display = row.display_name || row.key
                    const technicalHint =
                      row.raw_key && row.display_name && row.display_name !== row.raw_key
                        ? row.raw_key
                        : null
                    return (
                      <tr key={`${row.raw_key ?? row.key}`} className="border-b border-gray-50 last:border-0">
                        <td className="py-1.5 pr-3 text-gray-800 max-w-[280px]">
                          <div className="truncate" title={technicalHint || display}>{display}</div>
                        </td>
                        <td className="py-1.5 pr-3 text-right tabular-nums">{row.attempts}</td>
                        <td className={`py-1.5 pr-3 text-right tabular-nums ${row.valid_rate < 0.9 ? 'text-rose-600' : 'text-gray-700'}`}>
                          {pct(row.valid_rate)}
                        </td>
                        <td className="py-1.5 pr-3 text-right tabular-nums text-gray-600">{row.empty_count}</td>
                        <td className="py-1.5 pr-3 text-right tabular-nums text-gray-600">{row.structured_fail_count}</td>
                        <td className="py-1.5 text-right tabular-nums text-gray-600">
                          {row.latency_p95_ms > 0 ? `${(row.latency_p95_ms / 1000).toFixed(1)}s` : '—'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="text-xs text-gray-400 text-center py-3">
              当前窗口内暂无生产 LLM 调用记录
            </div>
          )}

          {(reliability?.recorder?.dropped ?? 0) > 0 && (
            <div className="mt-3 text-[11px] text-amber-600">
              注意: 记录器已丢弃 {reliability?.recorder.dropped} 条事件(队列溢出或写入失败)，当前统计可能低估。
            </div>
          )}
        </>
      )}

      {drillOutcome && (
        <ReliabilityFailureDrawer
          window={window}
          outcome={drillOutcome}
          onClose={() => setDrillOutcome(null)}
        />
      )}
    </div>
  )
}

export default LlmReliabilityPanel
