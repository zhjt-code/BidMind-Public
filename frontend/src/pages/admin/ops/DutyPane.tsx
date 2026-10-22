import React from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Coins } from 'lucide-react'
import { COST_FLOW_ORDER, opsService, type CostFlowKey, type OpsOverviewResponse } from '@/services/opsApi'
import FlowHealthStrip from './components/FlowHealthStrip'
import LlmReliabilityPanel from './components/LlmReliabilityPanel'
import WorkerHealthPanel from './components/WorkerHealthPanel'
import type { VerdictType } from './components/HealthBadge'
import {
  failedTaskKpi,
  findLayerMetric,
  llmFinalValidKpi,
  type DutyKpiStatus,
  type OpsOverviewPane,
  unresolvedErrorKpi,
  workerKpi,
} from './dutyLogic'
import { KPI_FRAME, verdictBarClass, verdictLabelClass, verdictValueClass } from './dutyVisual'

export interface DutyPaneProps {
  window: string
  overview: OpsOverviewResponse | undefined
  overviewError: boolean
  onSwitchPane: (pane: OpsOverviewPane) => void
}

const COST_FLOW_LABELS: Record<CostFlowKey, string> = {
  bid_writing: '标书编写',
  bid_review: '投标审查',
  collusion_check: '串标审查',
}

const EMPTY_COST_FLOW_ROWS = COST_FLOW_ORDER.map((flow) => ({
  flow,
  project_count: 0,
  input_tokens: 0,
  output_tokens: 0,
  total_credits: 0,
  project_avg_credits: 0,
}))

function formatDecimal(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

function formatYuan(value: number): string {
  return value.toFixed(2)
}

function formatYuanRate(rate: number): string {
  return rate.toFixed(4).replace(/\.?0+$/, '')
}

function kpiClass(verdict: VerdictType, extra = ''): string {
  return `${KPI_FRAME} ${verdictBarClass(verdict)} ${extra}`.trim()
}

function KpiBody({ title, kpi }: { title: string; kpi: DutyKpiStatus }) {
  return (
    <>
      <div className="text-xs text-gray-500">{title}</div>
      <div className={`mt-2 text-2xl font-semibold tabular-nums ${verdictValueClass(kpi.verdict)}`}>
        {kpi.valueText}
      </div>
      <div className={`mt-1 text-xs ${verdictLabelClass(kpi.verdict)}`}>{kpi.label}</div>
    </>
  )
}

const DutyPane: React.FC<DutyPaneProps> = ({ window, overview, overviewError, onSwitchPane }) => {
  const workerQuery = useQuery({
    queryKey: ['ops-worker-health'],
    queryFn: () => opsService.getWorkerHealth(),
    staleTime: 15_000,
    refetchInterval: 30_000,
  })
  const llmQuery = useQuery({
    queryKey: ['llm-reliability', window, 'model'],
    queryFn: () => opsService.getLlmReliability(window, 'model'),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })

  const errorKpi = unresolvedErrorKpi(findLayerMetric(overview?.layers ?? {}, 'platform', 'unresolved_error_total'))
  const failKpi = failedTaskKpi(findLayerMetric(overview?.layers ?? {}, 'throughput', 'task_failed_total'))
  const wKpi = workerKpi(workerQuery.data?.health, workerQuery.isError)
  const llmKpi = llmFinalValidKpi(
    llmQuery.data?.totals.logical_calls,
    llmQuery.data?.totals.final_valid_rate,
    llmQuery.isError,
  )
  const cost = overview?.cost

  return (
    <div className="space-y-6">
      {overviewError && (
        <div className="bg-rose-50 border border-rose-200 text-rose-700 p-4 rounded-md text-sm">
          指标加载失败，请稍后重试或查看错误日志。Worker、模型与链路摘要仍会单独尝试加载。
        </div>
      )}
      <div className="grid grid-cols-4 gap-4">
        {errorKpi.visible && (
          <Link to="/admin/error-logs" className={kpiClass(errorKpi.verdict)}>
            <KpiBody title="未处理错误" kpi={errorKpi} />
          </Link>
        )}
        {failKpi.visible && (
          <div className={kpiClass(failKpi.verdict)}>
            <KpiBody title="失败任务" kpi={failKpi} />
          </div>
        )}
        <button
          type="button"
          onClick={() => document.getElementById('ops-worker-health')?.scrollIntoView({ behavior: 'smooth' })}
          className={kpiClass(wKpi.verdict, 'text-left')}
        >
          <KpiBody title="Worker 总状态" kpi={wKpi} />
        </button>
        <button
          type="button"
          onClick={() => onSwitchPane('lab')}
          className={kpiClass(llmKpi.verdict, 'text-left')}
        >
          <KpiBody title="LLM 最终有效率" kpi={llmKpi} />
        </button>
      </div>

      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-amber-100/80 bg-amber-50">
              <Coins className="h-[18px] w-[18px] text-amber-600" />
            </div>
            <div className="text-sm font-medium text-gray-700">成本快照</div>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-gray-500">
                <th className="pb-2 pr-3 text-left font-medium">流程</th>
                <th className="pb-2 px-3 text-right font-medium">项目数</th>
                <th className="pb-2 px-3 text-right font-medium">Token 入</th>
                <th className="pb-2 px-3 text-right font-medium">Token 出</th>
                <th className="pb-2 px-3 text-right font-medium">合计点数</th>
                <th className="pb-2 pl-3 text-right font-medium">项目均点</th>
              </tr>
            </thead>
            <tbody>
              {(cost?.by_flow?.length ? cost.by_flow : EMPTY_COST_FLOW_ROWS).map((row) => (
                <tr key={row.flow} className="border-t border-gray-100">
                  <td className="py-2 pr-3 text-gray-800">{COST_FLOW_LABELS[row.flow]}</td>
                  <td className="py-2 px-3 text-right tabular-nums">{row.project_count}</td>
                  <td className="py-2 px-3 text-right tabular-nums">{row.input_tokens}</td>
                  <td className="py-2 px-3 text-right tabular-nums">{row.output_tokens}</td>
                  <td className="py-2 px-3 text-right tabular-nums">{formatDecimal(row.total_credits)}</td>
                  <td className="py-2 pl-3 text-right tabular-nums">{formatDecimal(row.project_avg_credits)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-gray-200 font-medium">
                <td className="pt-2 pr-3 text-gray-800">合计</td>
                <td className="pt-2 px-3 text-right tabular-nums">{cost?.project_count ?? 0}</td>
                <td className="pt-2 px-3 text-right tabular-nums">{cost?.input_tokens ?? 0}</td>
                <td className="pt-2 px-3 text-right tabular-nums">{cost?.output_tokens ?? 0}</td>
                <td className="pt-2 px-3 text-right tabular-nums">{formatDecimal(cost?.total_credits ?? 0)}</td>
                <td className="pt-2 pl-3 text-right tabular-nums">{formatDecimal(cost?.project_avg_credits ?? 0)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
        <div className="mt-3 space-y-1 text-xs text-gray-500">
          <div>均点按计费规则估算，含页/OCR/保底，非 Token 均价。</div>
          <div>
            窗口约合 ¥{formatYuan(cost?.estimated_yuan ?? 0)}（1 点 ≈ ¥{formatYuanRate(cost?.credit_yuan_rate ?? 0)}，按基础套餐折算，非实收）
          </div>
          {(cost?.unknown_project_count ?? 0) > 0 && (
            <div>另有 {cost?.unknown_project_count} 个项目无法归类，已计入合计。</div>
          )}
        </div>
      </div>

      <LlmReliabilityPanel window={window} compact />
      <FlowHealthStrip window={window} />
      <WorkerHealthPanel />
    </div>
  )
}

export default DutyPane
