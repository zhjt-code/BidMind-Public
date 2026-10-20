import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, Cpu, Gauge, Loader2, PlayCircle, RefreshCw, ServerCog } from 'lucide-react'
import { opsService, type WorkerActionItem, type WorkerHealthLevel, type WorkerHealthResponse } from '@/services/opsApi'
import HealthBadge, { VerdictType } from './HealthBadge'

const HEALTH_VERDICT: Record<WorkerHealthLevel, VerdictType> = {
  healthy: 'stable',
  degraded: 'warn',
  unhealthy: 'critical',
  offline: 'critical',
}

const HEALTH_LABELS: Record<WorkerHealthLevel, string> = {
  healthy: 'Worker 健康',
  degraded: 'Worker 降级',
  unhealthy: 'Worker 异常',
  offline: 'Worker 离线',
}

const ACTION_STYLES: Record<WorkerActionItem['severity'], string> = {
  critical: 'border-rose-200 bg-rose-50 text-rose-900',
  warn: 'border-amber-200 bg-amber-50 text-amber-900',
  info: 'border-slate-200 bg-slate-50 text-slate-700',
}

function formatReason(reason: string): string {
  const labels: Record<string, string> = {
    critical_function_missing: '关键 Worker function 缺失',
    event_loop_lag_high: 'Worker 事件循环延迟过高',
    worker_draining: 'Worker 正在 draining',
    gpu_free_memory_low: 'GPU 空闲显存偏低',
    gpu_permit_saturated: 'GPU permit 已满',
    fallback_permit_saturated: 'API fallback permit 已满',
  }
  return labels[reason] || reason
}

function formatResourceName(name: string): string {
  const labels: Record<string, string> = {
    process: 'Process',
    llm: 'LLM',
    report: 'Report',
    maintenance: 'Maintenance',
    gpu: 'GPU',
    default: 'Default',
    fallback: 'Fallback',
  }
  return labels[name] || name
}

function copyCommand(command: string): void {
  if (!command || typeof navigator === 'undefined' || !navigator.clipboard) return
  void navigator.clipboard.writeText(command)
}

interface ActionCardProps {
  action: WorkerActionItem
}

const ActionCard: React.FC<ActionCardProps> = ({ action }) => {
  const style = ACTION_STYLES[action.severity] || ACTION_STYLES.info
  return (
    <div className={`rounded-lg border p-3 ${style}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-1.5 text-sm font-semibold">
            {action.severity === 'critical' ? <AlertTriangle className="h-3.5 w-3.5" /> : <PlayCircle className="h-3.5 w-3.5" />}
            {action.title}
          </div>
          <p className="mt-1 text-xs leading-5 opacity-80">{action.description}</p>
        </div>
        {action.requires_supervisor && (
          <span className="shrink-0 rounded-full bg-white/70 px-2 py-0.5 text-[11px] font-medium">
            supervisor
          </span>
        )}
      </div>
      {action.command && (
        <button
          type="button"
          onClick={() => copyCommand(action.command)}
          className="mt-2 w-full rounded-md bg-white/75 px-2 py-1.5 text-left font-mono text-[11px] text-slate-700 hover:bg-white"
          title="点击复制命令"
        >
          {action.command}
        </button>
      )}
    </div>
  )
}

interface WorkerHealthPanelProps {
  className?: string
}

const WorkerHealthPanel: React.FC<WorkerHealthPanelProps> = ({ className = '' }) => {
  const { data, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey: ['ops-worker-health'],
    queryFn: () => opsService.getWorkerHealth(),
    staleTime: 15_000,
    refetchInterval: 30_000,
  })

  const summary = data as WorkerHealthResponse | undefined
  const health = summary?.health ?? 'offline'
  const missingCount = summary?.missing_functions?.length ?? 0
  const actionItems = summary?.action_items ?? []

  return (
    <section id="ops-worker-health" className={`overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm ${className}`}>
      <div className="border-b border-slate-100 bg-slate-950 px-4 py-4 text-white">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-cyan-400/15 text-cyan-200 ring-1 ring-cyan-300/20">
              <ServerCog className="h-5 w-5" />
            </div>
            <div>
              <div className="text-sm font-semibold">Worker 生命周期处置台</div>
              <div className="text-xs text-slate-400">心跳覆盖、资源许可、GPU 压力与下一步动作</div>
            </div>
          </div>
          <button
            type="button"
            onClick={() => refetch()}
            disabled={isFetching}
            className="inline-flex items-center gap-1.5 rounded-md border border-white/10 bg-white/5 px-2.5 py-1 text-xs text-slate-200 hover:bg-white/10 disabled:opacity-60"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>
      </div>

      {isLoading && (
        <div className="flex items-center gap-2 p-4 text-sm text-slate-500">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在读取 Worker 健康摘要…
        </div>
      )}

      {isError && !isLoading && (
        <div className="m-4 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
          Worker 健康摘要加载失败，请检查 `/admin/ops/workers/health`。
        </div>
      )}

      {summary && (
        <div className="space-y-4 p-4">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
            <div className="rounded-lg bg-slate-50 p-3">
              <div className="text-xs text-slate-500">总状态</div>
              <div className="mt-2 flex items-center justify-between">
                <span className="text-sm font-semibold text-slate-900">{HEALTH_LABELS[health]}</span>
                <HealthBadge verdict={HEALTH_VERDICT[health]} />
              </div>
            </div>
            <div className="rounded-lg bg-slate-50 p-3">
              <div className="text-xs text-slate-500">Worker 数</div>
              <div className="mt-2 text-2xl font-semibold tabular-nums text-slate-900">{summary.worker_count}</div>
            </div>
            <div className="rounded-lg bg-slate-50 p-3">
              <div className="text-xs text-slate-500">Function 覆盖</div>
              <div className="mt-2 text-sm font-semibold text-slate-900">
                {summary.covered_functions.length}/{summary.expected_functions.length}
              </div>
              {missingCount > 0 && <div className="mt-1 text-xs text-rose-600">缺失 {missingCount} 项</div>}
            </div>
            <div className="rounded-lg bg-slate-50 p-3">
              <div className="text-xs text-slate-500">动作项</div>
              <div className="mt-2 text-2xl font-semibold tabular-nums text-slate-900">{actionItems.length}</div>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1.2fr_1fr]">
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-sm font-medium text-slate-700">
                <Gauge className="h-4 w-4 text-slate-400" />
                资源许可
              </div>
              <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
                {summary.resources.map((resource) => {
                  const saturated = resource.limit > 0 && resource.inflight >= resource.limit
                  return (
                    <div
                      key={resource.resource_type}
                      className={`rounded-lg border p-2 ${saturated ? 'border-amber-200 bg-amber-50' : 'border-slate-100 bg-white'}`}
                    >
                      <div className="text-[11px] text-slate-500">{formatResourceName(resource.resource_type)}</div>
                      <div className="mt-1 text-sm font-semibold tabular-nums text-slate-900">
                        {resource.inflight}/{resource.limit}
                      </div>
                    </div>
                  )
                })}
              </div>

              <div className="flex items-center gap-2 text-sm font-medium text-slate-700">
                <Cpu className="h-4 w-4 text-slate-400" />
                Worker 明细
              </div>
              <div className="space-y-2">
                {summary.workers.map((worker) => (
                  <div key={worker.worker_id} className="rounded-lg border border-slate-100 bg-slate-50 p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="font-mono text-xs text-slate-700">{worker.worker_id}</div>
                      <span className="rounded-full bg-white px-2 py-0.5 text-[11px] text-slate-600">{worker.state}</span>
                    </div>
                    <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-slate-500 md:grid-cols-4">
                      <span>函数 {worker.functions.length}</span>
                      <span>GPU 空闲 {worker.gpu_free_mb ?? '—'} MB</span>
                      <span>GPU inflight {worker.gpu_inflight ?? '—'}</span>
                      <span>lag {worker.event_loop_lag_ms ?? '—'} ms</span>
                    </div>
                  </div>
                ))}
                {summary.workers.length === 0 && (
                  <div className="rounded-lg border border-dashed border-slate-200 p-4 text-sm text-slate-500">
                    当前新鲜窗口内没有 Worker 心跳。按右侧动作项先拉起 Worker，再复查 Redis 心跳。
                  </div>
                )}
              </div>
            </div>

            <div className="space-y-3">
              <div className="text-sm font-medium text-slate-700">处置动作</div>
              <div className="space-y-2">
                {actionItems.map((action) => (
                  <ActionCard key={action.code} action={action} />
                ))}
              </div>
              {(summary.reasons.unhealthy.length > 0 || summary.reasons.degraded.length > 0) && (
                <div className="rounded-lg border border-slate-100 bg-slate-50 p-3">
                  <div className="text-xs font-medium text-slate-600">触发原因</div>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {[...summary.reasons.unhealthy, ...summary.reasons.degraded].map((reason) => (
                      <span key={reason} className="rounded-full bg-white px-2 py-0.5 text-[11px] text-slate-600">
                        {formatReason(reason)}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

export default WorkerHealthPanel
