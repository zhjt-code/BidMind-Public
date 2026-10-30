import React, { useState } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { ProjectTypeStatusRow } from '@/services/projectDashboardApi'
import { STATUS_GROUP_STYLE, getStatusGroupColor } from './projectDashboardConfig'
import { countAxisTicks, countAxisWidth } from './countAxisTicks'
import { PERCENT_AXIS_TICKS, toPercentShares } from './percentShares'
import type { DashboardFilterPatch } from './projectDashboardFilters'
import type { TabKey } from '../types'

interface ProjectTypeStatusChartProps {
  rows: ProjectTypeStatusRow[]
  onSelect: (patch: DashboardFilterPatch) => void
}

const STATUS_KEYS = ['pending', 'processing', 'completed', 'failed'] as const
type ChartMode = 'count' | 'share'

const ProjectTypeStatusChart: React.FC<ProjectTypeStatusChartProps> = ({ rows, onSelect }) => {
  const [mode, setMode] = useState<ChartMode>('count')
  const countMax = rows.reduce((max, row) => {
    const total = row.pending + row.processing + row.completed + row.failed
    return Math.max(max, total)
  }, 0)
  const countTicks = countAxisTicks(countMax)
  const yAxis = mode === 'share'
    ? {
        domain: [0, 100] as [number, number],
        ticks: [...PERCENT_AXIS_TICKS],
        width: 48,
        format: (value: number) => `${Math.round(value)}%`,
      }
    : {
        domain: [0, countTicks[countTicks.length - 1] ?? 1] as [number, number],
        ticks: countTicks,
        width: countAxisWidth(countTicks),
        format: (value: number) => String(value),
      }
  const chartRows = rows.map((row) => {
    if (mode === 'count') return row
    const shares = toPercentShares([
      row.pending,
      row.processing,
      row.completed,
      row.failed,
    ])
    return {
      ...row,
      pending: shares[0] ?? 0,
      processing: shares[1] ?? 0,
      completed: shares[2] ?? 0,
      failed: shares[3] ?? 0,
    }
  })

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">业务类型与状态</h3>
          <p className="text-xs text-gray-500">数量看规模，占比看各业务健康</p>
        </div>
        <div className="flex shrink-0 rounded-lg border border-slate-200 p-0.5">
          {(['count', 'share'] as const).map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => setMode(item)}
              className={`rounded-md px-2 py-1 text-[11px] ${
                mode === item ? 'bg-cyan-50 text-cyan-800' : 'text-slate-500 hover:text-slate-800'
              }`}
            >
              {item === 'count' ? '数量' : '占比'}
            </button>
          ))}
        </div>
      </div>
      <div className="h-80">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartRows} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
            <XAxis dataKey="label" tick={{ fontSize: 11 }} />
            <YAxis
              allowDecimals={false}
              allowDataOverflow
              domain={yAxis.domain}
              ticks={yAxis.ticks}
              interval={0}
              width={yAxis.width}
              tick={{ fontSize: 11 }}
              tickFormatter={yAxis.format}
            />
            <Tooltip
              formatter={(value, name) => [
                mode === 'share' ? `${Number(value).toFixed(1)}%` : `${value} 个`,
                name,
              ]}
            />
            <Legend verticalAlign="top" height={28} />
            {STATUS_KEYS.map((key) => (
              <Bar
                key={key}
                name={STATUS_GROUP_STYLE[key].label}
                dataKey={key}
                stackId="status"
                fill={getStatusGroupColor(key)}
                isAnimationActive={false}
                onClick={(data: unknown) => {
                  const projectType = (data as { projectType?: unknown } | null)?.projectType
                  if (typeof projectType === 'string') {
                    onSelect({ type: projectType as TabKey, status: key })
                  }
                }}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}

export default ProjectTypeStatusChart
