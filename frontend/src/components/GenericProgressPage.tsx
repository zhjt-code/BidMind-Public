import React, { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import { useProgressAnimation, ProgressStep } from '@/hooks/useProgressAnimation'
import ProgressPanel from '@/components/ProgressPanel'
import BalanceGateCard from '@/components/BalanceGateCard'
import {
  getProgressPageError,
  isProgressCompleted,
  nextProgressSessionStatus,
} from '@/components/progressPageState'
import { useInsufficientBalanceGate } from '@/hooks/useInsufficientBalanceGate'
import { getBalanceGateCopy, type BalanceGateNodeKey } from '@/lib/balanceGateCopy'
import { isPaymentEnabled } from '@/config/features'
import {
  normalizeOverallStatus,
  normalizeStepStatus,
} from '@/services/progressStatusTransforms'
import RetryActionPanel from '@/pages/writing/RetryActionPanel'
import type { WritingRetryState } from '@/services/writingRetryTransforms'
import { formatFailureMessage } from '@/lib/formatFailureMessage'

export interface CommonStatus {
  overallStatus: string
  overallProgress: number
  steps: Array<{ label?: string; status: string; progress: number; detail?: string }>
  isRecovering?: boolean
  message?: string
  errorMessage?: string
  comparedCount?: number
  failedPairs?: number
  retryable?: boolean
  failedNode?: string | null
  targetId?: number | null
  failedStage?: string | null
  retryActionLabel?: string
  preservedOutputs?: string[]
  resetScope?: string[]
  lastAttempt?: number
  retryRunning?: boolean
  lastError?: string | null
}

export interface ProgressPageConfig {
  title: string
  initialSteps: ProgressStep[]
  queryKey: string
  fetchStatus: (projectId: number) => Promise<CommonStatus>
  accentColor: 'blue' | 'iris' | 'teal'
  backPath: (pid: string) => string
  nextPath: (pid: string) => string
  completedMessage: string
  resultButtonText: string
  /** 标题下灰字：下一站或耗时预期，不复述进度步骤 */
  description: string
  textMap: { completed: string; failed: string; running: string }
  forceAllCompleted?: boolean
/**
 * 完成态自动跳转结果页的延时（毫秒）。
 * - 设置为正数：completed 状态下经过该延时自动 navigate(nextPath)
 * - 不设置或 <=0：保持现有"仅显示按钮，需手动点击"行为（向后兼容）
 * - 失败态（error 不为空）下不会触发自动跳转，避免打断用户排查
 */
  autoNavigateOnCompleteMs?: number
  /** 重试节点的余额门禁文案 key；缺省时快照 key 退化为 queryKey 派生值 */
  balanceGateNodeKey?: BalanceGateNodeKey
  retryAction?: (
    projectId: number,
    nodeKey?: string | null,
    targetId?: number | null,
  ) => Promise<WritingRetryState>
}

function deriveSteps(
  status: CommonStatus,
  initialSteps: ProgressStep[],
  config: ProgressPageConfig,
): {
  steps: ProgressStep[]
  overallTarget: number
  stageText: string
  isRunning: boolean
  isRecovering: boolean
  recoveryMessage: string
} {
  const apiSteps = status.steps
  const overallStatus = normalizeOverallStatus(status.overallStatus)

  const steps: ProgressStep[] = initialSteps.map((init, i) => {
    const api = apiSteps[i]
    if (!api) return init
    return {
      id: init.id,
      name: api.label || init.name,
      status: normalizeStepStatus(api.status),
      progress: 0,
      targetProgress: api.progress / 100,
      detail: api.detail ?? undefined,
    }
  })

  const overallTarget = status.overallProgress / 100
  const isRunning = overallStatus === 'running'
  const isRecovering = status.isRecovering || false
  const recoveryMessage = status.message || ''

  if (config.forceAllCompleted && overallStatus === 'completed') {
    steps.forEach((s, i) => {
      steps[i] = { ...s, status: 'completed', targetProgress: 1 }
    })
  }

  let stageText = ''
  const running = steps.find((s) => s.status === 'processing')
  if (overallStatus === 'completed') {
    stageText = config.textMap.completed
  } else if (overallStatus === 'failed') {
    stageText = config.textMap.failed
  } else if (isRunning) {
    if (isRecovering) {
      stageText = recoveryMessage || '系统恢复中...'
    } else if (running) {
      const idx = steps.indexOf(running) + 1
      stageText = `步骤${idx} ${running.name}`
      if (running.detail) stageText += ` · ${running.detail}`
    } else {
      stageText = config.textMap.running
    }
  }

  return { steps, overallTarget, stageText, isRunning, isRecovering, recoveryMessage }
}

const GenericProgressPage: React.FC<{ config: ProgressPageConfig }> = ({ config }) => {
  const { id: projectId } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const { steps, displayProgress, updateTargets } = useProgressAnimation(config.initialSteps)
  const [stageText, setStageText] = useState('')
  const [isRunning, setIsRunning] = useState(false)
  const [isCompleted, setIsCompleted] = useState(false)
  const [isInitialLoading, setIsInitialLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [isRecovering, setIsRecovering] = useState(false)
  const [recoveryMessage, setRecoveryMessage] = useState('')
  const [retrying, setRetrying] = useState(false)

  // 重试动作的余额门禁：余额 403 不进红色失败路径，进度成果保留可见
  const balanceGate = useInsufficientBalanceGate({
    projectId: projectId ? parseInt(projectId) : 0,
    nodeKey: config.balanceGateNodeKey ?? `${config.queryKey}-retry`,
    paymentEnabled: isPaymentEnabled,
    toastPrefix: '重试未启动',
  })

  // 记录本页会话是否目睹过 running：
  // 首次进入即 completed（从结果页返回）：禁用自动跳转，避免死循环
  // 缓存/旧终态之后又变成 running（重试或粘滞 completed）：改记 running，真正完成后再引导
  const initialStatusRef = useRef<string | null>(null)

  const { data: statusData, error: queryError, refetch } = useQuery({
    queryKey: [config.queryKey, projectId],
    queryFn: () => config.fetchStatus(parseInt(projectId!)),
    enabled: !!projectId,
    refetchInterval: (query) => {
      const s = normalizeOverallStatus(query.state.data?.overallStatus)
      if (s === 'completed' || s === 'failed') return false
      return 1000
    },
  })

  const canViewResult = isCompleted
    || (!!error && (((statusData?.comparedCount ?? 0) > 0) || ((statusData?.failedPairs ?? 0) > 0)))
  const showNextButton = canViewResult && !isInitialLoading
  const showRetryHint = Boolean(
    !isInitialLoading
    && error
    && !config.retryAction
    && (statusData?.retryable || statusData?.failedNode || statusData?.lastError),
  )
  const retryPanelState: WritingRetryState | null = (
    statusData?.failedNode || statusData?.retryable || statusData?.retryRunning
  )
    ? {
        retryable: Boolean(statusData.retryable),
        failedNode: statusData.failedNode ?? null,
        targetId: statusData.targetId ?? null,
        failedStage: statusData.failedStage ?? null,
        retryActionLabel: statusData.retryActionLabel || '从失败节点重试',
        preservedOutputs: statusData.preservedOutputs || [],
        resetScope: statusData.resetScope || [],
        lastAttempt: statusData.lastAttempt || 0,
        retryRunning: Boolean(statusData.retryRunning),
        lastError: statusData.lastError ?? null,
      }
    : null

  const handleRetry = async () => {
    if (!projectId || !retryPanelState || !config.retryAction) return
    setRetrying(true)
    try {
      await config.retryAction(
        parseInt(projectId),
        retryPanelState.failedNode,
        retryPanelState.targetId,
      )
      balanceGate.clear()
      await refetch()
    } catch (retryError) {
      // 余额不足走门禁卡，其余错误 toast 提示（原实现无 catch，异常会成为未处理拒绝）
      if (!balanceGate.capture(retryError)) {
        toast.error(retryError instanceof Error ? retryError.message : '重试启动失败')
      }
    } finally {
      setRetrying(false)
    }
  }

  useEffect(() => {
    if (!statusData) return
    const derived = deriveSteps(statusData, config.initialSteps, config)
    const overallStatus = normalizeOverallStatus(statusData.overallStatus)

    initialStatusRef.current = nextProgressSessionStatus(
      initialStatusRef.current,
      overallStatus,
    )

    updateTargets(derived.steps, derived.overallTarget)
    setIsRunning(derived.isRunning)
    setIsCompleted(isProgressCompleted(statusData.overallStatus))
    setStageText(derived.stageText)
    setIsRecovering(derived.isRecovering)
    setRecoveryMessage(derived.recoveryMessage)
    setIsInitialLoading(false)
    setError(getProgressPageError({
      overallStatus,
      errorMessage: statusData.errorMessage ?? null,
      failedText: config.textMap.failed,
    }))
    if (overallStatus === 'failed') {
      setIsRunning(false)
    }
  }, [statusData, updateTargets, config])

  useEffect(() => {
    if (queryError) {
      setError(getProgressPageError({
        overallStatus: normalizeOverallStatus(statusData?.overallStatus),
        errorMessage: statusData?.errorMessage ?? null,
        queryError,
        failedText: config.textMap.failed,
      }))
      setIsInitialLoading(false)
    }
  }, [queryError, statusData, config.textMap.failed])

  // 完成态自动跳转：仅在 autoNavigateOnCompleteMs > 0 且无错误时触发
  // 失败态保留在进度页便于用户查看错误并选择重试
  // 首次进入即 completed 视为"事后回看"，不触发自动跳转，避免从结果页返回时被甩回
  useEffect(() => {
    const delay = config.autoNavigateOnCompleteMs
    if (!isCompleted || error || !projectId || !delay || delay <= 0) return
    if (initialStatusRef.current === 'completed') return
    const timer = setTimeout(() => {
      navigate(config.nextPath(projectId))
    }, delay)
    return () => clearTimeout(timer)
  }, [isCompleted, error, projectId, navigate, config])

  return (
    <div>
      <div className="flex justify-between items-start mb-6">
        <div className="flex items-start">
          <button
            onClick={() => navigate(config.backPath(projectId || ''))}
            className="mr-4 p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-md transition-colors"
            title="返回"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
          </button>
          <div>
            <h1 className="text-2xl font-bold text-gray-900">{config.title}</h1>
            <p className="text-gray-500 mt-1">{config.description}</p>
          </div>
        </div>
        {showNextButton && (
          <button
            onClick={() => navigate(config.nextPath(projectId || ''))}
            className="px-4 py-2 rounded-md text-white bg-green-500 hover:bg-green-600 flex items-center"
          >
            下一步
            <svg className="w-4 h-4 ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14 5l7 7m0 0l-7 7m7-7H3" />
            </svg>
          </button>
        )}
      </div>

      <div className="max-w-2xl mx-auto">
      <ProgressPanel
        steps={steps}
        displayProgress={displayProgress}
        stageText={stageText}
        isRunning={isRunning}
        isCompleted={isCompleted}
        canViewResult={canViewResult}
        isInitialLoading={isInitialLoading}
        error={error}
        accentColor={config.accentColor}
        completedMessage={config.completedMessage}
        onViewResult={() => navigate(config.nextPath(projectId || ''))}
        resultButtonText={config.resultButtonText}
        isRecovering={isRecovering}
        recoveryMessage={recoveryMessage}
      />

      {balanceGate.blocked && (
        <div className="mt-4">
          <BalanceGateCard
            layout="inline"
            semantic="retry"
            copy={getBalanceGateCopy(config.balanceGateNodeKey ?? 'review_retry')}
            message={balanceGate.blocked.message}
            paymentEnabled={isPaymentEnabled}
            continueBusy={retrying}
            onRecharge={balanceGate.openRecharge}
            onContinue={() => {
              balanceGate.clear()
              void handleRetry()
            }}
          />
        </div>
      )}

      {config.retryAction && retryPanelState && (
        <RetryActionPanel
          state={retryPanelState}
          onRetry={handleRetry}
          onRefresh={() => { void refetch() }}
          retrying={retrying}
        />
      )}

      {showRetryHint && (
        <div className="mt-4 p-4 bg-red-50 rounded-lg border border-red-200">
          <p className="text-sm font-medium text-red-800">
            {statusData?.failedNode ? `失败节点: ${statusData.failedNode}` : '任务已进入失败态'}
          </p>
          {(statusData?.failedStage || statusData?.lastError) && (
            <p className="text-sm text-red-700 mt-1">
              {statusData.failedStage ? `阶段: ${statusData.failedStage}` : ''}
              {statusData.failedStage && statusData.lastError ? ' · ' : ''}
              {statusData.lastError ? formatFailureMessage(statusData.lastError) : ''}
            </p>
          )}
          <p className="text-sm text-red-700 mt-2">
            {statusData?.retryable
              ? (statusData.retryActionLabel || '可从失败节点重试')
              : '请刷新状态或返回上一步重新发起任务'}
          </p>
        </div>
      )}
      </div>
    </div>
  )
}

export default GenericProgressPage
