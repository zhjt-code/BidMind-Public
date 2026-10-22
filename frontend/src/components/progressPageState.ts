import { formatFailureMessage } from '../lib/formatFailureMessage.ts'
import { normalizeOverallStatus } from '../services/progressStatusTransforms.ts'

interface ProgressErrorArgs {
  overallStatus?: string | null
  errorMessage?: string | null
  queryError?: unknown
  failedText: string
}

export function formatProgressQueryError(queryError: unknown): string {
  return `获取状态失败: ${queryError instanceof Error ? queryError.message : '未知错误'}`
}

export function getProgressPageError({
  overallStatus,
  errorMessage,
  queryError,
  failedText,
}: ProgressErrorArgs): string | null {
  if (queryError) {
    return formatProgressQueryError(queryError)
  }

  if (overallStatus === 'failed') {
    if (errorMessage && errorMessage.trim()) {
      return formatFailureMessage(errorMessage)
    }
    return failedText
  }

  return null
}

export function isProgressCompleted(overallStatus: string | null | undefined): boolean {
  return normalizeOverallStatus(overallStatus) === 'completed'
}

export function nextProgressSessionStatus(
  sessionStatus: string | null,
  overallStatus: string | null | undefined,
): string {
  const normalized = normalizeOverallStatus(overallStatus)
  if (sessionStatus === null) return normalized
  if (normalized === 'running') return 'running'
  return sessionStatus
}
