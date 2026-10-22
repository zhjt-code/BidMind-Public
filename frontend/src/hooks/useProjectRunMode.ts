/**
 * 职责: 读取并 PATCH 项目 run_mode
 * 不做: 不决定上传后跳转、不启动提取或审查
 */
import { useCallback, useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import {
  isRunModeLocked,
  normalizeRunMode,
  type RunMode,
} from '@/lib/runMode'
import { projectQueryKey, projectService } from '@/services/projectApi'

export function useProjectRunMode(projectId: number | string | undefined) {
  const queryClient = useQueryClient()
  const [runMode, setRunModeState] = useState<RunMode>('guided')
  const [status, setStatus] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!projectId) {
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    projectService.getProject(Number(projectId)).then((project) => {
      if (cancelled) return
      setRunModeState(normalizeRunMode(project.runMode))
      setStatus(project.status)
    }).catch(() => {
      if (!cancelled) setRunModeState('guided')
    }).finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => {
      cancelled = true
    }
  }, [projectId])

  const locked = isRunModeLocked(status) || saving

  const setRunMode = useCallback(async (next: RunMode) => {
    if (!projectId || locked || next === runMode) return
    const previous = runMode
    setRunModeState(next)
    setSaving(true)
    try {
      const project = await projectService.setRunMode(Number(projectId), next)
      setRunModeState(normalizeRunMode(project.runMode))
      setStatus(project.status)
      await queryClient.invalidateQueries({ queryKey: projectQueryKey(Number(projectId)) })
    } catch (error) {
      setRunModeState(previous)
      toast.error(error instanceof Error ? error.message : '运行模式更新失败')
    } finally {
      setSaving(false)
    }
  }, [locked, projectId, queryClient, runMode])

  return { runMode, setRunMode, locked, loading }
}
