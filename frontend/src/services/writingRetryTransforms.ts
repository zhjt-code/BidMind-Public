export interface WritingRetryState {
  retryable: boolean
  failedNode: string | null
  targetId: number | null
  failedStage: string | null
  retryActionLabel: string
  preservedOutputs: string[]
  resetScope: string[]
  lastAttempt: number
  retryRunning: boolean
  lastError: string | null
}

export function transformRetryState(raw: Record<string, unknown>): WritingRetryState {
  return {
    retryable: Boolean(raw.retryable),
    failedNode: (raw.failed_node as string | null) ?? null,
    targetId: (raw.target_id as number | null) ?? null,
    failedStage: (raw.failed_stage as string | null) ?? null,
    retryActionLabel: (raw.retry_action_label as string) || '重试',
    preservedOutputs: Array.isArray(raw.preserved_outputs)
      ? (raw.preserved_outputs as string[])
      : [],
    resetScope: Array.isArray(raw.reset_scope)
      ? (raw.reset_scope as string[])
      : [],
    lastAttempt: (raw.last_attempt as number) || 0,
    retryRunning: Boolean(raw.retry_running),
    lastError: (raw.last_error as string | null) ?? null,
  }
}
