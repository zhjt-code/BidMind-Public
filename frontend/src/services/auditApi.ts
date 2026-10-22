import { api } from './httpClient'

type AuditLogBatchPayload = {
  log_ids?: number[]
  select_all?: boolean
  exclude_ids?: number[]
  filters?: Record<string, unknown>
}

const normalizeAuditLogBatchPayload = (
  payload: number[] | AuditLogBatchPayload,
): AuditLogBatchPayload => (Array.isArray(payload) ? { log_ids: payload } : payload)

export const auditService = {
  getLogs: async (params: Record<string, unknown> = {}) => {
    return api.get('/admin/audit-logs', { params })
  },
  getStats: async () => {
    return api.get('/admin/audit-logs/stats')
  },
  batchDelete: async (payload: number[] | AuditLogBatchPayload) => {
    return api.post('/admin/audit-logs/batch-delete', normalizeAuditLogBatchPayload(payload))
  },
}
