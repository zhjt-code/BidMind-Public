import { api } from './httpClient'
import type { ApiResponse } from './apiTypes'
import { mapProgressStatusSteps, sanitizeProgressText } from './progressStatusTransforms'

interface ExtractStatusData {
  status: 'idle' | 'started' | 'running' | 'completed' | 'failed'
  stage: string
  progress: number
  message: string
  count: number
  overall_status?: 'idle' | 'running' | 'completed' | 'failed'
  overall_progress?: number
  steps?: Array<{ key?: string; label: string; status: string; progress: number; detail?: string }>
  error_message?: string
  is_recovering?: boolean
  recovery_count?: number
  extraction_warnings?: string[]
  run_id?: string
  revision?: number
}

export interface ExtractStartData {
  status?: string
  run_id?: string
  revision?: number
  skipped?: boolean
  in_progress?: boolean
  queue_mode?: string
  project_id?: number
  arq_job_id?: string | null
  cleared?: Record<string, number>
}

export const requirementService = {
  getRequirements: async (projectId: number) => {
    return api.get(`/projects/${projectId}/requirements`)
  },

  extractRequirements: async (projectId: number): Promise<ExtractStartData> => {
    const response = await api.post<ApiResponse<ExtractStartData>>(
      `/projects/${projectId}/requirements/extract`,
      null,
      {
        timeout: 30000,
      },
    )
    return response.data
  },

  reextractRequirements: async (projectId: number): Promise<ExtractStartData> => {
    const response = await api.post<ApiResponse<ExtractStartData>>(
      `/projects/${projectId}/requirements/reextract`,
      null,
      {
        timeout: 30000,
      },
    )
    return response.data
  },

  getExtractStatus: async (projectId: number): Promise<ExtractStatusData> => {
    const response = await api.get<ApiResponse<ExtractStatusData>>(
      `/projects/${projectId}/requirements/extract/status`,
    )
    const data = response.data
    return {
      ...data,
      message: sanitizeProgressText(data.message) || data.message,
      steps: mapProgressStatusSteps(data.steps).map((step) => ({
        key: step.key,
        label: step.label,
        status: step.status,
        progress: step.progress,
        detail: step.detail,
      })),
    }
  },

  updateRequirement: async (projectId: number, reqId: number, data: object) => {
    return api.put(`/projects/${projectId}/requirements/${reqId}`, data)
  },

  confirmRequirements: async (projectId: number, reqIds: number[]) => {
    return api.post(`/projects/${projectId}/requirements/confirm`, { requirement_ids: reqIds })
  },

  confirmAllRequirements: async (projectId: number) => {
    return api.post(`/projects/${projectId}/requirements/confirm-all`)
  },

  deleteRequirement: async (projectId: number, reqId: number) => {
    return api.delete(`/projects/${projectId}/requirements/${reqId}`)
  },

  createRequirement: async (
    projectId: number,
    data: { category: string; description: string; is_mandatory: boolean },
  ) => {
    return api.post(`/projects/${projectId}/requirements`, data)
  },

  createScoringItem: async (
    projectId: number,
    data: {
      category: string
      score_item_name: string
      description: string
      score_weight: number
      score_rules: string[]
      deduction_rules: string[]
      volume?: string | null
      volume_label?: string | null
      lot?: string | null
      source_pages: number[]
    },
  ) => {
    return api.post(`/projects/${projectId}/requirements/scoring-items`, data)
  },
}
