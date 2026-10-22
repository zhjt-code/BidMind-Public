import type { WritingRetryState } from '@/services/writingRetryTransforms'

export const WRITING_FAILURE_RETRY_NODES = [
  'writing_extract',
  'writing_outline',
  'writing_material_process',
  'writing_match',
  'writing_compose',
  'writing_compose_single',
  'writing_render',
] as const

export function getWritingFailedNodeRoute(projectId: number | string, failedNode: string | null | undefined): string {
  switch (failedNode) {
    case 'writing_outline':
      return `/project/${projectId}/writing/outline`
    case 'writing_material_process':
      return `/project/${projectId}/writing/materials`
    case 'writing_match':
      return `/project/${projectId}/writing/binding`
    case 'writing_compose':
    case 'writing_compose_single':
      return `/project/${projectId}/writing/compose`
    case 'writing_render':
      return `/project/${projectId}/writing/preview`
    case 'writing_extract':
    case 'writing_tender_process':
    default:
      return `/project/${projectId}/writing/upload`
  }
}

export function isActionableRetryState(state: WritingRetryState | null | undefined): state is WritingRetryState {
  return Boolean(state && (state.failedNode || state.retryable || state.retryRunning))
}
