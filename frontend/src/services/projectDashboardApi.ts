export const PROJECT_DASHBOARD_QUERY_KEY = 'project-dashboard'

export interface ProjectDashboardSummary {
  total: number
  pending: number
  processing: number
  completed: number
  failed: number
  thisMonth: number
}

export interface ProjectDashboardBucket {
  key: string
  label: string
  count: number
}

export interface ProjectTypeStatusRow {
  projectType: string
  label: string
  pending: number
  processing: number
  completed: number
  failed: number
}

export interface ProjectDailyCount {
  date: string
  count: number
}

export interface ProjectTypeCompletionDuration {
  projectType: string
  label: string
  avgDurationMs: number | null
  sampleCount: number
}

export interface ProjectDashboardHealth {
  successRate: number | null
  completionByType: ProjectTypeCompletionDuration[]
  backlog: number
  netChange: number
}

export interface ProjectDashboardActionFilter {
  status: string | null
  stages: string[]
  staleHours: number | null
}

export interface ProjectDashboardActionItem {
  id: string
  label: string
  count: number
  tone: 'red' | 'orange' | 'amber' | 'cyan'
  actionLabel: string
  filter: ProjectDashboardActionFilter
}

export interface ProjectDashboardStageGroup {
  key: string
  label: string
  count: number
  memberStatuses: string[]
}

export interface ProjectDashboardRecentProject {
  id: number
  name: string
  projectType: string
  status: string
  createdAt: string
  updatedAt: string
}

export interface ProjectDashboardData {
  scope: 'mine' | 'all'
  generatedAt: string
  windowDays: number
  healthWindowDays: number
  summary: ProjectDashboardSummary
  typeDistribution: ProjectDashboardBucket[]
  statusDistribution: ProjectDashboardBucket[]
  stageDistribution: ProjectDashboardBucket[]
  typeStatusMatrix: ProjectTypeStatusRow[]
  dailyCreated: ProjectDailyCount[]
  dailyCompleted: ProjectDailyCount[]
  recentProjects: ProjectDashboardRecentProject[]
  health: ProjectDashboardHealth
  actionQueue: ProjectDashboardActionItem[]
  stageGroups: ProjectDashboardStageGroup[]
}

interface ProjectDashboardSummaryResponse {
  total: number
  pending: number
  processing: number
  completed: number
  failed: number
  this_month: number
}

interface ProjectTypeStatusRowResponse {
  project_type: string
  label: string
  pending: number
  processing: number
  completed: number
  failed: number
}

interface ProjectTypeCompletionDurationResponse {
  project_type: string
  label: string
  avg_duration_ms: number | null
  sample_count: number
}

interface ProjectDashboardHealthResponse {
  success_rate: number | null
  completion_by_type: ProjectTypeCompletionDurationResponse[]
  backlog: number
  net_change: number
}

interface ProjectDashboardActionFilterResponse {
  status: string | null
  stages: string[]
  stale_hours: number | null
}

interface ProjectDashboardActionItemResponse {
  id: string
  label: string
  count: number
  tone: 'red' | 'orange' | 'amber' | 'cyan'
  action_label: string
  filter: ProjectDashboardActionFilterResponse
}

interface ProjectDashboardStageGroupResponse {
  key: string
  label: string
  count: number
  member_statuses: string[]
}

interface ProjectDashboardRecentProjectResponse {
  id: number
  name: string
  project_type: string
  status: string
  created_at: string
  updated_at: string
}

export interface ProjectDashboardResponse {
  scope: 'mine' | 'all'
  generated_at: string
  window_days: number
  health_window_days: number
  summary: ProjectDashboardSummaryResponse
  type_distribution: ProjectDashboardBucket[]
  status_distribution: ProjectDashboardBucket[]
  stage_distribution: ProjectDashboardBucket[]
  type_status_matrix: ProjectTypeStatusRowResponse[]
  daily_created: ProjectDailyCount[]
  daily_completed: ProjectDailyCount[]
  recent_projects: ProjectDashboardRecentProjectResponse[]
  health: ProjectDashboardHealthResponse
  action_queue: ProjectDashboardActionItemResponse[]
  stage_groups: ProjectDashboardStageGroupResponse[]
}

export function projectDashboardQueryKey(
  userId: number | undefined,
  role: string | undefined,
  trendWindowDays: number,
  healthWindowDays: number,
) {
  return [
    PROJECT_DASHBOARD_QUERY_KEY,
    userId ?? null,
    role ?? 'anonymous',
    trendWindowDays,
    healthWindowDays,
  ] as const
}

export function normalizeProjectDashboardResponse(
  response: ProjectDashboardResponse,
): ProjectDashboardData {
  return {
    scope: response.scope,
    generatedAt: response.generated_at,
    windowDays: response.window_days,
    healthWindowDays: response.health_window_days,
    summary: {
      total: response.summary.total,
      pending: response.summary.pending,
      processing: response.summary.processing,
      completed: response.summary.completed,
      failed: response.summary.failed,
      thisMonth: response.summary.this_month,
    },
    typeDistribution: response.type_distribution,
    statusDistribution: response.status_distribution,
    stageDistribution: response.stage_distribution,
    typeStatusMatrix: response.type_status_matrix.map((row) => ({
      projectType: row.project_type,
      label: row.label,
      pending: row.pending,
      processing: row.processing,
      completed: row.completed,
      failed: row.failed,
    })),
    dailyCreated: response.daily_created,
    dailyCompleted: response.daily_completed,
    recentProjects: response.recent_projects.map((project) => ({
      id: project.id,
      name: project.name,
      projectType: project.project_type,
      status: project.status,
      createdAt: project.created_at,
      updatedAt: project.updated_at,
    })),
    health: {
      successRate: response.health.success_rate,
      completionByType: response.health.completion_by_type.map((item) => ({
        projectType: item.project_type,
        label: item.label,
        avgDurationMs: item.avg_duration_ms,
        sampleCount: item.sample_count,
      })),
      backlog: response.health.backlog,
      netChange: response.health.net_change,
    },
    actionQueue: response.action_queue.map((item) => ({
      id: item.id,
      label: item.label,
      count: item.count,
      tone: item.tone,
      actionLabel: item.action_label,
      filter: {
        status: item.filter.status,
        stages: item.filter.stages,
        staleHours: item.filter.stale_hours,
      },
    })),
    stageGroups: response.stage_groups.map((group) => ({
      key: group.key,
      label: group.label,
      count: group.count,
      memberStatuses: group.member_statuses,
    })),
  }
}
