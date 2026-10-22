import { useEffect, useMemo, useRef } from 'react'
import type { Project } from '@/stores/projectStore'
import { LIST_POLL_STATUSES } from '../types'

const POLL_INTERVAL_MS = 30_000

interface UseProjectListRefreshArgs {
  projects: Project[]
  fetchProjects: () => Promise<void>
  isAuthenticated: boolean
  accessToken: string | null
}

export function useProjectListRefresh({
  projects,
  fetchProjects,
  isAuthenticated,
  accessToken,
}: UseProjectListRefreshArgs): void {
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const hasProcessing = useMemo(
    () => projects.some((project) => LIST_POLL_STATUSES.includes(project.status)),
    [projects],
  )

  useEffect(() => {
    if (!isAuthenticated) return
    void fetchProjects()
  }, [fetchProjects, isAuthenticated, accessToken])

  useEffect(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current)
      pollingRef.current = null
    }

    if (hasProcessing) {
      pollingRef.current = setInterval(() => fetchProjects(), POLL_INTERVAL_MS)
    }

    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current)
    }
  }, [hasProcessing, fetchProjects])
}
