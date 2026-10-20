import { useCallback, useEffect, useState } from 'react'
import { errorLogService } from '@/services/errorLogApi'
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

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const params: Record<string, unknown> = {
        page,
        page_size: PAGE_SIZE,
        ...activeFilters,
      }
      const res = await errorLogService.getLogs(params) as {
        data?: { items?: ErrorLogItem[]; total?: number; total_pages?: number }
      }
      const data = res?.data
      setLogs(data?.items || [])
      onTotal(data?.total || 0)
      onTotalPages(data?.total_pages || 0)
    } catch {
      setLogs([])
      onTotal(0)
      onTotalPages(0)
    } finally {
      setLoading(false)
    }
  }, [activeFilters, page, onTotal, onTotalPages])

  useEffect(() => {
    if (viewMode === 'flat') reload()
  }, [reload, viewMode])

  return { logs, loading, reload }
}
