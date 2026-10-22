/**
 * 职责: express 审标进度页在未进入 reviewing 时启动提取并组合状态
 * 不做: 不确认要求、不启动审查
 */
import { useEffect } from 'react'
import { requirementService } from '@/services/requirementApi'
import { reviewService } from '@/services/reviewApi'
import { projectService } from '@/services/projectApi'
import { isExpressRunMode } from '@/lib/runMode'
import type { CommonStatus } from '@/components/GenericProgressPage'
import {
  mapPreReviewExpressStatus,
  prependCompletedExtractSteps,
  shouldBootstrapReviewExtract,
} from './expressReviewStatus'

export function useExpressReviewExtract(
  projectId: number,
  runMode?: string | null,
  projectStatus?: string | null,
) {
  useEffect(() => {
    if (!projectId || !isExpressRunMode(runMode) || !shouldBootstrapReviewExtract(projectStatus)) {
      return
    }
    let cancelled = false
    void requirementService.getExtractStatus(projectId).then((status) => {
      if (cancelled || status.status !== 'idle') return
      return requirementService.extractRequirements(projectId)
    }).catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [projectId, runMode, projectStatus])
}

export async function fetchExpressReviewStatus(projectId: number): Promise<CommonStatus> {
  const project = await projectService.getProject(projectId)
  if (project.status === 'reviewing' || project.status === 'completed' || project.status === 'failed') {
    const review = await reviewService.getReviewStatus(projectId)
    return prependCompletedExtractSteps(review)
  }
  const extract = await requirementService.getExtractStatus(projectId)
  return mapPreReviewExpressStatus(project.status, extract)
}
