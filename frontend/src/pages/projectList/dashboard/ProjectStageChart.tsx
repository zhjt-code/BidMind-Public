import React from 'react'
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { ProjectDashboardStageGroup } from '@/services/projectDashboardApi'
import type { DashboardFilterPatch } from './projectDashboardFilters'

interface ProjectStageChartProps {
  groups: ProjectDashboardStageGroup[]
  onSelect: (patch: DashboardFilterPatch) => void
}

const STAGE_GROUP_COLOR: Record<string, string> = {
  awaiting_material: '#94a3b8',
  document_handling: '#38bdf8',
  awaiting_confirm: '#f59e0b',
  ai_analysis: '#14b8a6',
  result_generating: '#2563eb',
}

const ProjectStageChart: React.FC<ProjectStageChartProps> = ({ groups, onSelect }) => {
  const visibleItems = groups.filter((group) => group.count > 0)
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs">
      <div className="mb-3">
        <h3 className="text-sm font-semibold text-gray-900">当前办理阶段</h3>
        <p className="text-xs text-gray-500">按业务阶段汇总，点击查看对应项目</p>
      </div>
      {visibleItems.length === 0 ? (
        <div className="flex h-56 items-center justify-center rounded-lg bg-gray-50 text-sm text-gray-400">
          当前没有正在推进的项目
        </div>
      ) : (
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={visibleItems} layout="vertical" margin={{ top: 8, right: 16, left: 12, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis type="number" allowDecimals={false} domain={[0, 'dataMax']} tick={{ fontSize: 10 }} />
              <YAxis type="category" dataKey="label" width={96} tick={{ fontSize: 11 }} />
              <Tooltip formatter={(value) => [`${value} 个`, '项目数']} />
              <Bar
                dataKey="count"
                radius={[0, 6, 6, 0]}
                isAnimationActive={false}
                onClick={(data: unknown) => {
                  const stages = (data as { memberStatuses?: unknown } | null)?.memberStatuses
                  if (Array.isArray(stages) && stages.every((item) => typeof item === 'string')) {
                    onSelect({ stages })
                  }
                }}
              >
                {visibleItems.map((item) => (
                  <Cell key={item.key} fill={STAGE_GROUP_COLOR[item.key] ?? '#64748b'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  )
}

export default ProjectStageChart
