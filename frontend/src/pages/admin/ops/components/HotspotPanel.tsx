import React, { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { opsService, type HotspotItem, type HotspotsResponse } from '@/services/opsApi'
import { Loader2, Flame, RefreshCw, ExternalLink, Play } from 'lucide-react'
import { Link } from 'react-router-dom'
import HealthBadge, { VerdictType } from './HealthBadge'
import { formatBusinessLabel, formatLayerLabel, formatPillarLabel } from '../labels'
import { toast } from 'sonner'
import { isLlmCenterEnabled } from '@/config/features'

interface Props {
  window?: string
}

const HotspotPanel: React.FC<Props> = ({ window = '24h' }) => {
  const [selected, setSelected] = useState<HotspotItem | null>(null)

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['hotspots', window],
    queryFn: () => opsService.getMetricsHotspots(window, 10),
    staleTime: 30_000,
    refetchInterval: 120_000,
  })

  const triggerReg = useMutation({
    mutationFn: (flow?: string) => opsService.triggerRegression(flow),
    onSuccess: (res) => {
      const jobId = (res as { job_id?: string | null })?.job_id
      toast.success(`已触发回归校验${jobId ? `（任务号 ${jobId}）` : ''}`)
    },
    onError: (e: unknown) => {
      toast.error(`触发失败: ${(e as Error).message || '未知错误'}`)
    },
  })

  const resp = data as HotspotsResponse | undefined
  const items = resp?.items ?? []

  return (
    <div className="bg-white rounded-lg border border-gray-200">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
        <div className="flex items-center gap-2">
          <Flame className="h-4 w-4 text-rose-500" />
          <span className="text-sm font-medium text-gray-700">优先优化项（前10）</span>
          {resp && <span className="text-xs text-gray-400">共 {items.length} 项</span>}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => triggerReg.mutate(undefined)}
            disabled={triggerReg.isPending}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md border border-gray-200 bg-white text-gray-600 hover:bg-gray-50 disabled:opacity-50"
          >
            <Play className="h-3 w-3" />
            触发回归
          </button>
          <button
            onClick={() => refetch()}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md border border-gray-200 bg-white text-gray-600 hover:bg-gray-50"
            disabled={isFetching}
          >
            <RefreshCw className={`h-3 w-3 ${isFetching ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center gap-2 text-gray-500 py-6">
          <Loader2 className="h-4 w-4 animate-spin" />
          加载中…
        </div>
      ) : items.length === 0 ? (
        <div className="text-sm text-gray-400 text-center py-8">
          当前时间窗口内暂无需要优先处理的异常指标。
        </div>
      ) : (
        <div className="divide-y divide-gray-100">
          {items.map((item) => (
            <button
              key={item.name}
              onClick={() => setSelected(item)}
              className="w-full text-left px-4 py-3 hover:bg-gray-50 flex items-start gap-3"
            >
              <HealthBadge verdict={item.verdict as VerdictType} size="sm" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-gray-900 truncate">{item.description || item.name}</span>
                  <span className="text-xs font-mono text-gray-400">{item.name}</span>
                </div>
                <div className="mt-1 text-xs text-gray-500 truncate">{item.reason}</div>
                <div className="mt-1 flex items-center gap-3 text-[11px] text-gray-400">
                  <span>样本 {item.samples}</span>
                  <span>判定算法 {item.algorithm}</span>
                  {item.source.qualname && (
                    <span className="truncate max-w-xs">
                      {item.source.qualname}
                      {item.source.line ? `:${item.source.line}` : ''}
                    </span>
                  )}
                </div>
              </div>
            </button>
          ))}
        </div>
      )}

      {/* 详情抽屉 */}
      {selected && (
        <div
          className="fixed inset-0 z-50 bg-black/30 flex justify-end"
          onClick={() => setSelected(null)}
        >
          <div
            className="w-full max-w-lg bg-white h-full overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-5 py-4 border-b border-gray-200">
              <div className="flex items-center justify-between">
                <h2 className="text-base font-semibold text-gray-900">优化项详情</h2>
                <button
                  onClick={() => setSelected(null)}
                  className="text-sm text-gray-400 hover:text-gray-700"
                >
                  关闭
                </button>
              </div>
              <div className="mt-1 text-xs font-mono text-gray-500">{selected.name}</div>
            </div>

            <div className="p-5 space-y-5 text-sm">
              <div className="space-y-1">
                <div className="text-xs text-gray-500">描述</div>
                <div>{selected.description || '—'}</div>
              </div>

              <div className="grid grid-cols-2 gap-3 text-xs">
                <div>
                  <div className="text-gray-500">当前状态</div>
                  <HealthBadge verdict={selected.verdict as VerdictType} />
                </div>
                <div>
                  <div className="text-gray-500">判定算法</div>
                  <div className="font-mono">{selected.algorithm}</div>
                </div>
                <div>
                  <div className="text-gray-500">所属层级 / 维度</div>
                  <div>{formatLayerLabel(selected.layer)} / {formatPillarLabel(selected.pillar)}</div>
                </div>
                <div>
                  <div className="text-gray-500">样本</div>
                  <div>{selected.samples}</div>
                </div>
              </div>

              <div className="bg-gray-50 rounded-md p-3 text-xs text-gray-700">
                <div className="font-medium mb-1">触发原因</div>
                <div className="text-gray-600">{selected.reason}</div>
              </div>

              {selected.source.file && (
                <div>
                  <div className="text-xs text-gray-500 mb-1">源码位置</div>
                  <div className="font-mono text-xs break-all text-gray-700">
                    {selected.source.file}
                    {selected.source.line ? `:${selected.source.line}` : ''}
                    <div className="text-gray-500">{selected.source.qualname}</div>
                  </div>
                </div>
              )}

              {selected.change_points && selected.change_points.length > 0 && (
                <div>
                  <div className="text-xs text-gray-500 mb-1">变点</div>
                  <div className="flex flex-wrap gap-1.5">
                    {selected.change_points.map((cp, i) => (
                      <span key={i} className="text-[11px] px-2 py-0.5 rounded bg-rose-50 text-rose-700 font-mono">
                        序号 {cp.index} · {cp.direction}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {selected.related.llm_execution_trace.length > 0 && (
                <div>
                  <div className="text-xs text-gray-500 mb-1">近 10 条模型调用轨迹</div>
                  <div className="space-y-1 text-xs">
                    {selected.related.llm_execution_trace.slice(0, 5).map((t) => (
                      <div key={t.id} className="flex items-center justify-between font-mono text-gray-600">
                        <span>trace#{t.id}</span>
                        <span className="text-gray-400">{t.created_at ?? ''}</span>
                      </div>
                    ))}
                  </div>
                  {isLlmCenterEnabled && (
                    <Link
                      to="/admin/llm-center"
                      className="inline-flex items-center gap-1 mt-1 text-xs text-primary hover:underline"
                    >
                      前往模型调度中心 <ExternalLink className="h-3 w-3" />
                    </Link>
                  )}
                </div>
              )}

              {selected.related.system_error_log.length > 0 && (
                <div>
                  <div className="text-xs text-gray-500 mb-1">近 10 条错误日志</div>
                  <div className="space-y-1 text-xs text-gray-700">
                    {selected.related.system_error_log.slice(0, 5).map((e) => (
                      <div key={e.id} className="flex items-start gap-2">
                        <span className={`font-mono ${e.level === 'CRITICAL' ? 'text-rose-600' : 'text-amber-600'}`}>
                          [{e.level}]
                        </span>
                        <span className="truncate">{e.message}</span>
                      </div>
                    ))}
                  </div>
                  <Link
                    to="/admin/error-logs"
                    className="inline-flex items-center gap-1 mt-1 text-xs text-primary hover:underline"
                  >
                    前往错误日志 <ExternalLink className="h-3 w-3" />
                  </Link>
                </div>
              )}

              {selected.related.prompt_version_changes.length > 0 && (
                <div>
                  <div className="text-xs text-gray-500 mb-1">变点附近 Prompt 版本变更</div>
                  <div className="space-y-1 text-xs text-gray-700">
                    {selected.related.prompt_version_changes.map((pv) => (
                      <div key={pv.id} className="font-mono">
                        {pv.purpose} v{pv.version}
                        {pv.is_active && <span className="ml-1 text-emerald-600">当前生效</span>}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <button
                onClick={() => triggerReg.mutate(selected.business === 'all' ? undefined : selected.business)}
                disabled={triggerReg.isPending}
                className="w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-md bg-primary text-white text-sm hover:bg-primary/90 disabled:opacity-60"
              >
                <Play className="h-4 w-4" />
                对“{formatBusinessLabel(selected.business)}”触发回归校验
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default HotspotPanel
