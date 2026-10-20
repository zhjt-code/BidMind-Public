import type { Project } from '@/stores/projectStore'
import {
  filterByStatus,
  isProcessingStatus,
  sortProjects,
  type ProjectTypeSelection,
  type SortKey,
  type StatusFilter,
  type TabKey,
  type UpdatedWithin,
} from './types.ts'
import type { ProjectDashboardProjectFilter } from './dashboard/projectDashboardFilters.ts'

export interface ProjectListContext {
  title: string
  description: string
  total: number
  iconName: 'House' | 'PenTool' | 'ClipboardCheck' | 'ShieldAlert'
}

export interface ProjectListStats {
  total: number
  pending: number
  processing: number
  completed: number
  failed: number
  bidWriting: number
  bidReview: number
  collusionCheck: number
  thisMonth: number
  showTypeDistribution: boolean
}

export interface ProjectListFilterOptions {
  projects: Project[]
  activeTab: TabKey
  searchQuery: string
  usernameQuery: string
  isAdmin: boolean
  statusFilter: StatusFilter
  sortKey: SortKey
  updatedWithin?: UpdatedWithin
  now?: Date
  dashboardFilter?: ProjectDashboardProjectFilter
}

export function resolveProjectListTab(type: string | null): TabKey {
  if (type === 'bid_writing' || type === 'bid_review' || type === 'collusion_check') {
    return type
  }
  return 'all'
}

export function getScopedProjects(projects: Project[], activeTab: TabKey): Project[] {
  if (activeTab === 'all') return projects
  return projects.filter((project) => project.projectType === activeTab)
}

export function getCreateProjectPreset(activeTab: TabKey): ProjectTypeSelection | undefined {
  return activeTab === 'all' ? undefined : activeTab
}

export function getProjectListContext(projects: Project[], activeTab: TabKey): ProjectListContext {
  const total = getScopedProjects(projects, activeTab).length
  if (activeTab === 'bid_writing') {
    return { title: '标书编写项目', description: '查看和管理标书编写项目', total, iconName: 'PenTool' }
  }
  if (activeTab === 'bid_review') {
    return { title: '投标审查项目', description: '查看和管理投标审查项目', total, iconName: 'ClipboardCheck' }
  }
  if (activeTab === 'collusion_check') {
    return { title: '串标审查项目', description: '查看和管理串标审查项目', total, iconName: 'ShieldAlert' }
  }
  return { title: '项目控制台', description: '查看项目运行态势，按业务和状态快速定位项目', total, iconName: 'House' }
}

export function buildProjectListStats(projects: Project[], activeTab: TabKey, now = new Date()): ProjectListStats {
  const scopedProjects = getScopedProjects(projects, activeTab)
  const sameMonth = (dateString: string): boolean => {
    const date = new Date(dateString)
    return date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth()
  }

  return {
    total: scopedProjects.length,
    pending: filterByStatus(scopedProjects, 'pending').length,
    processing: filterByStatus(scopedProjects, 'processing').length,
    completed: filterByStatus(scopedProjects, 'completed').length,
    failed: filterByStatus(scopedProjects, 'failed').length,
    bidWriting: scopedProjects.filter((project) => project.projectType === 'bid_writing').length,
    bidReview: scopedProjects.filter((project) => project.projectType === 'bid_review').length,
    collusionCheck: scopedProjects.filter((project) => project.projectType === 'collusion_check').length,
    thisMonth: scopedProjects.filter((project) => sameMonth(project.createdAt)).length,
    showTypeDistribution: activeTab === 'all',
  }
}

export function filterProjectList({
  projects,
  activeTab,
  searchQuery,
  usernameQuery,
  isAdmin,
  statusFilter,
  sortKey,
  updatedWithin = 'all',
  now = new Date(),
  dashboardFilter,
}: ProjectListFilterOptions): Project[] {
  let result = getScopedProjects(projects, activeTab)

  if (searchQuery) {
    const query = searchQuery.toLowerCase()
    result = result.filter((project) => project.name.toLowerCase().includes(query))
  }

  if (isAdmin && usernameQuery) {
    const query = usernameQuery.toLowerCase()
    result = result.filter((project) => project.owner?.username?.toLowerCase().includes(query))
  }

  result = filterByStatus(result, statusFilter)
  result = filterByUpdatedWithin(result, updatedWithin, now)
  if (dashboardFilter?.exactStatuses.length) {
    const allowed = new Set(dashboardFilter.exactStatuses)
    result = result.filter((project) => allowed.has(project.status))
  }
  if (dashboardFilter?.staleHours) {
    const cutoff = now.getTime() - dashboardFilter.staleHours * 60 * 60 * 1000
    result = result.filter((project) => {
      if (!isProcessingStatus(project.status)) return false
      const updatedMs = new Date(project.updatedAt).getTime()
      return !Number.isNaN(updatedMs) && updatedMs < cutoff
    })
  }
  if (dashboardFilter?.createdDate) {
    result = result.filter((project) => getUtcDateKey(project.createdAt) === dashboardFilter.createdDate)
  }
  return sortProjects(result, sortKey)
}

export function filterByUpdatedWithin(
  projects: Project[],
  updatedWithin: UpdatedWithin,
  now = new Date(),
): Project[] {
  if (updatedWithin === 'all') return projects
  const cutoffMs = getUpdatedWithinCutoffMs(updatedWithin, now)
  if (cutoffMs === null) return projects
  return projects.filter((project) => {
    const updatedMs = new Date(project.updatedAt).getTime()
    return !Number.isNaN(updatedMs) && updatedMs >= cutoffMs
  })
}

export function getUpdatedWithinCutoffMs(updatedWithin: UpdatedWithin, now = new Date()): number | null {
  if (updatedWithin === 'all') return null
  if (updatedWithin === 'today') {
    return new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  }
  const days = updatedWithin === '7d' ? 7 : 30
  return now.getTime() - days * 24 * 60 * 60 * 1000
}

function getUtcDateKey(dateString: string): string {
  const date = new Date(dateString)
  if (Number.isNaN(date.getTime())) return ''
  return date.toISOString().slice(0, 10)
}
