import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { Project } from '@/stores/projectStore'
import {
  getRememberedProjectListPage,
  resetProjectListPage,
  setRememberedProjectListPage,
} from '@/stores/projectListPageMemory'
import { PAGE_SIZE, type SortKey, type StatusFilter, type UpdatedWithin } from '../types'
import {
  buildProjectListStats,
  filterProjectList,
  getProjectListContext,
  resolveProjectListTab,
} from '../projectListDerivations'
import {
  applyProjectDashboardFilter,
  buildDashboardProjectFilter,
  type DashboardFilterPatch,
} from '../dashboard/projectDashboardFilters'

interface UseProjectListViewArgs {
  projects: Project[]
  isAdmin: boolean
}

export function useProjectListView({ projects, isAdmin }: UseProjectListViewArgs) {
  const [searchParams, setSearchParams] = useSearchParams()
  const activeTab = resolveProjectListTab(searchParams.get('type'))
  const dashboardFilter = useMemo(
    () => buildDashboardProjectFilter(searchParams),
    [searchParams],
  )
  const [searchInput, setSearchInput] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [usernameSearchInput, setUsernameSearchInput] = useState('')
  const [usernameQuery, setUsernameQuery] = useState('')
  const [currentPage, setCurrentPage] = useState(() => getRememberedProjectListPage(activeTab))
  const [sortKey, setSortKey] = useState<SortKey>('updatedAt')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [updatedWithin, setUpdatedWithin] = useState<UpdatedWithin>('all')
  const effectiveStatusFilter = dashboardFilter.statusFilter !== 'all'
    ? dashboardFilter.statusFilter
    : statusFilter

  useEffect(() => {
    setCurrentPage(getRememberedProjectListPage(activeTab))
    setSearchInput('')
    setSearchQuery('')
    setUsernameSearchInput('')
    setUsernameQuery('')
    setStatusFilter('all')
    setUpdatedWithin('all')
  }, [activeTab])

  useDebouncedTrimmedValue(searchInput, (nextQuery) => {
    setSearchQuery((prev) => {
      if (prev === nextQuery) return prev
      resetProjectListPage(activeTab)
      setCurrentPage(1)
      return nextQuery
    })
  }, [activeTab])

  useDebouncedTrimmedValue(usernameSearchInput, (nextQuery) => {
    setUsernameQuery((prev) => {
      if (prev === nextQuery) return prev
      resetProjectListPage(activeTab)
      setCurrentPage(1)
      return nextQuery
    })
  }, [activeTab])

  const filteredProjects = useMemo(
    () => filterProjectList({
      projects,
      activeTab,
      searchQuery,
      usernameQuery,
      isAdmin,
      statusFilter: effectiveStatusFilter,
      sortKey,
      updatedWithin,
      dashboardFilter,
    }),
    [projects, activeTab, searchQuery, usernameQuery, isAdmin, dashboardFilter, effectiveStatusFilter, sortKey, updatedWithin],
  )
  const totalPages = useMemo(
    () => Math.max(1, Math.ceil(filteredProjects.length / PAGE_SIZE)),
    [filteredProjects.length],
  )

  const handlePageChange = useCallback((page: number) => {
    const memory = setRememberedProjectListPage(activeTab, page)
    setCurrentPage(memory[activeTab])
  }, [activeTab])

  useEffect(() => {
    const clampedPage = Math.min(Math.max(1, currentPage), totalPages)
    if (clampedPage !== currentPage) {
      setRememberedProjectListPage(activeTab, clampedPage)
      setCurrentPage(clampedPage)
    }
  }, [activeTab, currentPage, totalPages])

  const pagedProjects = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE
    return filteredProjects.slice(start, start + PAGE_SIZE)
  }, [filteredProjects, currentPage])

  const resetCurrentPage = useCallback(() => {
    resetProjectListPage(activeTab)
    setCurrentPage(1)
  }, [activeTab])

  const clearDashboardFilterParams = useCallback(() => {
    const next = new URLSearchParams(searchParams)
    next.delete('status')
    next.delete('stage')
    next.delete('stale_hours')
    next.delete('created')
    setSearchParams(next)
  }, [searchParams, setSearchParams])

  const handleStatusFilterChange = useCallback((filter: StatusFilter) => {
    if (filter === effectiveStatusFilter) return
    setStatusFilter(filter)
    clearDashboardFilterParams()
    resetCurrentPage()
  }, [clearDashboardFilterParams, effectiveStatusFilter, resetCurrentPage])

  const handleUpdatedWithinChange = useCallback((value: UpdatedWithin) => {
    if (value === updatedWithin) return
    setUpdatedWithin(value)
    resetCurrentPage()
  }, [resetCurrentPage, updatedWithin])

  const handleSortChange = useCallback((key: SortKey) => {
    if (key === sortKey) return
    setSortKey(key)
    resetCurrentPage()
  }, [resetCurrentPage, sortKey])

  const handleStatClick = useCallback((filter: StatusFilter) => {
    setStatusFilter(filter)
    setUpdatedWithin('all')
    setSearchParams(applyProjectDashboardFilter(searchParams, { status: filter }))
    resetCurrentPage()
    setSearchInput('')
    setSearchQuery('')
    setUsernameSearchInput('')
    setUsernameQuery('')
  }, [resetCurrentPage, searchParams, setSearchParams])

  const applyDashboardFilter = useCallback((patch: DashboardFilterPatch) => {
    setSearchParams(applyProjectDashboardFilter(searchParams, patch))
    resetCurrentPage()
    setSearchInput('')
    setSearchQuery('')
    setUsernameSearchInput('')
    setUsernameQuery('')
    setUpdatedWithin('all')
  }, [resetCurrentPage, searchParams, setSearchParams])

  return {
    activeTab,
    context: getProjectListContext(projects, activeTab),
    stats: buildProjectListStats(projects, activeTab),
    dashboardFilter,
    filteredProjects,
    pagedProjects,
    searchInput,
    setSearchInput,
    usernameSearchInput,
    setUsernameSearchInput,
    sortKey,
    statusFilter: effectiveStatusFilter,
    updatedWithin,
    currentPage,
    selectionResetKey: `${activeTab}|${searchQuery}|${effectiveStatusFilter}|${updatedWithin}|${sortKey}|${currentPage}|${dashboardFilter.exactStatuses.join(',')}|${dashboardFilter.staleHours ?? ''}|${dashboardFilter.createdDate ?? ''}`,
    handlePageChange,
    handleStatusFilterChange,
    handleUpdatedWithinChange,
    handleSortChange,
    handleStatClick,
    applyDashboardFilter,
  }
}

function useDebouncedTrimmedValue(
  value: string,
  onValueChange: (value: string) => void,
  deps: unknown[],
): void {
  useEffect(() => {
    const timer = setTimeout(() => onValueChange(value.trim()), 500)
    return () => clearTimeout(timer)
  }, [value, onValueChange, ...deps]) // eslint-disable-line react-hooks/exhaustive-deps
}
