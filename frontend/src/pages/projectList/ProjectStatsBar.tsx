import React from 'react'
import { SkeletonStatCards } from '@/components'
import { FolderOpen, Loader, CheckCircle, AlertCircle, FileText, ShieldAlert, PenTool, CalendarPlus } from 'lucide-react'
import type { StatusFilter } from './types'
import type { ProjectListStats } from './projectListDerivations'

interface ProjectStatsBarProps {
  stats: ProjectListStats
  loading: boolean
  onStatClick: (filter: StatusFilter) => void
}

interface StatCardProps {
  icon: React.ReactNode
  label: string
  value: number
  color: string
  onClick?: () => void
}

const StatCard: React.FC<StatCardProps> = ({ icon, label, value, color, onClick }) => (
  <button
    onClick={onClick}
    className={`flex items-center gap-3 px-4 py-3 rounded-lg transition-colors hover:bg-gray-50 ${onClick ? 'cursor-pointer' : 'cursor-default'}`}
  >
    <div className={`w-9 h-9 rounded-lg flex items-center justify-center ${color}`}>
      {icon}
    </div>
    <div className="text-left">
      <p className="text-xl font-semibold text-gray-900">{value}</p>
      <p className="text-xs text-gray-500">{label}</p>
    </div>
  </button>
)

const ProjectStatsBar: React.FC<ProjectStatsBarProps> = ({ stats, loading, onStatClick }) => {
  if (loading) {
    return <SkeletonStatCards count={4} />
  }

  return (
    <div className="bg-white rounded-lg shadow-sm px-2 py-1">
      <div className="flex items-center justify-between">
        <div className="flex items-center">
          <StatCard
            icon={<FolderOpen className="w-4.5 h-4.5 text-blue-600" />}
            label={stats.showTypeDistribution ? '全部项目' : '当前项目'}
            value={stats.total}
            color="bg-blue-100"
            onClick={() => onStatClick('all')}
          />
          <StatCard
            icon={<Loader className="w-4.5 h-4.5 text-orange-600" />}
            label="处理中"
            value={stats.processing}
            color="bg-orange-100"
            onClick={() => onStatClick('processing')}
          />
          <StatCard
            icon={<CheckCircle className="w-4.5 h-4.5 text-green-600" />}
            label="已完成"
            value={stats.completed}
            color="bg-green-100"
            onClick={() => onStatClick('completed')}
          />
          <StatCard
            icon={<AlertCircle className="w-4.5 h-4.5 text-red-600" />}
            label="失败"
            value={stats.failed}
            color="bg-red-100"
            onClick={() => onStatClick('failed')}
          />
        </div>

        <div className="flex items-center gap-4 text-sm text-gray-500 pr-3">
          {stats.showTypeDistribution && (
            <>
              <span className="flex items-center gap-1">
                <PenTool className="w-3.5 h-3.5 text-teal-500" />
                标书编写 {stats.bidWriting}
              </span>
              <span className="flex items-center gap-1">
                <FileText className="w-3.5 h-3.5 text-blue-500" />
                投标审查 {stats.bidReview}
              </span>
              <span className="flex items-center gap-1">
                <ShieldAlert className="w-3.5 h-3.5 text-[#9056D8]" />
                串标审查 {stats.collusionCheck}
              </span>
            </>
          )}
          <span className="flex items-center gap-1">
            <CalendarPlus className="w-3.5 h-3.5 text-amber-500" />
            本月新增 {stats.thisMonth}
          </span>
        </div>
      </div>
    </div>
  )
}

export default ProjectStatsBar
