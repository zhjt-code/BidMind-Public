import { useState, useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { writingService } from '@/services/writingApi'
import { projectService } from '@/services/projectApi'
import { normalizeRunMode } from '@/lib/runMode'
import { getWritingProjectRoute, isWritingExpressPipeline, WRITING_STATUS_ORDER } from './writingConstants.ts'
import { APP_HOME } from '@/config/routes'

interface WritingGuardResult {
  ready: boolean
  projectStatus: string | null
  loading: boolean
}

const GUARD_MAX_RETRIES = 2
const GUARD_RETRY_DELAY_MS = 1500

export function useWritingGuard(
  projectId: number,
  minStatus: string,
): WritingGuardResult {
  const navigate = useNavigate()
  const location = useLocation()
  const [ready, setReady] = useState(false)
  const [projectStatus, setProjectStatus] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!projectId || isNaN(projectId)) {
      navigate(APP_HOME)
      return
    }

    let cancelled = false

    const check = async () => {
      let attempt = 0
      while (!cancelled) {
        attempt += 1
        try {
          const [raw, project] = await Promise.all([
            writingService.getStatus(projectId),
            projectService.getProject(projectId).catch(() => null),
          ])
          if (cancelled) return

          const status = raw.status || 'created'
          const runMode = normalizeRunMode(project?.runMode)
          setProjectStatus(status)

          const currentOrder = WRITING_STATUS_ORDER[status] ?? -1
          const minOrder = WRITING_STATUS_ORDER[minStatus] ?? 0
          const target = getWritingProjectRoute(projectId, status, runMode)

          if (status === 'failed' && runMode !== 'express') {
            setReady(true)
            return
          }
          if (currentOrder < minOrder || (isWritingExpressPipeline(status, runMode) && location.pathname !== target)) {
            if (location.pathname !== target) {
              navigate(`${target}?from=guard`, { replace: true })
              return
            }
          }
          setReady(true)
          return
        } catch (err) {
          if (cancelled) return
          if (attempt > GUARD_MAX_RETRIES) {
            console.warn(
              `[writing-guard] getStatus 失败 ${attempt} 次, 放行未知态: ${(err as Error)?.message ?? err}`,
            )
            setReady(true)
            return
          }
          await new Promise((resolve) => setTimeout(resolve, GUARD_RETRY_DELAY_MS))
        }
      }
    }

    check().finally(() => {
      if (!cancelled) setLoading(false)
    })

    return () => { cancelled = true }
  }, [projectId, minStatus, navigate, location.pathname])

  return { ready, projectStatus, loading }
}
