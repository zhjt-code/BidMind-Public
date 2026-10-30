import React, { useState, useCallback, useRef, useEffect, useMemo } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { isPaymentEnabled } from '@/config/features'
import BalanceGateCard from '@/components/BalanceGateCard'
import { getBalanceGateCopy } from '@/lib/balanceGateCopy'
import { useInsufficientBalanceGate } from '@/hooks/useInsufficientBalanceGate'
import { APP_HOME } from '@/config/routes'
import { ArrowLeft, ArrowRight, Loader2, CheckCircle, AlertCircle } from 'lucide-react'
import ProgressPanel from '@/components/ProgressPanel'
import { useProgressAnimation, type ProgressStep } from '@/hooks/useProgressAnimation'
import { documentService, type DocGateResult } from '@/services/documentApi'
import { writingService } from '@/services/writingApi'
import DocGateWarningDialog from '@/components/DocGateWarningDialog'
import type { WritingRetryState } from '@/services/writingRetryTransforms'
import { useWritingGuard } from './useWritingGuard'
import WritingStepBar from './WritingStepBar'
import {
  buildUploadPlaceholderSteps,
  mapApiProgressSteps,
} from './useWritingProgress'
import {
  clearPersistedWritingSkipVl,
  persistWritingSkipVl,
  readPersistedWritingSkipVl,
} from './writingUploadVlState'
import SkipVlOption from '@/components/upload/SkipVlOption'
import {
  IMAGE_PACE_LABEL,
  TENDER_IMAGE_PACE_HINT,
} from '@/components/upload/documentPace'
import {
  POLL_MAX_NO_ADVANCE_MS,
  classifyPollError,
  computeProgressKey,
  decorateWaitingText,
  isRetryableNetworkError,
} from './writingUploadRetryPolicy'
import {
  getWritingFailedNodeRoute,
  isActionableRetryState,
  WRITING_FAILURE_RETRY_NODES,
} from './writingFailureRecovery'
import {
  UPLOAD_ACCEPT,
  UPLOAD_FORMAT_HINT,
  UPLOAD_FORMAT_TOAST,
  isAllowedUploadFile,
} from '@/lib/uploadAccept'
import {
  isWritingTenderProcessStage,
  isWritingDoneProjectStatus,
  isResumableUploadProjectStatus,
  isAwaitingExtractProjectStatus,
  shouldAutoTriggerExtract,
  isPollUploadExtracted,
  isPollUploadFailed,
  isWritingUploadChooserStage,
  omitSkippedImageRecognitionSteps,
  type UploadStage,
} from './writingUploadHelpers'
import {
  WRITING_LONG_POLL_MAX_ATTEMPTS,
  WRITING_PROGRESS_POLL_INTERVAL_MS,
} from './progressPolling'
import RetryActionPanel from './RetryActionPanel'
import { UPLOAD_FAILURE_FALLBACK, resolveUploadFailureMessage } from './retryActionDisplay.ts'
import RunModeOption from '@/components/upload/RunModeOption'
import { useProjectRunMode } from '@/hooks/useProjectRunMode'
import { isRunModeChooserVisible } from '@/lib/runMode'
import { getPostProcessHint, getWritingExtractDoneHint } from '@/lib/runModeCopy'
import { getWritingProjectRoute } from './writingConstants'

const WritingUploadPage: React.FC = () => {
  const { id: projectId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const fromParam = searchParams.get('from')
  const isManualReturn = fromParam === 'clauses' || fromParam === 'stepbar' || fromParam === 'guard'
  const pid = Number(projectId)
  const { projectStatus, loading: guardLoading } = useWritingGuard(pid, 'created')
  const { runMode, setRunMode, locked: runModeLocked } = useProjectRunMode(pid)
  // 余额门禁: 提取节点被 403 阻断时落卡片态，刷新后从会话快照恢复 
  const gate = useInsufficientBalanceGate({
    projectId: pid,
    nodeKey: 'writing_extract',
    paymentEnabled: isPaymentEnabled,
  })
  const [stage, setStage] = useState<UploadStage>('idle')
  const [errorMsg, setErrorMsg] = useState('')
  const [stageText, setStageText] = useState('')
  const [continuingExtract, setContinuingExtract] = useState(false)
  const [retryState, setRetryState] = useState<WritingRetryState | null>(null)
  const [retrying, setRetrying] = useState(false)
  const [refreshingRetry, setRefreshingRetry] = useState(false)
  const [skipVlForTender, setSkipVlForTender] = useState(true)
  const [isDraggingTender, setIsDraggingTender] = useState(false)
  const [effectiveVlSkipped, setEffectiveVlSkipped] = useState<boolean>(() => readPersistedWritingSkipVl(pid) ?? true)
  // 文档门禁警告弹窗
  const [gatePending, setGatePending] = useState<{
    docId: number
    filename: string
    gateResult: DocGateResult
  } | null>(null)
  const [gateBusy, setGateBusy] = useState(false)

  const initialSteps = useMemo<ProgressStep[]>(
    () => buildUploadPlaceholderSteps(readPersistedWritingSkipVl(pid) ?? true),
    [pid],
  )
  const {
    steps: progressSteps,
    displayProgress,
    updateTargets,
  } = useProgressAnimation(initialSteps)

  const mountedRef = useRef(true)
  const isHandlingRef = useRef(false)
  const lastOverallRef = useRef(0)
  const effectiveVlSkippedRef = useRef(effectiveVlSkipped)
  // 镜像 gate.isBlocked 供长轮询闭包读取，避免闭包过期漏截断自动提取
  const gateBlockedRef = useRef(gate.isBlocked)

  useEffect(() => {
    gateBlockedRef.current = gate.isBlocked
  }, [gate.isBlocked])

  const syncEffectiveVlSkipped = useCallback((value: boolean) => {
    effectiveVlSkippedRef.current = value
    setEffectiveVlSkipped(value)
    persistWritingSkipVl(pid, value)
  }, [pid])

  const resetUploadProgress = useCallback(() => {
    lastOverallRef.current = 0
    updateTargets(buildUploadPlaceholderSteps(effectiveVlSkippedRef.current), 0)
    setStageText('准备处理文档')
  }, [updateTargets])

  const getTenderDocumentId = useCallback(async (): Promise<number | null> => {
    const filesResult = await documentService.getDocuments(pid) as Record<string, unknown>
    const filesData = (filesResult?.data || filesResult) as Record<string, unknown>
    const tender = filesData?.tender as Record<string, unknown> | null
    return (tender?.id as number | undefined) ?? null
  }, [pid])

  const refreshRetryState = useCallback(async () => {
    setRefreshingRetry(true)
    try {
      const raw = await writingService.getStatus(pid).catch(() => null)
      const currentStage = raw?.currentStage || ''
      const useTenderProcess = isWritingTenderProcessStage(currentStage)
        || projectStatus === 'parsing'
        || projectStatus === 'uploading'
      if (useTenderProcess) {
        const docId = await getTenderDocumentId()
        if (docId != null) {
          setRetryState(await writingService.getRetryState(pid, 'writing_tender_process', docId))
          return
        }
      }
      setRetryState(await writingService.getRetryState(pid, 'writing_extract'))
    } finally {
      setRefreshingRetry(false)
    }
  }, [getTenderDocumentId, pid, projectStatus])

  const refreshFailedEntryRetryState = useCallback(async () => {
    const status = await writingService.getStatus(pid).catch(() => null)
    if (status && isWritingDoneProjectStatus(status.status)) {
      setRetryState(null)
      setErrorMsg('')
      setStage('already_done')
      return null
    }
    // status 已带回后端真实失败原因，不再让失败卡片停留在硬编码引导文案
    setErrorMsg(resolveUploadFailureMessage(status?.errorMessage))
    const docId = await getTenderDocumentId().catch(() => null)
    const candidates: Array<{ nodeKey: string; targetId?: number }> = [
      ...(docId != null ? [{ nodeKey: 'writing_tender_process', targetId: docId }] : []),
      ...WRITING_FAILURE_RETRY_NODES.map((nodeKey) => ({ nodeKey })),
    ]
    for (const candidate of candidates) {
      const state = await writingService
        .getRetryState(pid, candidate.nodeKey, candidate.targetId)
        .catch(() => null)
      if (isActionableRetryState(state)) {
        setRetryState(state)
        return state
      }
    }
    return null
  }, [getTenderDocumentId, pid])

  const startExtract = useCallback(async (): Promise<boolean> => {
    try {
      await writingService.triggerExtract(pid)
      if (mountedRef.current) {
        setStage('extracting')
        setStageText('正在提取编写约束')
      }
      return true
    } catch (err) {
      // 余额不足: 不置失败态，保留"处理完成"语义等待充值后继续 
      if (gate.capture(err)) {
        if (mountedRef.current) {
          setErrorMsg('')
          setStage('awaiting_extract')
          setStageText('招标文件处理完成，等待继续提取')
        }
        return false
      }
      if (isRetryableNetworkError(err)) {
        const message = err instanceof Error ? err.message : '约束提取失败'
        console.warn('[writing-upload] triggerExtract POST 瞬态错误, 交给轮询确认:', message)
        if (mountedRef.current) {
          setStage('extracting')
          setStageText(decorateWaitingText('正在提取编写约束', 'post_timeout'))
        }
        return true
      }
      throw err
    }
  }, [gate.capture, pid])

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  useEffect(() => {
    const persisted = readPersistedWritingSkipVl(pid)
    const next = persisted ?? true
    effectiveVlSkippedRef.current = next
    setEffectiveVlSkipped(next)
  }, [pid])

  useEffect(() => {
    if (stage === 'done' || stage === 'already_done') {
      clearPersistedWritingSkipVl(pid)
    }
  }, [pid, stage])

  useEffect(() => {
    if ((stage === 'done' || (stage === 'already_done' && !isManualReturn)) && projectId) {
      const delay = stage === 'done' ? 1200 : 800
      const timer = setTimeout(() => {
        navigate(getWritingProjectRoute(Number(projectId), 'extracted', runMode))
      }, delay)
      return () => clearTimeout(timer)
    }
  }, [stage, projectId, navigate, runMode])

  const applyUploadStatus = useCallback((raw: Awaited<ReturnType<typeof writingService.getStatus>>) => {
    const mapped = mapApiProgressSteps(
      omitSkippedImageRecognitionSteps(raw.steps, effectiveVlSkippedRef.current),
      raw.overallProgress,
      raw.message || '',
    )
    if (!mapped) return
    const safeOverall = Math.max(lastOverallRef.current, mapped.overallTarget)
    lastOverallRef.current = safeOverall
    updateTargets(mapped.steps, safeOverall)
    setStageText(mapped.stageText || raw.message || '')
  }, [updateTargets])

  const pollUploadStatus = useCallback(async (allowAutoStartExtract: boolean): Promise<boolean> => {
    // 方向 C: 轮询以"连续无推进时长"为失败门槛, 单次 30s timeout 不再直接判死
    const maxAttempts = WRITING_LONG_POLL_MAX_ATTEMPTS
    let extractTriggered = false
    let lastAdvanceAt = Date.now()
    let lastProgressKey = ''
    let consecutiveErrors = 0
    for (let i = 0; i < maxAttempts; i++) {
      if (!mountedRef.current) return false
      await new Promise(r => setTimeout(r, WRITING_PROGRESS_POLL_INTERVAL_MS))
      try {
        const raw = await writingService.getStatus(pid)
        applyUploadStatus(raw)

        const progressKey = computeProgressKey(raw)
        if (progressKey !== lastProgressKey) {
          lastProgressKey = progressKey
          lastAdvanceAt = Date.now()
        }
        consecutiveErrors = 0

        if (allowAutoStartExtract
          && !extractTriggered
          && shouldAutoTriggerExtract(raw, gateBlockedRef.current)
        ) {
          extractTriggered = true
          const started = await startExtract()
          if (!started) return false
          continue
        }

        if (isPollUploadExtracted(raw)) {
          setStageText('处理完成')
          return true
        }
        if (isPollUploadFailed(raw)) {
          throw new Error(raw.errorMessage || '约束提取失败')
        }
      } catch (e) {
        consecutiveErrors += 1
        const noAdvanceMs = Date.now() - lastAdvanceAt
        const verdict = classifyPollError(e, noAdvanceMs, POLL_MAX_NO_ADVANCE_MS)
        if (verdict.kind === 'fatal') {
          throw e instanceof Error ? e : new Error(verdict.reason)
        }
        if (consecutiveErrors === 1 || consecutiveErrors % 10 === 0) {
          console.warn(
            `[writing-upload] poll 瞬态错误 (连续${consecutiveErrors}次, 无推进${Math.round(noAdvanceMs / 1000)}s): ${verdict.reason}`,
          )
        }
      }
    }
    throw new Error('处理超时')
  }, [applyUploadStatus, startExtract])

  const resumeUploadPoll = useCallback(async (): Promise<void> => {
    isHandlingRef.current = true
    try {
      // 外围请求（getDocuments / getStatus）遇到瞬态网络/超时错误时不应直接 setStage('error')，
      // 而是进入 processing 态继续让轮询确认后端任务状态；真正的业务错误（未找到文件等）照常上抛。
      let docId: number | undefined
      try {
        const filesResult = await documentService.getDocuments(pid) as Record<string, unknown>
        const filesData = (filesResult?.data || filesResult) as Record<string, unknown>
        const tender = filesData?.tender as Record<string, unknown> | null
        docId = tender?.id as number | undefined
        if (!docId) throw new Error('未找到招标文件')
      } catch (err) {
        if (isRetryableNetworkError(err)) {
          console.warn(
            '[writing-upload] resumeUploadPoll getDocuments 瞬态错误, 交给轮询确认:',
            err instanceof Error ? err.message : String(err),
          )
        } else {
          throw err
        }
      }

      if (!mountedRef.current) return
      resetUploadProgress()
      let currentStatus: Awaited<ReturnType<typeof writingService.getStatus>> | null = null
      try {
        currentStatus = await writingService.getStatus(pid)
      } catch (err) {
        if (!isRetryableNetworkError(err)) throw err
        console.warn(
          '[writing-upload] resumeUploadPoll getStatus 瞬态错误, 进入轮询:',
          err instanceof Error ? err.message : String(err),
        )
      }

      if (currentStatus) {
        applyUploadStatus(currentStatus)
        if (
          currentStatus.status !== 'extracting'
          && (currentStatus.currentStage === 'doc_completed'
            || currentStatus.status === 'parsed')
        ) {
          setStage('awaiting_extract')
          setStageText(currentStatus.currentDetail || '招标文件处理完成，等待继续提取')
          return
        }
      }
      if (projectStatus === 'extracting') {
        setStage('extracting')
      } else {
        setStage('processing')
      }

      const ok = await pollUploadStatus(projectStatus !== 'extracting')
      if (ok && mountedRef.current) {
        setStage('done')
        toast.success('约束提取完成')
      }
    } catch (err) {
      if (!mountedRef.current) return
      setStage('error')
      const msg = err instanceof Error ? err.message : '处理恢复失败'
      setErrorMsg(msg)
      toast.error(msg)
      refreshRetryState().catch(() => {})
    } finally {
      isHandlingRef.current = false
    }
  }, [applyUploadStatus, pid, pollUploadStatus, projectStatus, refreshRetryState, resetUploadProgress])

  const handleContinueExtract = useCallback(async () => {
    if (isHandlingRef.current) return
    isHandlingRef.current = true
    setErrorMsg('')
    setContinuingExtract(true)
    try {
      const started = await startExtract()
      if (!started) return
      // 提取成功启动，清除余额阻断态与会话快照
      gate.clear()
      const ok = await pollUploadStatus(false)
      if (ok && mountedRef.current) {
        setStage('done')
        toast.success('约束提取完成')
      }
    } catch (err) {
      if (!mountedRef.current) return
      setStage('error')
      const msg = err instanceof Error ? err.message : '约束提取失败'
      setErrorMsg(msg)
      toast.error(msg)
      refreshRetryState().catch(() => {})
    } finally {
      setContinuingExtract(false)
      isHandlingRef.current = false
    }
  }, [gate.clear, pollUploadStatus, refreshRetryState, startExtract])

  useEffect(() => {
    if (!guardLoading && projectStatus && !isHandlingRef.current) {
      if (isWritingDoneProjectStatus(projectStatus)) {
        setStage('already_done')
      } else if (projectStatus === 'extracting') {
        setStage('extracting')
        resetUploadProgress()
        pollUploadStatus(false)
          .then((ok) => { if (ok && mountedRef.current) setStage('done') })
          .catch((err: Error) => {
            if (!mountedRef.current) return
            setStage('error')
            setErrorMsg(err.message || '约束提取失败')
            refreshRetryState().catch(() => {})
          })
      } else if (isAwaitingExtractProjectStatus(projectStatus)) {
        setStage('awaiting_extract')
        setStageText('招标文件处理完成，等待继续提取')
      } else if (isResumableUploadProjectStatus(projectStatus)) {
        setStage('processing')
        resumeUploadPoll()
      } else if (projectStatus === 'failed') {
        setStage('error')
        setErrorMsg(UPLOAD_FAILURE_FALLBACK)
        refreshFailedEntryRetryState()
          .then((state) => {
            if (!mountedRef.current || !state?.failedNode) return
            const target = getWritingFailedNodeRoute(projectId || pid, state.failedNode)
            if (!target.endsWith('/writing/upload')) {
              navigate(target, { replace: true })
            }
          })
          .catch(() => {})
      }
    }
  }, [guardLoading, navigate, pid, pollUploadStatus, projectId, projectStatus, refreshFailedEntryRetryState, refreshRetryState, resetUploadProgress, resumeUploadPoll])

  // 上传落库之后的解析与提取流程(门禁放行/用户确认继续后的公共后半程)
  const continueProcessingAfterGate = useCallback(async (docId: number, gateAck: boolean) => {
    isHandlingRef.current = true
    try {
      setStage('processing')
      try {
        await documentService.processDocument(pid, docId, skipVlForTender, gateAck)
      } catch (postErr) {
        // 方向 C: POST 超时/瞬态网络错 ≠ 任务未入队
        // 30s 下 worker 可能已领取 job, 交给 pollUploadStatus 以轮询终态为准
        if (!isRetryableNetworkError(postErr)) {
          throw postErr
        }
        const postMsg = postErr instanceof Error ? postErr.message : String(postErr)
        console.warn('[writing-upload] processDocument POST 瞬态错误, 进入轮询等待后端确认:', postMsg)
        setStageText(decorateWaitingText('正在解析招标文件', 'post_timeout'))
      }

      const uploadOk = await pollUploadStatus(true)
      if (!uploadOk) return

      setStage('done')
      toast.success('约束提取完成')
    } catch (err) {
      if (!mountedRef.current) return
      setStage('error')
      const msg = err instanceof Error ? err.message : '处理失败'
      setErrorMsg(msg)
      toast.error(msg)
      refreshRetryState().catch(() => {})
    } finally {
      isHandlingRef.current = false
    }
  }, [pid, pollUploadStatus, refreshRetryState, skipVlForTender])

  const handleFileSelect = useCallback(async (file: File) => {
    if (!isAllowedUploadFile(file.name)) {
      toast.warning(UPLOAD_FORMAT_TOAST)
      return
    }

    isHandlingRef.current = true
    syncEffectiveVlSkipped(skipVlForTender)
    resetUploadProgress()
    setStage('uploading')
    setErrorMsg('')
      setRetryState(null)

    let docId: number | null = null
    let gateResult: DocGateResult | null = null
    try {
      const uploadResult = await documentService.uploadFile(pid, file, 'tender')
      const resultData = uploadResult?.data
      docId = resultData?.id ?? null
      if (!docId) throw new Error('上传返回无文档ID')
      gateResult = resultData?.gate_result ?? null
    } catch (err) {
      if (!mountedRef.current) return
      setStage('error')
      const msg = err instanceof Error ? err.message : '处理失败'
      setErrorMsg(msg)
      toast.error(msg)
      refreshRetryState().catch(() => {})
      isHandlingRef.current = false
      return
    }

    // 文档门禁: 可疑文档弹窗等待用户决策，解析暂不启动
    if (gateResult && gateResult.verdict !== 'pass') {
      setGatePending({ docId, filename: file.name, gateResult })
      setStageText('等待确认文档')
      isHandlingRef.current = false
      return
    }

    await continueProcessingAfterGate(docId, false)
  }, [continueProcessingAfterGate, pid, refreshRetryState, resetUploadProgress, skipVlForTender, syncEffectiveVlSkipped])

  // 门禁弹窗: 接受警告，继续解析(gate_ack留痕)
  const handleGateContinue = useCallback(async () => {
    if (!gatePending) return
    const pending = gatePending
    setGateBusy(true)
    try {
      setGatePending(null)
      await continueProcessingAfterGate(pending.docId, true)
    } finally {
      setGateBusy(false)
    }
  }, [continueProcessingAfterGate, gatePending])

  // 门禁弹窗: 返回更换文档(删除本次上传的未解析文档，回到选择状态)
  const handleGateReplace = useCallback(async () => {
    if (!gatePending) return
    const pending = gatePending
    setGateBusy(true)
    try {
      await documentService.deleteDocument(pid, pending.docId).catch(() => undefined)
      setGatePending(null)
      setStage('idle')
      toast.info('已撤销本次上传，请重新选择正规的招标文件')
    } finally {
      setGateBusy(false)
    }
  }, [gatePending, pid])

  const handleRetry = useCallback(async () => {
    if (!retryState?.failedNode) return
    setRetrying(true)
    setErrorMsg('')
    try {
      await writingService.retryWritingNode(
        pid,
        retryState.failedNode,
        retryState.targetId ?? undefined,
      )
      setStage(retryState.failedNode === 'writing_extract' ? 'extracting' : 'processing')
      const ok = await pollUploadStatus(retryState.failedNode !== 'writing_extract')
      if (ok && mountedRef.current) {
        setRetryState(null)
        setStage('done')
        toast.success('处理重试完成')
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : '处理重试失败'
      setStage('error')
      setErrorMsg(msg)
      toast.error(msg)
      await refreshRetryState().catch(() => {})
    } finally {
      setRetrying(false)
    }
  }, [pid, pollUploadStatus, refreshRetryState, retryState])

  return (
    <div>
      <WritingStepBar projectId={pid} currentStep={0} projectStatus={projectStatus} />
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => navigate(APP_HOME)}>
            <ArrowLeft className="h-4 w-4 mr-1" />
            返回
          </Button>
          <h1 className="text-xl font-semibold">标书编写 - 上传招标文件</h1>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-3">
          {isRunModeChooserVisible(isWritingUploadChooserStage(stage)) ? (
            <RunModeOption
              value={runMode}
              disabled={runModeLocked}
              projectType="bid_writing"
              onChange={(next) => { void setRunMode(next) }}
            />
          ) : null}
          {(stage === 'already_done' || stage === 'done') && (
            <Button onClick={() => navigate(getWritingProjectRoute(Number(projectId), 'extracted', runMode))}>
              下一步
              <ArrowRight className="h-4 w-4 ml-1" />
            </Button>
          )}
        </div>
      </div>

      <div className="max-w-2xl mx-auto">
      {retryState?.failedNode && (
        <RetryActionPanel
          state={retryState}
          onRetry={handleRetry}
          onRefresh={refreshRetryState}
          retrying={retrying}
          refreshing={refreshingRetry}
        />
      )}
      <div className="bg-white rounded-lg shadow-sm p-6">
        {guardLoading ? (
          <div className="text-center py-8">
            <Loader2 className="h-8 w-8 mx-auto text-primary animate-spin mb-2" />
            <p className="text-sm text-muted-foreground">加载项目状态...</p>
          </div>
        ) : stage === 'already_done' ? (
          <div className="text-center py-4">
            <CheckCircle className="h-12 w-12 mx-auto text-green-500 mb-4" />
            <h2 className="text-lg font-medium mb-2">招标文件已上传并提取完成</h2>
            {!isManualReturn && (
              <p className="text-sm text-muted-foreground">{getWritingExtractDoneHint(runMode)}</p>
            )}
          </div>
        ) : stage === 'idle' ? (
          <div>
            <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
              <h2 className="text-base font-semibold text-gray-800">招标文件 (PDF/Word)</h2>
              <SkipVlOption
                id="skip-vl-writing"
                checked={skipVlForTender}
                onChange={setSkipVlForTender}
                label={IMAGE_PACE_LABEL}
                description={TENDER_IMAGE_PACE_HINT}
              />
            </div>
            <div
              className={`border-2 border-dashed rounded-lg p-6 transition-all duration-150 ${
                isDraggingTender ? 'border-primary bg-blue-50 scale-[1.01]' : 'border-gray-300 hover:border-blue-400'
              }`}
              onDragOver={(e) => {
                e.preventDefault()
                e.dataTransfer.dropEffect = 'copy'
                setIsDraggingTender(true)
              }}
              onDragLeave={(e) => {
                if (!e.currentTarget.contains(e.relatedTarget as Node)) setIsDraggingTender(false)
              }}
              onDrop={(e) => {
                e.preventDefault()
                setIsDraggingTender(false)
                const dropped = e.dataTransfer.files?.[0]
                if (dropped) void handleFileSelect(dropped)
              }}
            >
              {isDraggingTender ? (
                <>
                  <svg className="w-10 h-10 mx-auto mb-2 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                  </svg>
                  <p className="text-primary font-medium text-center">松开鼠标即可上传</p>
                </>
              ) : (
                <div className="text-center">
                  <svg className="w-10 h-10 mx-auto mb-2 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                  </svg>
                  <p className="text-gray-500 mb-3">将招标文件（PDF/Word）拖拽到此处，或</p>
                  <input
                    type="file"
                    accept={UPLOAD_ACCEPT}
                    id="writing-tender-file"
                    className="hidden"
                    onChange={(e) => {
                      const chosen = e.target.files?.[0]
                      e.target.value = ''
                      if (chosen) void handleFileSelect(chosen)
                    }}
                  />
                  <label
                    htmlFor="writing-tender-file"
                    className="inline-block px-4 py-2 rounded-md cursor-pointer bg-primary text-white hover:bg-blue-600"
                  >
                    点击选择文件
                  </label>
                  <p className="text-xs text-gray-400 mt-2">{UPLOAD_FORMAT_HINT}</p>
                </div>
              )}
            </div>
          </div>
        ) : stage === 'done' ? (
          <div className="text-center py-4">
            <CheckCircle className="h-12 w-12 mx-auto text-green-500 mb-4" />
            <h2 className="text-lg font-medium mb-2">提取完成</h2>
            <p className="text-sm text-muted-foreground">{getWritingExtractDoneHint(runMode)}</p>
          </div>
        ) : stage === 'awaiting_extract' ? (
          gate.isBlocked ? (
            <BalanceGateCard
              layout="center"
              semantic="completed"
              copy={getBalanceGateCopy('writing_extract')}
              message={gate.blocked?.message ?? ''}
              paymentEnabled={isPaymentEnabled}
              continueBusy={continuingExtract}
              onRecharge={gate.openRecharge}
              onContinue={handleContinueExtract}
            />
          ) : (
            // 正常节点完成等待继续（非余额阻断），不展示充值入口
            <div className="text-center py-4">
              <CheckCircle className="h-12 w-12 mx-auto text-amber-500 mb-4" />
              <h2 className="text-lg font-medium mb-2">招标文件处理完成</h2>
              <p className="text-sm text-muted-foreground mb-4">
                当前节点已完成，可继续提取编写约束。
              </p>
              <Button onClick={handleContinueExtract} disabled={continuingExtract}>
                {continuingExtract ? '启动中...' : '继续提取'}
                {!continuingExtract && <ArrowRight className="h-4 w-4 ml-1" />}
              </Button>
            </div>
          )
        ) : stage === 'error' ? (
          <div className="text-center py-4">
            <AlertCircle className="h-12 w-12 mx-auto text-red-500 mb-4" />
            <h2 className="text-lg font-medium mb-2 text-red-700">处理失败</h2>
            <p className="text-sm text-red-600 mb-4">{errorMsg}</p>
            <div className="flex items-center justify-center gap-2">
              <Button variant="outline" onClick={refreshFailedEntryRetryState} disabled={refreshingRetry}>
                刷新恢复状态
              </Button>
              <Button onClick={() => setStage('idle')}>重新上传</Button>
            </div>
          </div>
        ) : (
          <div className="py-2 px-2">
            <ProgressPanel
              steps={progressSteps}
              displayProgress={displayProgress}
              stageText={stage === 'uploading' ? '正在上传...' : stageText}
              isRunning
              isCompleted={progressSteps.every((s) => s.status === 'completed')}
              isInitialLoading={false}
              error={null}
              accentColor="teal"
              compact
            />
          </div>
        )}
      </div>
      {stage === 'idle' ? (
        <p className="mt-3 text-xs text-muted-foreground">{getPostProcessHint(runMode, 'bid_writing')}</p>
      ) : null}
      </div>

      <DocGateWarningDialog
        open={gatePending != null}
        items={gatePending ? [{ filename: gatePending.filename, gateResult: gatePending.gateResult }] : []}
        busy={gateBusy}
        onContinue={handleGateContinue}
        onReplace={handleGateReplace}
      />
    </div>
  )
}

export default WritingUploadPage
