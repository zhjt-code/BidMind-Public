/**
 * 职责: 统一写标活跃任务进度轮询节拍与墙钟预算。
 * 不做: 不管理文档处理自适应轮询、导航延时、自动保存或普通重试。
 */

import { sanitizeProgressText } from '../../services/progressStatusTransforms.ts'

export const WRITING_PROGRESS_POLL_INTERVAL_MS = 1_000
export const WRITING_HIDDEN_POLL_INTERVAL_MS = 5_000
export const WRITING_LONG_POLL_TIMEOUT_MS = 120 * 60_000
export const WRITING_SINGLE_POLL_TIMEOUT_MS = 6 * 60_000
export const WRITING_LONG_POLL_MAX_ATTEMPTS =
  WRITING_LONG_POLL_TIMEOUT_MS / WRITING_PROGRESS_POLL_INTERVAL_MS
export const WRITING_SINGLE_POLL_MAX_ATTEMPTS =
  WRITING_SINGLE_POLL_TIMEOUT_MS / WRITING_PROGRESS_POLL_INTERVAL_MS

const TERMINAL_FAILURE_STAGES = new Set([
  'failed',
  'cancelled',
  'canceled',
  'interrupted',
  'unknown',
])

export interface WritingTaskStatus {
  active: boolean
  stage: string | null
  progress?: string | null
  progressPct: number
  currentDetail: string
}

export interface PollWritingTaskOptions {
  fetchStatus: () => Promise<WritingTaskStatus>
  timeoutMs: number
  signal?: AbortSignal
  onProgress?: (status: WritingTaskStatus) => void
  visibleIntervalMs?: number
  hiddenIntervalMs?: number
}

export function isWritingPollAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
}

function makeAbortError(): Error {
  const error = new Error('polling aborted')
  error.name = 'AbortError'
  return error
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) {
    throw makeAbortError()
  }
}

function currentIntervalMs(options: PollWritingTaskOptions): number {
  const visible = options.visibleIntervalMs ?? WRITING_PROGRESS_POLL_INTERVAL_MS
  const hidden = options.hiddenIntervalMs ?? WRITING_HIDDEN_POLL_INTERVAL_MS
  const isHidden = typeof document !== 'undefined' && document.hidden === true
  return isHidden ? hidden : visible
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    throwIfAborted(signal)
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    const onAbort = () => {
      clearTimeout(timer)
      reject(makeAbortError())
    }
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

function terminalError(status: WritingTaskStatus): Error {
  const stage = String(status.stage || 'unknown').toLowerCase()
  const detail = sanitizeProgressText(status.currentDetail).trim()
  if (stage === 'failed') {
    return new Error(detail || '生成失败')
  }
  if (stage === 'cancelled' || stage === 'canceled') {
    return new Error(detail || '任务已取消')
  }
  if (stage === 'interrupted') {
    return new Error(detail || '任务已中断')
  }
  return new Error(detail || '生成状态异常')
}

export async function pollWritingTask(
  options: PollWritingTaskOptions,
): Promise<WritingTaskStatus> {
  const started = Date.now()
  throwIfAborted(options.signal)

  while (true) {
    throwIfAborted(options.signal)
    if (Date.now() - started >= options.timeoutMs) {
      throw new Error('生成超时')
    }

    const status = await options.fetchStatus()
    throwIfAborted(options.signal)
    options.onProgress?.(status)

    const stage = String(status.stage || '').toLowerCase()
    if (!status.active && stage === 'completed') {
      return status
    }
    if (!status.active) {
      if (!TERMINAL_FAILURE_STAGES.has(stage)) {
        throw terminalError({ ...status, stage: stage || 'unknown' })
      }
      throw terminalError({ ...status, stage })
    }

    const remaining = options.timeoutMs - (Date.now() - started)
    if (remaining <= 0) {
      throw new Error('生成超时')
    }
    await delay(Math.min(currentIntervalMs(options), remaining), options.signal)
  }
}
