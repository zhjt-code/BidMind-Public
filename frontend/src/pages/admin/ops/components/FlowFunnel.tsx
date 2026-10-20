import React, { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { opsService, type FlowFunnelResponse } from '@/services/opsApi'
import { formatOutcomeLabel, formatTaskTypeLabel } from '../labels'
import { Loader2, RefreshCw } from 'lucide-react'
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
  LineChart,
  Line,
} from 'recharts'

const WINDOWS: Array<{ key: string; label: string }> = [
  { key: '1h', label: '近1小时' },
  { key: '24h', label: '近24小时' },
  { key: '7d', label: '近7天' },
  { key: '30d', label: '近30天' },
]

interface Props {
  flow: 'tender' | 'collusion' | 'writer' | 'process'
  title: string
  subtitle?: string
}

function formatDuration(ms: number): string {
  if (!ms || !Number.isFinite(ms)) return '-'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60_000).toFixed(1)}min`
}

const FlowFunnel: React.FC<Props> = ({ flow, title, subtitle }) => {
  const [window, setWindow] = useState<string>('24h')
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['flow-funnel', flow, window],
    queryFn: () => opsService.getFlowFunnel(flow, window),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })

  const resp = data as FlowFunnelResponse | undefined

  return (
    <div className="space-y-6">
      {/* 顶部 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">{title}</h1>
          {subtitle && <div className="text-xs text-gray-500 mt-1">{subtitle}</div>}
        </div>
        <div className="flex items-center gap-2">
          <div className="inline-flex rounded-md border border-gray-200 bg-white p-0.5 text-sm">
            {WINDOWS.map((w) => (
              <button
                key={w.key}
                onClick={() => setWindow(w.key)}
                className={`px-2.5 py-1 rounded ${
                  window === w.key ? 'bg-primary text-white' : 'text-gray-600 hover:bg-gray-50'
                }`}
              >
                {w.label}
              </button>
            ))}
          </div>
          <button
            onClick={() => refetch()}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 text-sm rounded-md border border-gray-200 bg-white text-gray-600 hover:bg-gray-50"
            disabled={isFetching}
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>
      </div>

      {isLoading && (
        <div className="flex items-center gap-2 text-gray-500 py-8">
          <Loader2 className="h-4 w-4 animate-spin" />
          加载中…
        </div>
      )}
      {isError && !isLoading && (
        <div className="bg-rose-50 border border-rose-200 text-rose-700 p-4 rounded-md text-sm">
          漏斗数据加载失败。
        </div>
      )}

      {resp && (
        <>
          {/* 阶段漏斗表 */}
          <div className="bg-white rounded-lg border border-gray-200 p-4">
            <div className="text-sm font-medium text-gray-700 mb-3">任务环节完成情况（所选时间范围）</div>
            {resp.funnel.length === 0 ? (
              <div className="text-sm text-gray-400 py-6 text-center">
                该时间窗口内暂无任务观测数据，请先触发相关业务任务。
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-gray-500 border-b border-gray-200">
                      <th className="py-2 pr-4">任务环节</th>
                      <th className="py-2 pr-4 text-right">启动</th>
                      <th className="py-2 pr-4 text-right">完成</th>
                      <th className="py-2 pr-4 text-right">成功</th>
                      <th className="py-2 pr-4 text-right">失败</th>
                      <th className="py-2 pr-4 text-right">成功率</th>
                      <th className="py-2 pr-4 text-right">平均耗时</th>
                    </tr>
                  </thead>
                  <tbody>
                    {resp.funnel.map((row) => (
                      <tr key={row.task_type} className="border-b border-gray-100 last:border-0">
                        <td className="py-2 pr-4 text-xs text-gray-700">{formatTaskTypeLabel(row.task_type, flow)}</td>
                        <td className="py-2 pr-4 text-right tabular-nums">{row.started}</td>
                        <td className="py-2 pr-4 text-right tabular-nums">{row.ended}</td>
                        <td className="py-2 pr-4 text-right tabular-nums text-emerald-600">{row.success}</td>
                        <td className="py-2 pr-4 text-right tabular-nums text-rose-600">{row.failure}</td>
                        <td className="py-2 pr-4 text-right tabular-nums">
                          {(row.success_ratio * 100).toFixed(1)}%
                        </td>
                        <td className="py-2 pr-4 text-right tabular-nums text-gray-500">
                          {formatDuration(row.avg_duration_ms)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* 图表 */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* 成功率条形图 */}
            <div className="bg-white rounded-lg border border-gray-200 p-4">
              <div className="text-sm font-medium text-gray-700 mb-3">各环节完成结果</div>
              {resp.funnel.length > 0 ? (
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart
                    data={resp.funnel.map((f) => ({
                      name: formatTaskTypeLabel(f.task_type, flow),
                      success: f.success,
                      failure: f.failure,
                      other: f.other,
                    }))}
                  >
                    <CartesianGrid stroke="#f1f5f9" />
                    <XAxis dataKey="name" fontSize={11} />
                    <YAxis fontSize={11} />
                    <Tooltip />
                    <Legend />
                    <Bar dataKey="success" stackId="a" fill="#10b981" name="成功" />
                    <Bar dataKey="failure" stackId="a" fill="#f43f5e" name="失败" />
                    <Bar dataKey="other" stackId="a" fill="#94a3b8" name="其他" />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <div className="h-[220px] flex items-center justify-center text-sm text-gray-400">
                  暂无数据
                </div>
              )}
            </div>

            {/* 时间序列 */}
            <div className="bg-white rounded-lg border border-gray-200 p-4">
              <div className="text-sm font-medium text-gray-700 mb-3">按时间查看完成情况</div>
              {resp.time_series.length > 0 ? (
                <ResponsiveContainer width="100%" height={220}>
                  <LineChart
                    data={resp.time_series.map((p) => ({
                      ts: new Date(p.ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
                      total: p.total,
                      success: p.counts.success ?? 0,
                      failure: p.counts.failure ?? 0,
                    }))}
                  >
                    <CartesianGrid stroke="#f1f5f9" />
                    <XAxis dataKey="ts" fontSize={11} />
                    <YAxis fontSize={11} />
                    <Tooltip />
                    <Legend />
                    <Line type="monotone" dataKey="success" stroke="#10b981" name="成功" dot={false} />
                    <Line type="monotone" dataKey="failure" stroke="#f43f5e" name="失败" dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              ) : (
                <div className="h-[220px] flex items-center justify-center text-sm text-gray-400">
                  暂无数据
                </div>
              )}
            </div>
          </div>

          {/* 任务终态分布 */}
          <div className="bg-white rounded-lg border border-gray-200 p-4">
            <div className="text-sm font-medium text-gray-700 mb-3">最终结果分布</div>
            <div className="flex flex-wrap gap-3">
              {Object.entries(resp.outcome_distribution).map(([oc, cnt]) => (
                <div
                  key={oc}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-gray-50 text-sm"
                >
                  <span className="text-gray-500">{formatOutcomeLabel(oc)}</span>
                  <span className="tabular-nums font-medium text-gray-900">{cnt}</span>
                </div>
              ))}
              {Object.keys(resp.outcome_distribution).length === 0 && (
                <div className="text-xs text-gray-400">暂无终态记录</div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

export default FlowFunnel
