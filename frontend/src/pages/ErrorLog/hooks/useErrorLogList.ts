import { useCallback, useEffect, useState } from 'react'
import { errorLogService } from '@/services/errorLogApi'
import { withSampledStartDate } from './useErrorLogFilters'
import type { ErrorLogItem } from '../types'
import { PAGE_SIZE } from '../constants'

interface UseErrorLogListOptions {
  viewMode: 'grouped' | 'flat'
  activeFilters: Record<string, unknown>
  page: number
  onTotal: (total: number) => void
  onTotalPages: (totalPages: number) => void
}

export interface UseErrorLogListResult {
  logs: ErrorLogItem[]
  loading: boolean
  reload: () => Promise<void>
}

export function useErrorLogList(opts: UseErrorLogListOptions): UseErrorLogListResult {
  const { viewMode, activeFilters, page, onTotal, onTotalPages } = opts

  const [logs, setLogs] = useState<ErrorLogItem[]>([])
  const [loading, setLoading] = useState(true)

  const [prevListQuery, setPrevListQuery] = useState({ viewMode, activeFilters, page })
  if (
    viewMode !== prevListQuery.viewMode ||
    activeFilters !== prevListQuery.activeFilters ||
    page !== prevListQuery.page
  ) {
    setPrevListQuery({ viewMode, activeFilters, page })
    if (viewMode === 'flat') setLoading(true)
  }

  const fetchLogs = useCallback(async () => {
    let nextLogs: ErrorLogItem[] = []
    let nextTotal = 0
    let nextTotalPages = 0
    try {
      const params = withSampledStartDate({
        page,
        page_size: PAGE_SIZE,
        ...activeFilters,
      })
      const res = await errorLogService.getLogs(params) as {
        data?: { items?: ErrorLogItem[]; total?: number; total_pages?: number }
      }
      const data = res?.data
      nextLogs = data?.items || []
      nextTotal = data?.total || 0
      nextTotalPages = data?.total_pages || 0
    } catch {
      nextLogs = []
      nextTotal = 0
      nextTotalPages = 0
    } finally {
      setLogs(nextLogs)
      onTotal(nextTotal)
      onTotalPages(nextTotalPages)
      setLoading(false)
    }
  }, [activeFilters, page, onTotal, onTotalPages])

  const reload = useCallback(async () => {
    setLoading(true)
    await fetchLogs()
  }, [fetchLogs])

  useEffect(() => {
    if (viewMode === 'flat') void fetchLogs()
  }, [fetchLogs, viewMode])

  return { logs, loading, reload }
}
