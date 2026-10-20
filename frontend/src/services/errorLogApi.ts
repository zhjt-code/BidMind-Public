import { api } from './httpClient'

type ErrorLogBatchPayload = {
  log_ids?: number[]
  select_all?: boolean
  exclude_ids?: number[]
  filters?: Record<string, unknown>
}

const normalizeErrorLogBatchPayload = (
  payload: number[] | ErrorLogBatchPayload,
): ErrorLogBatchPayload => (Array.isArray(payload) ? { log_ids: payload } : payload)

export const errorLogService = {
  getLogs: async (params: Record<string, unknown> = {}) => {
    return api.get('/admin/error-logs', { params })
  },
  getGroupedLogs: async (params: Record<string, unknown> = {}) => {
    return api.get('/admin/error-logs/grouped', { params })
  },
  getDetail: async (id: number) => {
    return api.get(`/admin/error-logs/${id}`)
  },
  getStats: async (params: { hours?: number } = {}) => {
    return api.get('/admin/error-logs/stats', { params })
  },
  resolve: async (id: number) => {
    return api.patch(`/admin/error-logs/${id}/resolve`)
  },
  batchDelete: async (payload: number[] | ErrorLogBatchPayload) => {
    return api.post('/admin/error-logs/batch-delete', normalizeErrorLogBatchPayload(payload))
  },
  batchResolve: async (payload: number[] | ErrorLogBatchPayload) => {
    return api.post('/admin/error-logs/batch-resolve', normalizeErrorLogBatchPayload(payload))
  },
  suppress: async (logId: number, data: { reason?: string } = {}) => {
    return api.post(`/admin/error-logs/${logId}/suppress`, data)
  },
  getSuppressionRules: async (params: Record<string, unknown> = {}) => {
    return api.get('/admin/error-logs/suppression-rules', { params })
  },
  toggleSuppressionRule: async (ruleId: number, data: { enabled: boolean }) => {
    return api.patch(`/admin/error-logs/suppression-rules/${ruleId}`, data)
  },
  deleteSuppressionRule: async (ruleId: number) => {
    return api.delete(`/admin/error-logs/suppression-rules/${ruleId}`)
  },
  recomputeFingerprints: async () => {
    return api.post('/admin/error-logs/recompute-fingerprints')
  },
  getChain: async (incidentId: string) => {
    return api.get(`/admin/error-logs/chain/${incidentId}`)
  },
  getProjectTimeline: async (projectId: number, params: Record<string, unknown> = {}) => {
    return api.get(`/admin/projects/${projectId}/timeline`, { params })
  },
}
