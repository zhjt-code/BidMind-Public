import React from 'react'
import type { ProjectDashboardData } from '@/services/projectDashboardApi'
import ProjectDashboardHero from './ProjectDashboardHero'
import ProjectHealthPanel from './ProjectHealthPanel'
import ProjectCreationTrendChart from './ProjectCreationTrendChart'
import ProjectTypeStatusChart from './ProjectTypeStatusChart'
import ProjectStageChart from './ProjectStageChart'
import RecentProjectsPanel from './RecentProjectsPanel'
import type { DashboardFilterPatch } from './projectDashboardFilters'

interface ProjectDashboardProps {
  data: ProjectDashboardData
  isFetching: boolean
  trendWindowDays: number
  onTrendWindowDaysChange: (days: number) => void
  healthWindowDays: number
  onHealthWindowDaysChange: (days: number) => void
  onRefresh: () => void
  onApplyFilter: (patch: DashboardFilterPatch) => void
}

const ProjectDashboard: React.FC<ProjectDashboardProps> = ({
  data,
  isFetching,
  trendWindowDays,
  onTrendWindowDaysChange,
  healthWindowDays,
  onHealthWindowDaysChange,
  onRefresh,
  onApplyFilter,
}) => (
  <div className="space-y-4">
    <div className="grid gap-4 md:grid-cols-[minmax(0,1.25fr)_340px]">
      <ProjectDashboardHero data={data} isFetching={isFetching} onRefresh={onRefresh} onSelect={onApplyFilter} />
      <ProjectHealthPanel
        health={data.health}
        windowDays={healthWindowDays}
        onWindowDaysChange={onHealthWindowDaysChange}
      />
    </div>
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(360px,0.85fr)]">
      <ProjectCreationTrendChart
        created={data.dailyCreated}
        completed={data.dailyCompleted}
        windowDays={trendWindowDays}
        onWindowDaysChange={onTrendWindowDaysChange}
      />
      <ProjectStageChart groups={data.stageGroups} onSelect={onApplyFilter} />
    </div>
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(340px,0.85fr)]">
      <ProjectTypeStatusChart rows={data.typeStatusMatrix} onSelect={onApplyFilter} />
      <RecentProjectsPanel projects={data.recentProjects} />
    </div>
  </div>
)

export default ProjectDashboard
