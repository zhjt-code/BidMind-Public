import { api } from './httpClient'
import type { ApiResponse } from './apiTypes'
import { mapProgressStatusSteps, sanitizeProgressText, unwrapProgressStatusPayload } from './progressStatusTransforms'
import { transformRetryState, type WritingRetryState } from './writingRetryTransforms'

export interface ReviewStepStatus {
  label: string
  status: 'waiting' | 'running' | 'completed' | 'failed'
  progress: number
  detail?: string
}

export interface ReviewStatus {
  projectId: number
  overallStatus: 'idle' | 'running' | 'completed' | 'failed'
  overallProgress: number
  steps: ReviewStepStatus[]
  errorMessage?: string
  message?: string
  isRecovering: boolean
  recoveryCount: number
  retryable?: boolean
  failedNode?: string | null
  targetId?: number | null
  failedStage?: string | null
  retryActionLabel?: string
  preservedOutputs?: string[]
  resetScope?: string[]
  lastAttempt?: number
  retryRunning?: boolean
  lastError?: string | null
}

export interface StartReviewResult {
  project_id: number
  requirements_count: number
  status: 'processing' | 'completed'
  queue_mode?: string
  already_started?: boolean
  already_completed?: boolean
  extraction_warnings?: unknown
}

export const reviewService = {
  startReview: async (projectId: number): Promise<ApiResponse<StartReviewResult>> => {
    return api.post<ApiResponse<StartReviewResult>>(`/projects/${projectId}/review`)
  },

  getReviewStatus: async (projectId: number): Promise<ReviewStatus> => {
    const response = await api.get<ApiResponse<Record<string, unknown>>>(
      `/projects/${projectId}/review/status`,
    )
    const d = unwrapProgressStatusPayload(response, '审查状态')
    return {
      projectId: d.project_id as number,
      overallStatus: d.overall_status as ReviewStatus['overallStatus'],
      overallProgress: d.overall_progress as number,
      steps: mapProgressStatusSteps(d.steps).map((step) => ({
        label: step.label,
        status: step.status as ReviewStepStatus['status'],
        progress: step.progress,
        detail: step.detail,
      })),
      errorMessage: d.error_message as string | undefined,
      message: sanitizeProgressText(d.message as string | undefined) || undefined,
      isRecovering: (d.is_recovering as boolean) || false,
      recoveryCount: (d.recovery_count as number) || 0,
      retryable: (d.retryable as boolean) || false,
      failedNode: (d.failed_node as string | null) ?? null,
      targetId: (d.target_id as number | null) ?? null,
      failedStage: (d.failed_stage as string | null) ?? null,
      retryActionLabel: (d.retry_action_label as string | undefined) || undefined,
      preservedOutputs: Array.isArray(d.preserved_outputs)
        ? (d.preserved_outputs as string[])
        : [],
      resetScope: Array.isArray(d.reset_scope) ? (d.reset_scope as string[]) : [],
      lastAttempt: (d.last_attempt as number) || 0,
      retryRunning: Boolean(d.retry_running),
      lastError: (d.last_error as string | null) ?? null,
    }
  },

  getReviewResults: async (projectId: number, includeDebug = false) => {
    const suffix = includeDebug ? '?include_debug=true' : ''
    return api.get(`/projects/${projectId}/results${suffix}`)
  },

  getRetryState: async (projectId: number): Promise<WritingRetryState> => {
    const response = await api.get<ApiResponse<Record<string, unknown>>>(
      `/projects/${projectId}/review/retry-state`,
    )
    return transformRetryState(unwrapProgressStatusPayload(response, '审查重试状态'))
  },

  retryReview: async (
    projectId: number,
    nodeKey?: string | null,
    targetId?: number | null,
  ): Promise<WritingRetryState> => {
    const response = await api.post<ApiResponse<Record<string, unknown>>>(
      `/projects/${projectId}/review/retry`,
      nodeKey || targetId != null ? { node_key: nodeKey, target_id: targetId } : {},
    )
    return transformRetryState(unwrapProgressStatusPayload(response, '审查重试触发'))
  },
}
