import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ErrorLogGroup, ErrorLogItem } from '../types'

interface UseErrorLogSelectionOptions {
  viewMode: 'grouped' | 'flat'
  activeFilters: Record<string, unknown>
  logs: ErrorLogItem[]
  groups: ErrorLogGroup[]
  total: number
  page: number
}

export interface BatchPayload {
  log_ids?: number[]
  select_all?: boolean
  exclude_ids?: number[]
  filters?: Record<string, unknown>
}

export interface UseErrorLogSelectionResult {
  selectionMode: boolean
  setSelectionMode: (v: boolean) => void
  selectedIds: Set<number>
  allMatchingSelected: boolean
  excludedIds: Set<number>
  selectedFingerprints: Set<string>
  isLogSelected: (id: number) => boolean
  selectedCount: number
  currentPageSelectedCount: number
  allCurrentPageSelected: boolean
  clearSelection: () => void
  exitSelectionMode: () => void
  selectAllMatchingLogs: () => void
  toggleSelect: (id: number) => void
  toggleSelectAll: () => void
  toggleGroupSelect: (fingerprint: string) => void
  getBatchPayload: () => BatchPayload
}

export function useErrorLogSelection(opts: UseErrorLogSelectionOptions): UseErrorLogSelectionResult {
  const { viewMode, activeFilters, logs, groups, total, page } = opts

  const [selectionMode, setSelectionMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [allMatchingSelected, setAllMatchingSelected] = useState(false)
  const [excludedIds, setExcludedIds] = useState<Set<number>>(new Set())
  const [selectedFingerprints, setSelectedFingerprints] = useState<Set<string>>(new Set())

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set())
    setAllMatchingSelected(false)
    setExcludedIds(new Set())
    setSelectedFingerprints(new Set())
  }, [])

  const exitSelectionMode = useCallback(() => {
    setSelectionMode(false)
    clearSelection()
  }, [clearSelection])

  const isLogSelected = useCallback((id: number) => {
    if (allMatchingSelected) return !excludedIds.has(id)
    return selectedIds.has(id)
  }, [allMatchingSelected, excludedIds, selectedIds])

  const selectedCount = useMemo(() => {
    if (viewMode === 'grouped' && selectedFingerprints.size > 0) {
      return groups
        .filter(g => selectedFingerprints.has(g.fingerprint))
        .reduce((sum, g) => sum + g.count, 0)
    }
    if (allMatchingSelected) return Math.max(total - excludedIds.size, 0)
    return selectedIds.size
  }, [allMatchingSelected, excludedIds, selectedIds, total, viewMode, selectedFingerprints, groups])

  const currentPageSelectedCount = useMemo(
    () => logs.filter(log => isLogSelected(log.id)).length,
    [logs, isLogSelected],
  )

  const allCurrentPageSelected = logs.length > 0 && currentPageSelectedCount === logs.length

  const selectAllMatchingLogs = useCallback(() => {
    setAllMatchingSelected(true)
    setSelectedIds(new Set())
    setExcludedIds(new Set())
  }, [])

  const toggleSelect = useCallback((id: number) => {
    if (allMatchingSelected) {
      setExcludedIds(prev => {
        const next = new Set(prev)
        if (next.has(id)) next.delete(id)
        else next.add(id)
        return next
      })
      return
    }
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [allMatchingSelected])

  const toggleSelectAll = useCallback(() => {
    const allIds = logs.map(l => l.id)
    if (allMatchingSelected) {
      setExcludedIds(prev => {
        const next = new Set(prev)
        if (allCurrentPageSelected) {
          allIds.forEach(id => next.add(id))
        } else {
          allIds.forEach(id => next.delete(id))
        }
        return next
      })
      return
    }
    if (allCurrentPageSelected) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(allIds))
    }
  }, [allCurrentPageSelected, allMatchingSelected, logs])

  const toggleGroupSelect = useCallback((fingerprint: string) => {
    setSelectedFingerprints(prev => {
      const next = new Set(prev)
      if (next.has(fingerprint)) next.delete(fingerprint)
      else next.add(fingerprint)
      return next
    })
  }, [])

  const getBatchPayload = useCallback((): BatchPayload => {
    if (viewMode === 'grouped' && selectedFingerprints.size > 0) {
      return {
        select_all: true,
        filters: { ...activeFilters, fingerprints: Array.from(selectedFingerprints) },
      }
    }
    if (allMatchingSelected) {
      return {
        select_all: true,
        exclude_ids: Array.from(excludedIds),
        filters: activeFilters,
      }
    }
    return { log_ids: Array.from(selectedIds) }
  }, [activeFilters, allMatchingSelected, excludedIds, selectedIds, viewMode, selectedFingerprints])

  useEffect(() => {
    if (selectionMode) clearSelection()
  }, [clearSelection, activeFilters, selectionMode])

  useEffect(() => {
    if (selectionMode && !allMatchingSelected) setSelectedIds(new Set())
  }, [allMatchingSelected, page, selectionMode])

  return {
    selectionMode, setSelectionMode,
    selectedIds, allMatchingSelected, excludedIds, selectedFingerprints,
    isLogSelected, selectedCount, currentPageSelectedCount, allCurrentPageSelected,
    clearSelection, exitSelectionMode, selectAllMatchingLogs,
    toggleSelect, toggleSelectAll, toggleGroupSelect,
    getBatchPayload,
  }
}
