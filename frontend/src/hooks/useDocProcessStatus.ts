/**
 * Hook for polling document processing status via TanStack Query.
 * Replaces manual setTimeout-based polling in FileUpload/CollusionUpload.
 */

import { useQuery } from '@tanstack/react-query'
import { documentService, type ProcessStatusData } from '@/services/documentApi'

interface UseDocProcessStatusOptions {
  projectId: number | undefined
  documentId: number | undefined
  enabled?: boolean
  /** Polling interval in ms; pass a function for dynamic intervals */
  intervalMs?: number | ((data: ProcessStatusData | undefined) => number | false)
}

export function useDocProcessStatus({
  projectId,
  documentId,
  enabled = true,
  intervalMs = 3000,
}: UseDocProcessStatusOptions) {
  return useQuery({
    queryKey: ['doc-process-status', projectId, documentId],
    queryFn: async () => {
      const result = await documentService.getProcessStatus(projectId!, documentId!)
      return result.data as ProcessStatusData
    },
    enabled: enabled && !!projectId && !!documentId,
    refetchInterval: (query) => {
      const data = query.state.data
      if (data?.status === 'completed' || data?.status === 'failed') return false
      if (typeof intervalMs === 'function') return intervalMs(data)
      return intervalMs
    },
  })
}
