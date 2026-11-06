import { useCallback, useEffect, useState } from 'react'
import { errorLogService } from '@/services/errorLogApi'
import { withSampledStartDate } from './useErrorLogFilters'
import type { ErrorLogItem, ErrorLogGroup } from '../types'
import { GROUP_PAGE_SIZE } from '../constants'

interface UseErrorLogGroupsOptions {
  viewMode: 'grouped' | 'flat'
  activeFilters: Record<string, unknown>
  groupPage: number
  groupSortBy: string
  onTotal: (total: number) => void
  onGroupTotalPages: (groupTotalPages: number) => void
}

export interface UseErrorLogGroupsResult {
  groups: ErrorLogGroup[]
  groupTotal: number
  groupLoading: boolean
  expandedFingerprint: string | null
  innerLogs: ErrorLogItem[]
  innerTotal: number
  innerPage: number
  innerTotalPages: number
  innerLoading: boolean
  reloadGroups: () => Promise<void>
  reloadInner: () => Promise<void>
  handleToggleGroup: (fingerprint: string) => void
  handleInnerPageChange: (nextPage: number) => void
  resetExpanded: () => void
}

export function useErrorLogGroups(opts: UseErrorLogGroupsOptions): UseErrorLogGroupsResult {
  const { viewMode, activeFilters, groupPage, groupSortBy, onTotal, onGroupTotalPages } = opts

  const [groups, setGroups] = useState<ErrorLogGroup[]>([])
  const [groupTotal, setGroupTotal] = useState(0)
  const [groupLoading, setGroupLoading] = useState(true)
  const [expandedFingerprint, setExpandedFingerprint] = useState<string | null>(null)
  const [innerLogs, setInnerLogs] = useState<ErrorLogItem[]>([])
  const [innerTotal, setInnerTotal] = useState(0)
  const [innerPage, setInnerPage] = useState(1)
  const [innerTotalPages, setInnerTotalPages] = useState(0)
  const [innerLoading, setInnerLoading] = useState(false)

  const [prevGroupPage, setPrevGroupPage] = useState(groupPage)
  if (groupPage !== prevGroupPage) {
    setPrevGroupPage(groupPage)
    setExpandedFingerprint(null)
    setInnerLogs([])
  }

  const [prevGroupQuery, setPrevGroupQuery] = useState({ viewMode, activeFilters, groupPage, groupSortBy })
  if (
    viewMode !== prevGroupQuery.viewMode ||
    activeFilters !== prevGroupQuery.activeFilters ||
    groupPage !== prevGroupQuery.groupPage ||
    groupSortBy !== prevGroupQuery.groupSortBy
  ) {
    setPrevGroupQuery({ viewMode, activeFilters, groupPage, groupSortBy })
    if (viewMode === 'grouped') setGroupLoading(true)
  }

  const fetchGroups = useCallback(async () => {
    let nextGroups: ErrorLogGroup[] = []
    let nextGroupTotal = 0
    let nextGroupTotalPages = 0
    let nextTotal = 0
    let failed = false
    try {
      const params = withSampledStartDate({
        page: groupPage,
        page_size: GROUP_PAGE_SIZE,
        sort_by: groupSortBy,
        ...activeFilters,
      })
      const [groupRes, countRes] = await Promise.all([
        errorLogService.getGroupedLogs(params) as Promise<{
          data?: { groups?: ErrorLogGroup[]; total_groups?: number; total_pages?: number }
        }>,
        errorLogService.getLogs(withSampledStartDate({ ...activeFilters, page: 1, page_size: 1 })) as Promise<{
          data?: { total?: number }
        }>,
      ])
      const data = groupRes?.data
      nextGroups = data?.groups || []
      nextGroupTotal = data?.total_groups || 0
      nextGroupTotalPages = data?.total_pages || 0
      nextTotal = countRes?.data?.total || 0
    } catch {
      failed = true
    } finally {
      if (failed) {
        setGroups([])
        setGroupTotal(0)
        onGroupTotalPages(0)
      } else {
        setGroups(nextGroups)
        setGroupTotal(nextGroupTotal)
        onGroupTotalPages(nextGroupTotalPages)
        onTotal(nextTotal)
      }
      setGroupLoading(false)
    }
  }, [activeFilters, groupPage, groupSortBy, onTotal, onGroupTotalPages])

  const reloadGroups = useCallback(async () => {
    setGroupLoading(true)
    await fetchGroups()
  }, [fetchGroups])

  const loadInner = useCallback(async (fingerprint: string, innerP: number = 1) => {
    setInnerLoading(true)
    try {
      const params = withSampledStartDate({
        page: innerP,
        page_size: 10,
        fingerprint,
        ...activeFilters,
      })
      const res = await errorLogService.getLogs(params) as {
        data?: { items?: ErrorLogItem[]; total?: number; total_pages?: number }
      }
      const data = res?.data
      setInnerLogs(data?.items || [])
      setInnerTotal(data?.total || 0)
      setInnerTotalPages(data?.total_pages || 0)
      setInnerPage(innerP)
    } catch {
      setInnerLogs([])
    } finally {
      setInnerLoading(false)
    }
  }, [activeFilters])

  const handleToggleGroup = useCallback((fingerprint: string) => {
    if (expandedFingerprint === fingerprint) {
      setExpandedFingerprint(null)
      setInnerLogs([])
      setInnerTotal(0)
      setInnerPage(1)
      setInnerTotalPages(0)
    } else {
      setExpandedFingerprint(fingerprint)
      loadInner(fingerprint, 1)
    }
  }, [expandedFingerprint, loadInner])

  const handleInnerPageChange = useCallback((nextPage: number) => {
    if (!expandedFingerprint) return
    loadInner(expandedFingerprint, nextPage)
  }, [expandedFingerprint, loadInner])

  const reloadInner = useCallback(async () => {
    if (!expandedFingerprint) return
    await loadInner(expandedFingerprint, innerPage)
  }, [expandedFingerprint, innerPage, loadInner])

  const resetExpanded = useCallback(() => {
    setExpandedFingerprint(null)
    setInnerLogs([])
    setInnerTotal(0)
    setInnerPage(1)
    setInnerTotalPages(0)
  }, [])

  useEffect(() => {
    if (viewMode === 'grouped') void fetchGroups()
  }, [fetchGroups, viewMode])

  return {
    groups, groupTotal, groupLoading,
    expandedFingerprint, innerLogs, innerTotal, innerPage, innerTotalPages, innerLoading,
    reloadGroups, reloadInner, handleToggleGroup, handleInnerPageChange, resetExpanded,
  }
}
