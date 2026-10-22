import React from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { ProjectDailyCount } from '@/services/projectDashboardApi'
import WindowDaysToggle from './WindowDaysToggle'

interface ProjectCreationTrendChartProps {
  created: ProjectDailyCount[]
  completed: ProjectDailyCount[]
  windowDays: number
  onWindowDaysChange: (days: number) => void
}

const ProjectCreationTrendChart: React.FC<ProjectCreationTrendChartProps> = ({
  created,
  completed,
  windowDays,
  onWindowDaysChange,
}) => {
  const completedByDate = new Map(completed.map((item) => [item.date, item.count]))
  const points = created.map((item) => ({
    date: item.date,
    created: item.count,
    completed: completedByDate.get(item.date) ?? 0,
  }))

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">新建 vs 完成</h3>
          <p className="text-xs text-gray-500">完成按办结日近似，用来看新增是否快过消化</p>
        </div>
        <WindowDaysToggle value={windowDays} onChange={onWindowDaysChange} />
      </div>
      <div className="relative h-80">
        <span className="absolute left-2 top-1/2 z-10 -translate-y-1/2 -rotate-90 text-[11px] text-slate-500">
          项目数
        </span>
        <span className="absolute bottom-3 left-1/2 z-10 -translate-x-1/2 text-[11px] text-slate-500">
          日期
        </span>
        <div className="absolute inset-0 pb-7 pl-8">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={points} margin={{ top: 16, right: 18, left: 0, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={(value) => String(value).slice(5)} />
              <YAxis allowDecimals={false} domain={[0, 'dataMax']} tick={{ fontSize: 11 }} />
              <Tooltip
                formatter={(value, name) => [`${value} 个`, name === 'created' ? '新建' : '完成']}
                labelFormatter={(label) => `${label}`}
              />
              <Legend formatter={(value) => (value === 'created' ? '新建' : '完成')} />
              <Line type="monotone" dataKey="created" stroke="#06b6d4" strokeWidth={2} dot={false} isAnimationActive={false} />
              <Line type="monotone" dataKey="completed" stroke="#22c55e" strokeWidth={2} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </section>
  )
}

export default ProjectCreationTrendChart
