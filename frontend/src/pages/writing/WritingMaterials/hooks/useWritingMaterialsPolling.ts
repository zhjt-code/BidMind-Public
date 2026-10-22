/**
 * 写标素材批处理轮询 hook。
 *
 * 从 WritingMaterials.tsx 抽出，承载完整的轮询状态机：
 * - 完成判定严格依赖后端 phase==='done'；phase==='failed' 判失败；
 * idle 仅代表"任务已静默"，不等同于完成（防止刚打开页面误判）。
 * - 偶发网络/超时错误不静默丢弃，按"连续无推进时长"耐受窗口处理。
 * - 状态签名变化（phase / heartbeatPhase / vlProgress / processing / completed /
 * failed / taskActive）算推进。
 * - startBatchProcess 的 POST 错误分流：余额不足 403 停轮询并经
 * onBalanceBlocked 上抛阻断；瞬态超时保持静默（后端可能仍在继续处理）。
 *
 * 关键实现约束（破坏将导致行为退化）：
 * - lastProgressKey / lastAdvanceAt / consecutiveErrors 必须用 useRef 保活，
 * 用 useState 会触发 re-render 重建 hook，破坏轮询节拍。
 * - pollRef 是 mutable，必须在 hook unmount 时清理。
 * - 完成后的副作用顺序必须保持：清轮询 → setIsProcessing(false) →
 * 调 onSettled(失败时含 refreshRetryState)。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { parseInsufficientBalanceError } from '@/lib/insufficientBalance'
import type { MaterialBatchStatus } from '@/services/writingApi'
import { writingService } from '@/services/writingApi'
import type { ProgressStep } from '@/hooks/useProgressAnimation'
import { mapBatchStatusToSteps } from '../batchStatusMapper.ts'
import { POLL_MAX_NO_ADVANCE_MS } from '../constants.ts'
import { WRITING_PROGRESS_POLL_INTERVAL_MS } from '../../progressPolling.ts'

interface UseWritingMaterialsPollingOptions {
  pid: number
  /** 透传给 useProgressAnimation 的进度推进入口 */
  updateTargets: (steps: ProgressStep[], overallTarget: number) => void
  /** 每次成功拿到 status 后的同步回调（用于 setBatchStatus / setRetryState 等） */
  onStatusTick: (status: MaterialBatchStatus) => void
  /** 任务到达终态（done / failed / taskSettledIdle）后的清理回调 */
  onSettled: (terminal: 'done' | 'failed' | 'idle') => Promise<void>
  /** 批处理 POST 命中余额不足 403 时的阻断回调（瞬态超时不触发） */
  onBalanceBlocked?: (error: unknown) => void
}

export interface WritingMaterialsPollingController {
  isProcessing: boolean
  stageText: string
  /** 启动轮询（幂等：已有轮询会先 cleanup 再启动） */
  start: () => void
  /** 主动停止并标记非 processing（用于切页/手动取消） */
  stop: () => void
  /** 触发 processMaterialsBatch 并同步启动轮询 */
  startBatchProcess: () => Promise<void>
  /** 仅用于上层判断是否有活跃 timer（auto-resume 边界） */
  hasActivePoll: () => boolean
}

export function useWritingMaterialsPolling(
  options: UseWritingMaterialsPollingOptions,
): WritingMaterialsPollingController {
  const { pid, updateTargets, onStatusTick, onSettled, onBalanceBlocked } = options

  const [isProcessing, setIsProcessing] = useState(false)
  const [stageText, setStageText] = useState('')

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const lastProgressKeyRef = useRef('')
  const lastAdvanceAtRef = useRef(Date.now())
  const consecutiveErrorsRef = useRef(0)
  const inFlightRef = useRef(false)

  // 用 ref 保存 props 回调，避免每次 props 变化都重建 interval。
  const updateTargetsRef = useRef(updateTargets)
  const onStatusTickRef = useRef(onStatusTick)
  const onSettledRef = useRef(onSettled)
  const onBalanceBlockedRef = useRef(onBalanceBlocked)
  useEffect(() => {
    updateTargetsRef.current = updateTargets
    onStatusTickRef.current = onStatusTick
    onSettledRef.current = onSettled
    onBalanceBlockedRef.current = onBalanceBlocked
  }, [updateTargets, onStatusTick, onSettled, onBalanceBlocked])

  const cleanupPoll = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
    inFlightRef.current = false
  }, [])

  const start = useCallback(() => {
    cleanupPoll()
    lastProgressKeyRef.current = ''
    lastAdvanceAtRef.current = Date.now()
    consecutiveErrorsRef.current = 0
    inFlightRef.current = false

    pollRef.current = setInterval(async () => {
      if (inFlightRef.current) return
      inFlightRef.current = true
      try {
        const status = await writingService.getMaterialsProcessStatus(pid)
        consecutiveErrorsRef.current = 0
        onStatusTickRef.current(status)
        const mapped = mapBatchStatusToSteps(status)
        updateTargetsRef.current(mapped.steps, mapped.overallTarget)
        setStageText(mapped.stageText)

        const progressKey = [
          status.phase,
          status.heartbeatPhase ?? '',
          status.vlProgress,
          status.processing,
          status.completed,
          status.failed,
          status.taskActive ? '1' : '0',
        ].join('|')
        if (progressKey !== lastProgressKeyRef.current) {
          lastProgressKeyRef.current = progressKey
          lastAdvanceAtRef.current = Date.now()
        }

        const hardDone = status.phase === 'done'
        const hardFailed = status.phase === 'failed'
        // idle + 任务已静默 + 确实没有处于 processing 的行 才认为"任务事实上已结束"。
        // 同时要求 completed > 0 以避免刚打开页面、心跳还没建立时被误判。
        const taskSettledIdle =
          status.phase === 'idle' &&
          !status.taskActive &&
          status.processing === 0 &&
          status.completed > 0

        if (hardDone || hardFailed || taskSettledIdle) {
          cleanupPoll()
          setIsProcessing(false)
          const terminal: 'done' | 'failed' | 'idle' = hardDone
            ? 'done'
            : hardFailed
              ? 'failed'
              : 'idle'
          await onSettledRef.current(terminal)
          return
        }
      } catch (e) {
        consecutiveErrorsRef.current += 1
        const noAdvanceMs = Date.now() - lastAdvanceAtRef.current
        if (consecutiveErrorsRef.current === 1 || consecutiveErrorsRef.current % 10 === 0) {
          const msg = e instanceof Error ? e.message : String(e)
          // eslint-disable-next-line no-console
          console.warn(
            `[writing-materials] poll 瞬态错误 (连续${consecutiveErrorsRef.current}次, 无推进${Math.round(noAdvanceMs / 1000)}s): ${msg}`,
          )
        }
        if (noAdvanceMs > POLL_MAX_NO_ADVANCE_MS && consecutiveErrorsRef.current >= 3) {
          cleanupPoll()
          setIsProcessing(false)
          toast.error('素材处理状态长时间未更新，请刷新页面查看最新进度')
        }
      } finally {
        inFlightRef.current = false
      }
    }, WRITING_PROGRESS_POLL_INTERVAL_MS)
  }, [pid, cleanupPoll])

  const stop = useCallback(() => {
    cleanupPoll()
    setIsProcessing(false)
  }, [cleanupPoll])

  const startBatchProcess = useCallback(async () => {
    setIsProcessing(true)
    start()
    try {
      await writingService.processMaterialsBatch(pid)
    } catch (e) {
      // 余额不足 403：停轮询、退出 processing 态并上抛阻断；
      // 其余瞬态超时保持静默，后端可能仍在继续处理，交由轮询兜底。
      if (parseInsufficientBalanceError(e)) {
        cleanupPoll()
        setIsProcessing(false)
        onBalanceBlockedRef.current?.(e)
      }
    }
  }, [pid, start, cleanupPoll])

  const hasActivePoll = useCallback(() => pollRef.current != null, [])

  // 包一层 wrapper 让外部触发 start 时 isProcessing 同步置 true（与原页面行为一致）。
  const startWithFlag = useCallback(() => {
    setIsProcessing(true)
    start()
  }, [start])

  useEffect(() => cleanupPoll, [cleanupPoll])

  return {
    isProcessing,
    stageText,
    start: startWithFlag,
    stop,
    startBatchProcess,
    hasActivePoll,
  }
}
