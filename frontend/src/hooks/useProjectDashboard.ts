import { useCallback } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  PROJECT_DASHBOARD_QUERY_KEY,
  projectDashboardQueryKey,
} from '@/services/projectDashboardApi'
import { projectDashboardService } from '@/services/projectDashboardService'

interface UseProjectDashboardArgs {
  userId?: number
  role?: string
  enabled: boolean
  hasProcessingProjects: boolean
  trendWindowDays?: number
  healthWindowDays?: number
}

export function useProjectDashboard({
  userId,
  role,
  enabled,
  hasProcessingProjects,
  trendWindowDays = 30,
  healthWindowDays = 30,
}: UseProjectDashboardArgs) {
  return useQuery({
    queryKey: projectDashboardQueryKey(userId, role, trendWindowDays, healthWindowDays),
    queryFn: () => projectDashboardService.getDashboard(trendWindowDays, healthWindowDays),
    enabled,
    staleTime: 30_000,
    refetchInterval: hasProcessingProjects ? 30_000 : false,
    placeholderData: (previous) => previous,
  })
}

export function useInvalidateProjectDashboard(): () => Promise<void> {
  const queryClient = useQueryClient()
  return useCallback(
    () => queryClient.invalidateQueries({ queryKey: [PROJECT_DASHBOARD_QUERY_KEY] }),
    [queryClient],
  )
}
