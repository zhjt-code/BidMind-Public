import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { errorLogService } from '@/services/errorLogApi'
import type { ErrorLogItem } from '../types'

interface UseErrorLogDetailOptions {
  page: number
}

export interface UseErrorLogDetailResult {
  expandedId: number | null
  detailCache: Record<number, ErrorLogItem>
  detailLoadingId: number | null
  handleToggleExpand: (id: number) => void
  reset: () => void
}

export function useErrorLogDetail(opts: UseErrorLogDetailOptions): UseErrorLogDetailResult {
  const { page } = opts

  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [detailCache, setDetailCache] = useState<Record<number, ErrorLogItem>>({})
  const [detailLoadingId, setDetailLoadingId] = useState<number | null>(null)

  const loadDetail = useCallback(async (id: number) => {
    if (detailCache[id]) return
    setDetailLoadingId(id)
    try {
      const res = await errorLogService.getDetail(id) as { data?: ErrorLogItem }
      if (res?.data) {
        setDetailCache(prev => ({ ...prev, [id]: res.data as ErrorLogItem }))
      }
    } catch {
      toast.error('加载详情失败')
    } finally {
      setDetailLoadingId(null)
    }
  }, [detailCache])

  const handleToggleExpand = useCallback((id: number) => {
    const nextId = expandedId === id ? null : id
    setExpandedId(nextId)
    if (nextId !== null) loadDetail(nextId)
  }, [expandedId, loadDetail])

  const reset = useCallback(() => {
    setDetailCache({})
    setExpandedId(null)
  }, [])

  // page 切换时清缓存与展开
  useEffect(() => {
    setDetailCache({})
    setExpandedId(null)
  }, [page])

  return { expandedId, detailCache, detailLoadingId, handleToggleExpand, reset }
}
