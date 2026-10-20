import React, { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, Loader2, RefreshCw } from 'lucide-react'
import { opsService, type OpsOverviewResponse } from '@/services/opsApi'
import DutyPane from './DutyPane'
import LabPane from './LabPane'
import {
  type OpsOverviewPane,
  readOverviewPane,
  writeOverviewPane,
} from './dutyLogic'

const WINDOWS: Array<{ key: string; label: string }> = [
  { key: '1h', label: '近1小时' },
  { key: '24h', label: '近24小时' },
  { key: '7d', label: '近7天' },
  { key: '30d', label: '近30天' },
]

const Overview: React.FC = () => {
  const [timeWindow, setTimeWindow] = useState('24h')
  const [pane, setPane] = useState<OpsOverviewPane>(() => readOverviewPane(globalThis.sessionStorage))

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['ops-overview', timeWindow],
    queryFn: () => opsService.getOverview(timeWindow),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })
  const overview = data as OpsOverviewResponse | undefined

  const switchPane = (next: OpsOverviewPane) => {
    setPane(next)
    writeOverviewPane(next, globalThis.sessionStorage)
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between rounded-lg border border-gray-200 bg-white px-4 py-3">
        <div className="flex items-center gap-3">
          <Activity className="h-5 w-5 text-primary" />
          <h1 className="text-xl font-semibold text-gray-900">运营监控总览</h1>
          {overview?.generated_at && (
            <span className="text-xs text-gray-400">
              更新于 {new Date(overview.generated_at).toLocaleTimeString('zh-CN')}
            </span>
          )}
          <div className="inline-flex rounded-md border border-gray-200 bg-white p-0.5 text-sm">
            {([
              { key: 'duty', label: '值班' },
              { key: 'lab', label: '指标调试' },
            ] as const).map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => switchPane(item.key)}
                className={`px-2.5 py-1 rounded ${pane === item.key ? 'bg-primary text-white' : 'text-gray-600 hover:bg-gray-50'}`}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="inline-flex rounded-md border border-gray-200 bg-white p-0.5 text-sm">
            {WINDOWS.map((w) => (
              <button
                key={w.key}
                type="button"
                onClick={() => setTimeWindow(w.key)}
                className={`px-2.5 py-1 rounded ${timeWindow === w.key ? 'bg-primary text-white' : 'text-gray-600 hover:bg-gray-50'}`}
              >
                {w.label}
              </button>
            ))}
          </div>
          <button
            type="button"
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

      {pane === 'duty' && (
        <DutyPane
          window={timeWindow}
          overview={overview}
          overviewError={isError && !isLoading}
          onSwitchPane={switchPane}
        />
      )}
      {pane === 'lab' && <LabPane window={timeWindow} overview={overview} />}
    </div>
  )
}

export default Overview
