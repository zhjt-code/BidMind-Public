/**
 * 写标素材匹配生命周期 hook。
 *
 * 从 WritingBinding.tsx 整段迁出：
 * - matching / matchStageText / retryState / retrying / refreshingRetry state
 * - matchProgressSteps / matchDisplayProgress / updateMatchTargets (useProgressAnimation)
 * - pollMatchStatus 主循环 (600 次 × 3000ms, 偶发错误窗口 5 次)
 * - handleMatch 手动触发
 * - handleRetryMatch 失败重试
 * - refreshRetryState 刷新 retry 面板
 * - autoTriggeredRef / forceRematchHandledRef / mountedRef 三个守卫 ref
 * - 自动触发 effect (projectStatus === 'outlined' && bindings.length === 0 || forceRematch)
 * - projectStatus === 'matching' 恢复轮询 effect
 * - projectStatus === 'failed' refresh retry effect
 *
 * Plan §三 Q6 决议：吸收 retry 链路，避免分两 hook 引入跨 hook callback 割裂。
 * Plan §三 Q7 决议：autoTriggeredRef / forceRematchHandledRef 留本 hook；mountedRef 本 hook 独立维护。
 * ：余额 403 经 onBalanceBlocked 回调交页面层落阻断态；balanceBlocked 期间截断自动触发。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Location } from 'react-router-dom'
import { toast } from 'sonner'
import type { ProgressStep } from '@/hooks/useProgressAnimation'
import { useProgressAnimation } from '@/hooks/useProgressAnimation'
import { parseInsufficientBalanceError } from '@/lib/insufficientBalance'
import { writingService, type WritingBinding as WritingBindingType } from '@/services/writingApi'
import type { WritingRetryState } from '@/services/writingRetryTransforms'
import { getEmptyBindingNotice, getMatchStartErrorMessage } from '../../writingBindingState'
import { mapApiProgressSteps, mapStageToProgressSteps, MATCH_STEPS } from '../../useWritingProgress'
import {
  MATCH_POLL_INTERVAL_MS,
  MATCH_POLL_MAX_ITER,
  MATCH_POLL_TRANSIENT_ERROR_WINDOW,
} from '../constants'

interface UseMatchPollingInputs {
  pid: number
  ready: boolean
  projectStatus: string | null
  initialLoaded: boolean
  bindings: WritingBindingType[]
  triggerMatch: (pid: number, options?: { resetReviewedBindings?: boolean }) => Promise<void>
  loadBindings: (pid: number) => Promise<void>
  location: Location
  navigate: (path: string, opts?: { replace?: boolean; state?: unknown }) => void
  /** 余额阻断中：为 true 时截断自动触发 */
  balanceBlocked?: boolean
  /** 启动请求命中余额不足时回调，由页面层落阻断态 */
  onBalanceBlocked?: (error: unknown) => void
  /** 匹配成功启动或完成后回调，由页面层清除阻断态 */
  onBalanceCleared?: () => void
}

export interface UseMatchPollingResult {
  matching: boolean
  matchStageText: string
  matchProgressSteps: ProgressStep[]
  matchDisplayProgress: number
  retryState: WritingRetryState | null
  retrying: boolean
  refreshingRetry: boolean
  handleMatch: (resetReviewedBindings?: boolean) => Promise<void>
  handleRetryMatch: () => Promise<void>
  refreshRetryState: () => Promise<void>
}

export function useMatchPolling({
  pid,
  ready,
  projectStatus,
  initialLoaded,
  bindings,
  triggerMatch,
  loadBindings,
  location,
  navigate,
  balanceBlocked = false,
  onBalanceBlocked,
  onBalanceCleared,
}: UseMatchPollingInputs): UseMatchPollingResult {
  const [matching, setMatching] = useState(false)
  const [matchStageText, setMatchStageText] = useState('')
  const [retryState, setRetryState] = useState<WritingRetryState | null>(null)
  const [retrying, setRetrying] = useState(false)
  const [refreshingRetry, setRefreshingRetry] = useState(false)

  const mountedRef = useRef(true)
  const autoTriggeredRef = useRef(false)
  const forceRematchHandledRef = useRef(false)

  const locationState = (location.state as {
    forceRematch?: boolean
    fromTitleBatchSave?: boolean
    forceRematchNonce?: number
  } | null) ?? null
  const forceRematchRequested = Boolean(locationState?.forceRematch)

  const matchInitialSteps = useMemo<ProgressStep[]>(
    () => MATCH_STEPS.steps.map((s) => ({
      id: s.id, name: s.name, status: 'pending' as const, progress: 0, targetProgress: 0,
    })),
    [],
  )
  const {
    steps: matchProgressSteps,
    displayProgress: matchDisplayProgress,
    updateTargets: updateMatchTargets,
  } = useProgressAnimation(matchInitialSteps)

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  // pid 切换时重置守卫
  useEffect(() => {
    autoTriggeredRef.current = false
    forceRematchHandledRef.current = false
  }, [pid])

  const pollMatchStatus = useCallback(async () => {
    for (let i = 0; i < MATCH_POLL_MAX_ITER; i++) {
      if (!mountedRef.current) return
      await new Promise((r) => setTimeout(r, MATCH_POLL_INTERVAL_MS))
      try {
        const raw = await writingService.getStatus(pid)
        const status = raw.status
        const currentStage = raw.currentStage || ''
        const currentDetail = raw.currentDetail || ''
        const pct = raw.overallProgress || 0

        const mapped = mapApiProgressSteps(raw.steps, pct, raw.message || '')
          || mapStageToProgressSteps(MATCH_STEPS, currentStage, pct)
        const detailedSteps = mapped.steps.map((step) => (
          step.status === 'processing' && currentDetail
            ? { ...step, detail: currentDetail }
            : step
        ))
        updateMatchTargets(detailedSteps, mapped.overallTarget)
        setMatchStageText(currentDetail || mapped.stageText)

        if (status === 'failed') {
          toast.error(raw.errorMessage || '匹配失败')
          writingService.getRetryState(pid, 'writing_match')
            .then(setRetryState)
            .catch(() => {})
          setMatching(false)
          return
        }
        if (status === 'matched' || status !== 'matching') {
          const done = mapped.steps.map((s) => ({ ...s, status: 'completed' as const, targetProgress: 1 }))
          updateMatchTargets(done, 1)
          setMatchStageText('处理完成')
          await loadBindings(pid).catch(() => {})
          const refreshedBindings = await writingService.getBindings(pid).catch(() => [])
          if (refreshedBindings.length === 0) {
            toast.info(getEmptyBindingNotice())
          } else {
            toast.success('匹配完成')
          }
          // 节点已越过，清除可能残留的余额阻断态
          onBalanceCleared?.()
          setMatching(false)
          return
        }
      } catch {
        if (i > MATCH_POLL_TRANSIENT_ERROR_WINDOW) {
          setMatching(false)
          return
        }
      }
    }
    setMatching(false)
  }, [pid, loadBindings, updateMatchTargets, onBalanceCleared])

  // projectStatus === 'matching' 恢复轮询
  useEffect(() => {
    if (ready && projectStatus === 'matching') {
      setMatching(true)
      pollMatchStatus()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, projectStatus])

  const handleMatch = useCallback(async (resetReviewedBindings = false) => {
    setMatching(true)
    setRetryState(null)
    setMatchStageText('启动中...')
    try {
      await triggerMatch(pid, { resetReviewedBindings })
      // 成功启动即清除余额阻断态
      onBalanceCleared?.()
      pollMatchStatus()
    } catch (error) {
      // 余额不足：交页面层落阻断态，不置失败、不走通用启动失败 toast
      if (parseInsufficientBalanceError(error) != null) {
        onBalanceBlocked?.(error)
        setMatching(false)
        return
      }
      const message = error instanceof Error ? error.message : '触发匹配失败'
      toast.error(getMatchStartErrorMessage(message))
      setMatching(false)
    }
  }, [pid, triggerMatch, pollMatchStatus, onBalanceBlocked, onBalanceCleared])

  const refreshRetryState = useCallback(async () => {
    setRefreshingRetry(true)
    try {
      setRetryState(await writingService.getRetryState(pid, 'writing_match'))
    } finally {
      setRefreshingRetry(false)
    }
  }, [pid])

  // projectStatus === 'failed' refresh retry
  useEffect(() => {
    if (ready && projectStatus === 'failed') {
      refreshRetryState().catch(() => {})
    }
  }, [ready, projectStatus, refreshRetryState])

  const handleRetryMatch = useCallback(async () => {
    setRetrying(true)
    setMatching(true)
    try {
      await writingService.retryWritingNode(pid, 'writing_match')
      // 成功启动即清除余额阻断态
      onBalanceCleared?.()
      setRetryState(null)
      pollMatchStatus()
    } catch (error) {
      // 余额不足：保留 retryState 供 retry 语义展示，不刷新重试面板
      if (parseInsufficientBalanceError(error) != null) {
        onBalanceBlocked?.(error)
        setMatching(false)
        return
      }
      const message = error instanceof Error ? error.message : '匹配重试失败'
      toast.error(message)
      setMatching(false)
      await refreshRetryState().catch(() => {})
    } finally {
      setRetrying(false)
    }
  }, [pid, pollMatchStatus, refreshRetryState, onBalanceBlocked, onBalanceCleared])

  // 自动触发匹配（outlined + 0 bindings || forceRematch）
  useEffect(() => {
    // 余额阻断期间截断自动触发且不置 autoTriggeredRef：
    // 清除阻断后（充值继续/成功启动/刷新前已解除）本 effect 重跑仍可自动或手动触发
    if (balanceBlocked) return
    if (
      initialLoaded &&
      !matching &&
      (
        (
          !autoTriggeredRef.current &&
          projectStatus === 'outlined' &&
          bindings.length === 0
        ) ||
        (
          forceRematchRequested &&
          !forceRematchHandledRef.current
        )
      )
    ) {
      autoTriggeredRef.current = true
      if (forceRematchRequested) {
        forceRematchHandledRef.current = true
        toast.info('检测到素材标题批量更新，正在按新标题重新匹配')
        navigate(location.pathname, { replace: true, state: null })
      }
      handleMatch()
    }
  }, [initialLoaded, matching, projectStatus, bindings.length, handleMatch, forceRematchRequested, navigate, location.pathname, balanceBlocked])

  return {
    matching,
    matchStageText,
    matchProgressSteps,
    matchDisplayProgress,
    retryState,
    retrying,
    refreshingRetry,
    handleMatch,
    handleRetryMatch,
    refreshRetryState,
  }
}
