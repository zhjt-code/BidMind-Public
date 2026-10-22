/**
 * 招标要求提取任务的自启动 + 轮询 hook。
 *
 * 从原 RequirementReview.tsx::loadRequirements 内嵌段抽出 (~270 行):
 * - 检查当前后端任务状态 (`getExtractStatus`)
 * - idle 时主动触发 `extractRequirements` 启动，failed 必须显式上报给用户
 * - 最多 3600 次轮询 (约 120 分钟,interval 2s), 与后端 WORKER_EXTRACT_JOB_TIMEOUT=7200s 对齐
 * - 双路 stage 映射: mapStandardExtractionSteps 优先,失败回退到 mapExtractionToSteps
 * - 完成态: 拉取最终 requirements 并通过 onRequirementsLoaded 回填
 * - 失败 / 超时: 通过 onError 上报
 * - : 以 pollEpoch + run_id/revision 拒绝旧轮询与旧快照回写
 *
 * 关键不变量 (破坏将导致行为回归):
 * - extractingRef / cancelledRef 必须用 useRef 保活 (useState 会触发 re-render,
 * 破坏轮询节拍 + 防重复入口判定),沿用 useWritingMaterialsPolling 同款模式
 * - cancelledRef 在 unmount 时自动置 true,任意进行中轮询的下一轮 while 检查会退出
 * - extraction_warnings 仅在 status === 'completed' 时收集,不在 processing 阶段堆积
 * - 拉取异常 / status.stage 缺失 不算"无推进",仅 sleep 2s 后重试 attempts++
 * - finally 仅在本 epoch 仍为当前时清理 extractingRef，避免重提取打断旧循环时误关新循环
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { requirementService } from '@/services/requirementApi'
import { parseInsufficientBalanceError } from '@/lib/insufficientBalance'
import type { ProgressStep } from '@/hooks/useProgressAnimation'
import type { Requirement } from '../../requirementReviewTransforms'
import { transformRequirement } from '../../requirementReviewTransforms'
import { mapExtractionToSteps, mapStandardExtractionSteps } from '../helpers'
import {
  advanceExtractionSnapshotIdentity,
  readExtractionSnapshotIdentity,
  shouldAcceptExtractionSnapshot,
  type ExtractionSnapshotIdentity,
} from './extractionSnapshotIdentity'

export interface UseRequirementExtractionPollingOptions {
  projectId: string | undefined
  /** 透传给 useProgressAnimation 的进度推进入口 */
  updateExtractionTargets: (steps: ProgressStep[], overallTarget: number) => void
  /** 提取完成且 requirements 非空时的回填回调 */
  onRequirementsLoaded: (items: Requirement[]) => void
  /** 提取完成时同步回填评分项满分校验报告 */
  onScoringTotalsReportLoaded?: (report: unknown) => void
  /** 提取完成时同步回填评分账本对账报告（P5-1：raw 交给页面侧 transform， 双路回填之一） */
  onScoringLedgerReportLoaded?: (report: unknown) => void
  /** 提取完成时同步回填评分三态权威结论（verdict 单源：raw 交给页面侧 transform） */
  onScoringVerdictLoaded?: (verdict: unknown) => void
  /** 失败 / 完成但 requirements 为空 / 超时 等出错路径 */
  onError: (message: string) => void
  /** 余额不足错误旁路：命中时不再走 onError 红色文案 */
  onBalanceBlocked?: (error: unknown) => void
  /** 返回 true 时不自动触发 extractRequirements（截断余额阻断态下刷新后的自动重入） */
  shouldBlockAutoStart?: () => boolean
}

export interface RequirementExtractionPollingController {
  /** 是否正在执行轮询(主页面切换视图时使用) */
  extracting: boolean
  /** 当前阶段文案(传递给 ProgressPanel.stageText) */
  extractionStageText: string
  /** 首次轮询命中前的 initial loading 标志(传递给 ProgressPanel.isInitialLoading) */
  extractionInitial: boolean
  /** 提取完成时后端返回的警告列表 */
  extractionWarnings: string[]
/**
 * 启动一次提取轮询。
 * 若已有轮询在跑则跳过(等价于原 extractingRef.current 防重复入口)。
 */
  startIfNeeded: () => Promise<void>
/**
 * 为新的提取代际强制启动轮询（重新提取）。
 * 递增 pollEpoch，使旧循环的副作用全部失效。
 */
  startForRun: (identity?: ExtractionSnapshotIdentity | null) => Promise<void>
}

export function useRequirementExtractionPolling(
  options: UseRequirementExtractionPollingOptions,
): RequirementExtractionPollingController {
  const {
    projectId,
    updateExtractionTargets,
    onRequirementsLoaded,
    onScoringTotalsReportLoaded,
    onScoringLedgerReportLoaded,
    onScoringVerdictLoaded,
    onError,
    onBalanceBlocked,
    shouldBlockAutoStart,
  } = options

  const [extracting, setExtracting] = useState(false)
  const [extractionStageText, setExtractionStageText] = useState('')
  const [extractionInitial, setExtractionInitial] = useState(true)
  const [extractionWarnings, setExtractionWarnings] = useState<string[]>([])

  const extractingRef = useRef(false)
  const cancelledRef = useRef(false)
  const pollEpochRef = useRef(0)
  const activeIdentityRef = useRef<ExtractionSnapshotIdentity | null>(null)
  const expectedRunIdRef = useRef<string | null>(null)
  const staleRunIdsRef = useRef<Set<string>>(new Set())

  // 用 ref 保存 props 回调,避免每次 props 变化都重建 startIfNeeded
  const updateExtractionTargetsRef = useRef(updateExtractionTargets)
  const onRequirementsLoadedRef = useRef(onRequirementsLoaded)
  const onScoringTotalsReportLoadedRef = useRef(onScoringTotalsReportLoaded)
  const onScoringLedgerReportLoadedRef = useRef(onScoringLedgerReportLoaded)
  const onScoringVerdictLoadedRef = useRef(onScoringVerdictLoaded)
  const onErrorRef = useRef(onError)
  const onBalanceBlockedRef = useRef(onBalanceBlocked)
  const shouldBlockAutoStartRef = useRef(shouldBlockAutoStart)
  useEffect(() => {
    updateExtractionTargetsRef.current = updateExtractionTargets
    onRequirementsLoadedRef.current = onRequirementsLoaded
    onScoringTotalsReportLoadedRef.current = onScoringTotalsReportLoaded
    onScoringLedgerReportLoadedRef.current = onScoringLedgerReportLoaded
    onScoringVerdictLoadedRef.current = onScoringVerdictLoaded
    onErrorRef.current = onError
    onBalanceBlockedRef.current = onBalanceBlocked
    shouldBlockAutoStartRef.current = shouldBlockAutoStart
  }, [
    updateExtractionTargets,
    onRequirementsLoaded,
    onScoringTotalsReportLoaded,
    onScoringLedgerReportLoaded,
    onScoringVerdictLoaded,
    onError,
    onBalanceBlocked,
    shouldBlockAutoStart,
  ])

  // unmount 时自动取消正在进行的轮询(原 useEffect cleanup 行为)
  useEffect(() => {
    cancelledRef.current = false
    return () => {
      cancelledRef.current = true
      pollEpochRef.current += 1
    }
  }, [])

  const isCurrentEpoch = useCallback((epoch: number) => {
    return !cancelledRef.current && pollEpochRef.current === epoch
  }, [])

  const canConsumeSnapshot = useCallback(
    (incoming: ExtractionSnapshotIdentity | null): boolean => {
      if (incoming && staleRunIdsRef.current.has(incoming.runId)) {
        return false
      }
      const expected = expectedRunIdRef.current
      if (expected) {
        if (!incoming) return false
        if (incoming.runId !== expected) return false
      }
      return shouldAcceptExtractionSnapshot(activeIdentityRef.current, incoming)
    },
    [],
  )

/**
 * 失败上报统一入口：余额不足优先旁路给 onBalanceBlocked（保留成果、不进红色错误面板），
 * 其余保持原 onError 文案。
 */
  const reportExtractionFailure = useCallback((rawMessage: string | undefined) => {
    const handler = onBalanceBlockedRef.current
    if (handler) {
      const failure = new Error(rawMessage ?? '')
      if (parseInsufficientBalanceError(failure)) {
        handler(failure)
        return
      }
    }
    onErrorRef.current(`招标要求提取失败: ${rawMessage}`)
  }, [])

  const markSnapshotAccepted = useCallback(
    (incoming: ExtractionSnapshotIdentity | null) => {
      activeIdentityRef.current = advanceExtractionSnapshotIdentity(
        activeIdentityRef.current,
        incoming,
      )
      if (incoming && expectedRunIdRef.current === incoming.runId) {
        expectedRunIdRef.current = null
      }
    },
    [],
  )

  const runPollingLoop = useCallback(
    async (epoch: number) => {
      if (!projectId) return

      extractingRef.current = true
      setExtracting(true)

      try {
        let status = await requirementService.getExtractStatus(parseInt(projectId))
        if (!isCurrentEpoch(epoch)) return

        if (status.status === 'idle') {
          // 余额阻断态下禁止自动重入扣费启动，恢复只能由门禁卡"继续"按钮显式发起
          if (shouldBlockAutoStartRef.current?.()) {
            return
          }
          const started = await requirementService.extractRequirements(
            parseInt(projectId),
          )
          if (!isCurrentEpoch(epoch)) return
          const startedIdentity = readExtractionSnapshotIdentity(started)
          if (startedIdentity) {
            expectedRunIdRef.current = startedIdentity.runId
          }
          await new Promise((resolve) => setTimeout(resolve, 500))
          if (!isCurrentEpoch(epoch)) return
        } else if (status.status === 'failed') {
          if (isCurrentEpoch(epoch)) {
            reportExtractionFailure(status.message)
          }
          return
        }

        // interval 2s × 3600 = 7200s，与 WORKER_EXTRACT_JOB_TIMEOUT 对齐
        const maxAttempts = 3600
        let attempts = 0

        while (attempts < maxAttempts && isCurrentEpoch(epoch)) {
          try {
            status = await requirementService.getExtractStatus(parseInt(projectId))
          } catch {
            attempts++
            await new Promise((resolve) => setTimeout(resolve, 2000))
            continue
          }

          if (!isCurrentEpoch(epoch)) return

          if (!status || !status.stage) {
            attempts++
            await new Promise((resolve) => setTimeout(resolve, 2000))
            continue
          }

          const incoming = readExtractionSnapshotIdentity(status)
          if (!canConsumeSnapshot(incoming)) {
            await new Promise((resolve) => setTimeout(resolve, 2000))
            attempts++
            continue
          }
          markSnapshotAccepted(incoming)

          const mapped =
            mapStandardExtractionSteps(
              status.steps,
              status.overall_progress || status.progress,
              status.message || '',
            ) ||
            mapExtractionToSteps(
              status.stage || 'extracting',
              status.progress,
              status.message || '',
            )
          updateExtractionTargetsRef.current(mapped.steps, mapped.overall)
          const nextStageText =
            'stageText' in mapped ? mapped.stageText : status.message || ''
          setExtractionStageText(String(nextStageText || ''))
          setExtractionInitial(false)

          if (status.status === 'completed') {
            const done = mapExtractionToSteps('completed', 100, '提取完成')
            updateExtractionTargetsRef.current(done.steps, done.overall)

            if (status.extraction_warnings?.length) {
              setExtractionWarnings(status.extraction_warnings)
            }

            const identityAtRequest = activeIdentityRef.current
            const checkResponse = await requirementService.getRequirements(
              parseInt(projectId),
            )
            if (!isCurrentEpoch(epoch)) return
            if (
              identityAtRequest
              && activeIdentityRef.current
              && (activeIdentityRef.current.runId !== identityAtRequest.runId
                || activeIdentityRef.current.revision < identityAtRequest.revision)
            ) {
              return
            }
            if (expectedRunIdRef.current) {
              return
            }

            const checkItems =
              (checkResponse as { items?: Record<string, unknown>[] }).items || []
            onScoringTotalsReportLoadedRef.current?.(
              (checkResponse as { scoring_totals_report?: unknown })
                .scoring_totals_report,
            )
            onScoringLedgerReportLoadedRef.current?.(
              (checkResponse as { scoring_ledger_report?: unknown })
                .scoring_ledger_report,
            )
            onScoringVerdictLoadedRef.current?.(
              (checkResponse as { scoring_verdict?: unknown }).scoring_verdict,
            )

            if (checkItems.length > 0) {
              onRequirementsLoadedRef.current(checkItems.map(transformRequirement))
            } else {
              onErrorRef.current('提取完成但未获取到结果，请刷新页面')
            }
            break
          }

          if (status.status === 'failed') {
            reportExtractionFailure(status.message)
            break
          }

          await new Promise((resolve) => setTimeout(resolve, 2000))
          attempts++
        }

        if (
          isCurrentEpoch(epoch)
          && attempts >= maxAttempts
          && status?.status !== 'completed'
        ) {
          onErrorRef.current('提取耗时较长，请稍后刷新页面查看结果')
        }
      } catch (extractError) {
        if (isCurrentEpoch(epoch)) {
          const balanceHandler = onBalanceBlockedRef.current
          if (balanceHandler && parseInsufficientBalanceError(extractError)) {
            balanceHandler(extractError)
          } else {
            onErrorRef.current(
              `招标要求提取失败: ${
                extractError instanceof Error ? extractError.message : '未知错误'
              }`,
            )
          }
        }
      } finally {
        if (isCurrentEpoch(epoch)) {
          extractingRef.current = false
          setExtracting(false)
        }
      }
    },
    [projectId, isCurrentEpoch, canConsumeSnapshot, markSnapshotAccepted, reportExtractionFailure],
  )

  const startIfNeeded = useCallback(async () => {
    if (!projectId) return
    if (extractingRef.current) {
      // eslint-disable-next-line no-console
      console.log('提取正在进行中，跳过重复请求')
      return
    }
    const epoch = ++pollEpochRef.current
    await runPollingLoop(epoch)
  }, [projectId, runPollingLoop])

  const startForRun = useCallback(
    async (identity?: ExtractionSnapshotIdentity | null) => {
      if (!projectId) return
      const previous = activeIdentityRef.current
      if (previous?.runId) {
        staleRunIdsRef.current.add(previous.runId)
      }
      activeIdentityRef.current = null
      expectedRunIdRef.current = identity?.runId ?? null
      setExtractionWarnings([])
      setExtractionInitial(true)
      const epoch = ++pollEpochRef.current
      await runPollingLoop(epoch)
    },
    [projectId, runPollingLoop],
  )

  return {
    extracting,
    extractionStageText,
    extractionInitial,
    extractionWarnings,
    startIfNeeded,
    startForRun,
  }
}
