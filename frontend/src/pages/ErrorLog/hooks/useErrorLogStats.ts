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

  const reload = useCallback(async () => {
    setStatsLoading(true)
    try {
      const hours = timeRange > 0 ? timeRange : 168
      const res = await errorLogService.getStats({ hours }) as { data?: ErrorStats }
      setStats(res?.data || null)
    } catch {
      setStats(null)
    } finally {
      setStatsLoading(false)
    }
  }, [timeRange])

  useEffect(() => { reload() }, [reload])

  return { stats, statsLoading, reload }
}
