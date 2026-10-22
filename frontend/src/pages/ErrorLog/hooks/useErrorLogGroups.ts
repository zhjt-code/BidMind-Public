import { useCallback, useEffect, useState } from 'react'
import { errorLogService } from '@/services/errorLogApi'
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

  const reloadGroups = useCallback(async () => {
    setGroupLoading(true)
    try {
      const params: Record<string, unknown> = {
        page: groupPage,
        page_size: GROUP_PAGE_SIZE,
        sort_by: groupSortBy,
        ...activeFilters,
      }
      const [groupRes, countRes] = await Promise.all([
        errorLogService.getGroupedLogs(params) as Promise<{
          data?: { groups?: ErrorLogGroup[]; total_groups?: number; total_pages?: number }
        }>,
        errorLogService.getLogs({ ...activeFilters, page: 1, page_size: 1 }) as Promise<{
          data?: { total?: number }
        }>,
      ])
      const data = groupRes?.data
      setGroups(data?.groups || [])
      setGroupTotal(data?.total_groups || 0)
      onGroupTotalPages(data?.total_pages || 0)
      onTotal(countRes?.data?.total || 0)
    } catch {
      setGroups([])
      setGroupTotal(0)
      onGroupTotalPages(0)
    } finally {
      setGroupLoading(false)
    }
  }, [activeFilters, groupPage, groupSortBy, onTotal, onGroupTotalPages])

  const loadInner = useCallback(async (fingerprint: string, innerP: number = 1) => {
    setInnerLoading(true)
    try {
      const params: Record<string, unknown> = {
        page: innerP,
        page_size: 10,
        fingerprint,
        ...activeFilters,
      }
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
    if (viewMode === 'grouped') reloadGroups()
  }, [reloadGroups, viewMode])

  // groupPage 切换时折叠展开（与原 useEffect [groupPage] 行为一致）
  useEffect(() => {
    setExpandedFingerprint(null)
    setInnerLogs([])
  }, [groupPage])

  return {
    groups, groupTotal, groupLoading,
    expandedFingerprint, innerLogs, innerTotal, innerPage, innerTotalPages, innerLoading,
    reloadGroups, reloadInner, handleToggleGroup, handleInnerPageChange, resetExpanded,
  }
}
