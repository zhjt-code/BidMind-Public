import { STATUS_CONFIG, type StatusFilter, type TabKey } from '../types.ts'
import { resolveProjectListTab } from '../projectListDerivations.ts'

const STATUS_FILTERS = new Set<StatusFilter>(['all', 'pending', 'processing', 'completed', 'failed'])
const STATUS_VALUES = new Set(Object.keys(STATUS_CONFIG))

export interface ProjectDashboardProjectFilter {
  activeTab: TabKey
  statusFilter: StatusFilter
  exactStatuses: string[]
  staleHours?: number
  createdDate?: string
  hasDashboardFilter: boolean
}

export interface DashboardFilterPatch {
  type?: TabKey
  status?: StatusFilter
  stage?: string
  stages?: string[]
  staleHours?: number
  created?: string
  clear?: boolean
}

export function parseProjectDashboardFilters(params: URLSearchParams): ProjectDashboardProjectFilter {
  const activeTab = resolveProjectListTab(params.get('type'))
  const status = params.get('status')
  const exactStatuses = parseExactStatuses(params.get('stage'))
  const staleHours = parseStaleHours(params.get('stale_hours'))
  const created = params.get('created')
  const statusFilter: StatusFilter = status && STATUS_FILTERS.has(status as StatusFilter)
    ? status as StatusFilter
    : 'all'
  const createdDate = created && isValidDateKey(created) ? created : undefined

  return {
    activeTab,
    statusFilter,
    exactStatuses,
    staleHours,
    createdDate,
    hasDashboardFilter: activeTab !== 'all'
      || statusFilter !== 'all'
      || exactStatuses.length > 0
      || staleHours !== undefined
      || !!createdDate,
  }
}

export function buildDashboardProjectFilter(params: URLSearchParams): ProjectDashboardProjectFilter {
  return parseProjectDashboardFilters(params)
}

export function applyProjectDashboardFilter(
  currentParams: URLSearchParams,
  patch: DashboardFilterPatch,
): URLSearchParams {
  if (patch.clear) return new URLSearchParams()

  const next = new URLSearchParams(currentParams)

  if (patch.type !== undefined) {
    toggleParam(next, 'type', patch.type === 'all' ? null : patch.type)
  }
  if (patch.status !== undefined) {
    toggleParam(next, 'status', patch.status === 'all' ? null : patch.status)
    if (patch.status !== 'all') next.delete('stage')
    if (patch.status !== 'all' && patch.staleHours === undefined) next.delete('stale_hours')
  }
  if (patch.stages !== undefined) {
    const value = joinValidStages(patch.stages)
    toggleParam(next, 'stage', value)
    if (value) {
      next.delete('status')
      next.delete('stale_hours')
    }
  } else if (patch.stage !== undefined) {
    toggleParam(next, 'stage', STATUS_VALUES.has(patch.stage) ? patch.stage : null)
    if (patch.stage) next.delete('status')
  }
  if (patch.staleHours !== undefined) {
    toggleParam(next, 'stale_hours', patch.staleHours > 0 ? String(patch.staleHours) : null)
  }
  if (patch.created !== undefined) {
    toggleParam(next, 'created', isValidDateKey(patch.created) ? patch.created : null)
  }

  return next
}

function parseExactStatuses(stage: string | null): string[] {
  if (!stage) return []
  return stage.split(',').map((item) => item.trim()).filter((item) => STATUS_VALUES.has(item))
}

function parseStaleHours(value: string | null): number | undefined {
  if (!value || !/^\d+$/.test(value)) return undefined
  const hours = Number(value)
  return hours > 0 ? hours : undefined
}

function joinValidStages(stages: string[]): string | null {
  const valid = stages.filter((stage) => STATUS_VALUES.has(stage))
  return valid.length > 0 ? valid.join(',') : null
}

function toggleParam(params: URLSearchParams, key: string, value: string | null): void {
  if (!value || params.get(key) === value) {
    params.delete(key)
    return
  }
  params.set(key, value)
}

function isValidDateKey(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const date = new Date(`${value}T00:00:00.000Z`)
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value
}
