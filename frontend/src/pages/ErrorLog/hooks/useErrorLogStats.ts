import { useCallback, useEffect, useState } from 'react'
import { errorLogService } from '@/services/errorLogApi'
import type { ErrorStats } from '../types'

export interface UseErrorLogStatsResult {
  stats: ErrorStats | null
  statsLoading: boolean
  reload: () => Promise<void>
}

export function useErrorLogStats(timeRange: number): UseErrorLogStatsResult {
  const [stats, setStats] = useState<ErrorStats | null>(null)
  const [statsLoading, setStatsLoading] = useState(true)

  const [prevTimeRange, setPrevTimeRange] = useState(timeRange)
  if (timeRange !== prevTimeRange) {
    setPrevTimeRange(timeRange)
    setStatsLoading(true)
  }

  const fetchStats = useCallback(async () => {
    let nextStats: ErrorStats | null = null
    try {
      const hours = timeRange > 0 ? timeRange : 168
      const res = await errorLogService.getStats({ hours }) as { data?: ErrorStats }
      nextStats = res?.data || null
    } catch {
      nextStats = null
    } finally {
      setStats(nextStats)
      setStatsLoading(false)
    }
  }, [timeRange])

  const reload = useCallback(async () => {
    setStatsLoading(true)
    await fetchStats()
  }, [fetchStats])

  useEffect(() => { void fetchStats() }, [fetchStats])

  return { stats, statsLoading, reload }
}
