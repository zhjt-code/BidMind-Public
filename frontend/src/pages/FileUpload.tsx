import React, { useState, useCallback, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { documentService, type DocGateResult } from '@/services/documentApi'
import { reviewService } from '@/services/reviewApi'
import { projectService } from '@/services/projectApi'
import type { WritingRetryState } from '@/services/writingRetryTransforms'
import { Modal } from '@/components'
import DocGateWarningDialog, { type GateWarningItem } from '@/components/DocGateWarningDialog'
import { useLimitedDisplayDialog } from '@/hooks/useLimitedDisplayDialog'
import { useMetricProbe } from '@/hooks/useMetricProbe'
import { getPollingInterval } from '@/lib/formatters'
import {
  BID_ENVELOPE_PROFILES,
  DEFAULT_ENVELOPE_MODE,
  envelopeIsComplete,
  inferEnvelopeModeFromDocuments,
} from '@/lib/bidEnvelope'
import { useDocumentProcessPolling } from './upload/hooks/useDocumentProcessPolling'
import { APP_HOME } from '@/config/routes'
import {
  persistFileUploadSkipVl,
  readPersistedFileUploadSkipVl,
} from './fileUploadParts/skipVlState'
import {
  bidReviewPostProcessPath,
  deriveBidReviewUploadPageState,
  isBidReviewUploadChooserMode,
} from './fileUploadParts/uploadPageState'
import RunModeOption from '@/components/upload/RunModeOption'
import { useProjectRunMode } from '@/hooks/useProjectRunMode'
import { isRunModeChooserVisible } from '@/lib/runMode'
import { getPostProcessHint } from '@/lib/runModeCopy'
import { toExistingDocumentFailure } from './fileUploadParts/existingDocFailure'
import { canSubmitReplaceUpload } from './fileUploadParts/replaceUploadGate'
import { runReplaceFailedUpload } from './fileUploadParts/runReplaceFailedUpload'
import { resolveDocumentProcessFailureMessage } from '@/lib/formatFailureMessage'
import UploadSlotArea, {
  type EnvelopeUploadedFile,
  type ExistingDocument,
} from './fileUploadParts/UploadSlotArea'
import UploadRecoveryPanel from './fileUploadParts/UploadRecoveryPanel'
import BidReviewUploadView from './fileUploadParts/BidReviewUploadView'
import {
  useBidEnvelopeSlots,
  type UploadSlotDescriptor,
} from './fileUploadParts/useBidEnvelopeSlots'
import RetryActionPanel from '@/pages/writing/RetryActionPanel'

// ==================== 类型 ====================

/** 文档列表 API 返回的已有文档(含投标信封卷角色) */
type ExistingDocumentPayload = ExistingDocument & { bid_part?: string | null }

/** 文档列表 API 响应(bids 为全部投标文档, bid 为兼容首份) */
type DocumentsPayload = {
  data?: {
    tender?: ExistingDocumentPayload
    bid?: ExistingDocumentPayload
    bids?: ExistingDocumentPayload[]
  }
}

// 门禁警告待决状态: 全部文件均已上传，等待用户选择"继续/更换"
interface GatePendingState {
  items: GateWarningItem[]
  /** 本次上传落库的全部文档(按槽位) */
  documents: Array<{ slotId: string; documentId: number }>
  flaggedSlotIds: string[]
}

const PROCESS_TIP_STORAGE_KEY = 'bidmind_processing_tip_display_count'
const PROCESS_TIP_MAX_DISPLAYS = 2

/** 全部档案的投标槽位 id(切换模式时清理另一模式残留选择) */
const ALL_BID_SLOT_IDS = BID_ENVELOPE_PROFILES.flatMap((profile) =>
  profile.slots.map((slot) => slot.id),
)

// ==================== 主组件 ====================

const FileUploadPage: React.FC = () => {
  useMetricProbe({ page: 'FileUpload' })
  const { id: projectId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { runMode, setRunMode, locked: runModeLocked } = useProjectRunMode(projectId)

  // 投标信封槽位容器: 招标 + 按模式的投标槽位(整本 / 商务标+技术标)
  const {
    mode,
    setMode,
    bidSlots,
    allSlots,
    slotState,
    patchSlot,
    updateSlotFile,
    updateSlotExisting,
    resetSlots,
  } = useBidEnvelopeSlots()

  const [isUploading, setIsUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [hasExistingDocs, setHasExistingDocs] = useState(false)
  /** : 单侧更换槽位 id；不清空其余槽位，避免误进半成品态 */
  const [replacingSlot, setReplacingSlot] = useState<string | null>(null)
  const [retryState, setRetryState] = useState<WritingRetryState | null>(null)
  const [retrying, setRetrying] = useState(false)
  const [isResettingUpload, setIsResettingUpload] = useState(false)
  const [refreshingRetry, setRefreshingRetry] = useState(false)
  // 文档门禁警告弹窗
  const [gatePending, setGatePending] = useState<GatePendingState | null>(null)
  const [gateBusy, setGateBusy] = useState(false)
  const [projectStatus, setProjectStatus] = useState<string | null>(null)
  const [projectErrorMessage, setProjectErrorMessage] = useState<string | null>(null)
  const {
    open: showProcessTip,
    requestOpen: requestProcessTip,
    dismiss: dismissProcessTip,
  } = useLimitedDisplayDialog(PROCESS_TIP_STORAGE_KEY, PROCESS_TIP_MAX_DISPLAYS)
  // 同源扩散：偏好需按 projectId 持久化到 sessionStorage，否则用户从
  // /requirements（唯一人工审核节点）返回上传页时被 useState(true) 默认值覆盖，
  // "取消勾选"在运行过程中会被自动还原成勾选
  const [skipVlForTender, setSkipVlForTender] = useState<boolean>(() => {
    const pidNum = projectId ? parseInt(projectId) : NaN
    const persisted = readPersistedFileUploadSkipVl(pidNum)
    return persisted ?? true
  })

  const handleSkipVlForTenderChange = useCallback((checked: boolean) => {
    setSkipVlForTender(checked)
    const pidNum = projectId ? parseInt(projectId) : NaN
    persistFileUploadSkipVl(pidNum, checked)
  }, [projectId])

  const activeRetryState =
    retryState?.failedNode === 'document_process'
      ? retryState
      : null

  const refreshRetryState = useCallback(async () => {
    if (!projectId) return
    setRefreshingRetry(true)
    try {
      const state = await reviewService.getRetryState(parseInt(projectId))
      setRetryState(state)
    } catch {
      setRetryState(null)
    } finally {
      setRefreshingRetry(false)
    }
  }, [projectId])

  // 文档解析进度轮询（与 CollusionUpload 共用同一 hook）。
  // FileUpload 传 (s) => getPollingInterval(s, true) 包含 vectorizing 8s 自适应差异。
  const docPolling = useDocumentProcessPolling({
    projectId: projectId ? parseInt(projectId) : null,
    getInterval: (stage) => getPollingInterval(stage, true),
    onRefreshRetryState: refreshRetryState,
  })

  // 阻止全局文件拖入浏览器默认行为（防止拖到区域外时打开文件）
  useEffect(() => {
    const preventDefaultDrop = (e: DragEvent) => {
      e.preventDefault()
    }
    document.addEventListener('dragover', preventDefaultDrop)
    document.addEventListener('drop', preventDefaultDrop)
    return () => {
      document.removeEventListener('dragover', preventDefaultDrop)
      document.removeEventListener('drop', preventDefaultDrop)
    }
  }, [])

  // 同源扩散：projectId 变化时从 sessionStorage 重新读取并同步本地状态
  useEffect(() => {
    const pidNum = projectId ? parseInt(projectId) : NaN
    const persisted = readPersistedFileUploadSkipVl(pidNum)
    if (persisted !== null) {
      setSkipVlForTender(persisted)
    }
  }, [projectId])

  // 对已存在但仍在处理中的文件轮询
  const startExistingDocPolling = (slotId: string, documentId: number) => {
    if (!projectId) return
    docPolling.startPoll({
      documentId,
      pollKey: `existing_${slotId}_${documentId}`,
      onTick: (d) => {
        patchSlot(slotId, { existingProcessStatus: d })
      },
      onCompleted: () => {
        updateSlotExisting(slotId, (prev) => prev ? { ...prev, parsed: 1 } : null)
        patchSlot(slotId, { existingProcessStatus: null })
      },
      onFailed: (errMsg) => {
        const failure = toExistingDocumentFailure(errMsg)
        updateSlotExisting(slotId, (prev) => prev ? { ...prev, ...failure } : null)
        patchSlot(slotId, { existingProcessStatus: null })
        // onRefreshRetryState 由 hook 内部触发
      },
    })
  }

  // 检查项目状态和已有文档，对未解析完的文档启动轮询
  useEffect(() => {
    const checkProject = async () => {
      if (!projectId) return
      try {
        const project = await projectService.getProject(parseInt(projectId))
        setProjectStatus(project.status)
        setProjectErrorMessage(project.errorMessage ?? null)

        const docs = await documentService.getDocuments(parseInt(projectId))
        const data = (docs as DocumentsPayload).data
        const bids = data?.bids ?? (data?.bid ? [data.bid] : [])

        // 从服务端文档推断信封模式(分卷文档存在即切分卷)
        if (bids.length > 0) {
          setMode(inferEnvelopeModeFromDocuments(bids))
        }
        if (data?.tender) {
          patchSlot('tender', { existing: data.tender })
          if (!data.tender.parsed) {
            startExistingDocPolling('tender', data.tender.id)
          }
        }
        for (const bidDoc of bids) {
          const slotId = bidDoc.bid_part ?? 'integral'
          patchSlot(slotId, { existing: bidDoc })
          if (!bidDoc.parsed) {
            startExistingDocPolling(slotId, bidDoc.id)
          }
        }
        setHasExistingDocs(Boolean(data?.tender) && envelopeIsComplete(bids))
        await refreshRetryState()
      } catch {
        // 忽略
      }
    }
    checkProject()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, refreshRetryState])

  // 新上传文件的进度轮询（Promise 包装让上传流程能 await）
  const startPolling = (slotId: string, documentId: number): Promise<void> => {
    return new Promise((resolve, reject) => {
      docPolling.startPoll({
        documentId,
        pollKey: `${slotId}_${documentId}`,
        onTick: (d) => {
          patchSlot(slotId, { processStatus: d })
        },
        onCompleted: () => {
          updateSlotFile(slotId, (prev) =>
            prev ? { ...prev, status: 'success', message: '处理完成' } : null)
          patchSlot(slotId, { processStatus: null })
          resolve()
        },
        onFailed: (errMsg) => {
          updateSlotFile(slotId, (prev) =>
            prev ? { ...prev, status: 'error', message: errMsg } : null)
          patchSlot(slotId, { processStatus: null })
          // onRefreshRetryState 由 hook 内部触发
          reject(new Error(errMsg))
        },
      })
    })
  }

  const markDocumentRetryPolling = (documentId: number | null | undefined) => {
    if (!documentId) return
    for (const slot of allSlots) {
      const state = slotState(slot.id)
      if (state.existing?.id === documentId) {
        updateSlotExisting(slot.id, (prev) => prev ? { ...prev, parsed: 0 } : prev)
        startExistingDocPolling(slot.id, documentId)
        return
      }
      if (state.file?.documentId === documentId) {
        updateSlotFile(slot.id, (prev) =>
          prev ? { ...prev, status: 'processing', message: '重试处理中...' } : prev)
        void startPolling(slot.id, documentId).catch(() => undefined)
        return
      }
    }
  }

  const handleRetryDocumentProcess = async () => {
    if (!projectId || !activeRetryState) return
    setRetrying(true)
    try {
      const nextState = await reviewService.retryReview(
        parseInt(projectId),
        activeRetryState.failedNode,
        activeRetryState.targetId,
      )
      setRetryState(nextState)
      markDocumentRetryPolling(nextState.targetId ?? activeRetryState.targetId)
      toast.success('已从文档解析节点继续重试')
    } catch (err) {
      toast.error(`重试失败: ${err instanceof Error ? err.message : '未知错误'}`)
    } finally {
      setRetrying(false)
    }
  }

  // 先完成全部文件落库，再触发解析，避免单文件解析制造半成品运行态。
  const uploadSingleFile = async (
    slot: UploadSlotDescriptor,
    uploadedFile: EnvelopeUploadedFile,
  ): Promise<{ documentId: number; gateResult: DocGateResult | null } | null> => {
    if (!projectId) return null

    updateSlotFile(slot.id, (prev) => prev ? { ...prev, status: 'uploading' } : null)

    try {
      // 1. 上传文件(分卷带 bid_part)
      const uploadResponse = await documentService.uploadFile(
        parseInt(projectId),
        uploadedFile.file,
        slot.docType,
        slot.bidPart,
      )
      const responseData = (uploadResponse as {
        data?: { id?: number; gate_result?: DocGateResult | null }
      }).data
      const documentId = responseData?.id
      if (!documentId) throw new Error('上传响应中没有文档ID')

      updateSlotFile(slot.id, (prev) => prev ? {
        ...prev, status: 'processing', documentId, message: '等待其他文件上传...'
      } : null)

      return { documentId, gateResult: responseData?.gate_result ?? null }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : '上传失败'
      updateSlotFile(slot.id, (prev) =>
        prev ? { ...prev, status: 'error', message: errorMessage } : null)
      throw err
    }
  }

  const processUploadedFile = async (
    slot: UploadSlotDescriptor,
    documentId: number,
    gateAck: boolean = false,
  ): Promise<void> => {
    if (!projectId) return

    updateSlotFile(slot.id, (prev) => prev ? {
      ...prev, status: 'processing', documentId, message: '正在处理...'
    } : null)

    const skipVl = slot.docType === 'tender' ? skipVlForTender : false
    await documentService.processDocument(parseInt(projectId), documentId, skipVl, gateAck)
    await startPolling(slot.id, documentId)
  }

  // 全部文件的解析与完成后跳转(上传与门禁确认之后的公共后半程)
  const processAllFiles = async (
    documents: Array<{ slotId: string; documentId: number }>,
    gateAckSlotIds: string[],
  ) => {
    await Promise.all(
      documents.map(({ slotId, documentId }) => {
        const slot = allSlots.find((s) => s.id === slotId)
        if (!slot) return Promise.resolve()
        return processUploadedFile(slot, documentId, gateAckSlotIds.includes(slotId))
      }),
    )

    setTimeout(() => {
      if (!projectId) return
      navigate(bidReviewPostProcessPath(projectId, runMode))
    }, 1000)
  }

  const handleReplaceUpload = async () => {
    if (!projectId || !replacingSlot) return
    const slot = allSlots.find((s) => s.id === replacingSlot)
    const file = slot ? slotState(slot.id).file : null
    if (!slot || !file || file.status !== 'pending') {
      toast.warning('请先选择要更换的文件')
      return
    }

    setIsUploading(true)
    setError(null)
    try {
      const result = await runReplaceFailedUpload({
        projectId: parseInt(projectId),
        slot: { id: slot.id, docType: slot.docType, bidPart: slot.bidPart },
        file,
        skipVlForTender,
        uploadFile: (pid, f, docType, bidPart) =>
          documentService.uploadFile(pid, f, docType, bidPart) as Promise<{
            data?: { id?: number; gate_result?: DocGateResult | null }
          }>,
        processDocument: (pid, documentId, skipVl, gateAck) =>
          documentService.processDocument(pid, documentId, skipVl, gateAck),
        getDocuments: async (pid) => {
          const docs = await documentService.getDocuments(pid)
          return docs as DocumentsPayload
        },
        getProjectStatus: async (pid) => {
          const project = await projectService.getProject(pid)
          return project.status
        },
        startPolling: (slotId, documentId) => startPolling(slotId, documentId),
        setFileStatus: (updater) => updateSlotFile(slot.id, updater),
      })

      patchSlot('tender', { existing: result.tender })
      for (const bidDoc of result.bids) {
        patchSlot((bidDoc as ExistingDocumentPayload).bid_part ?? 'integral', {
          existing: bidDoc,
        })
      }
      setHasExistingDocs(
        Boolean(result.tender) && envelopeIsComplete(result.bids),
      )
      updateSlotFile(slot.id, () => null)
      setReplacingSlot(null)
      setProjectStatus(result.projectStatus)
      toast.success('文件已更换并处理完成')
    } catch (err) {
      const msg = err instanceof Error ? err.message : '更换处理失败'
      toast.error(resolveDocumentProcessFailureMessage(msg))
    } finally {
      setIsUploading(false)
    }
  }

  const handleUpload = async () => {
    const slotFiles = allSlots.map((slot) => ({
      slot,
      file: slotState(slot.id).file,
    }))
    const missing = slotFiles.filter((entry) => !entry.file)
    if (missing.length > 0) {
      if (missing.some((entry) => entry.slot.id === 'tender') || mode === DEFAULT_ENVELOPE_MODE) {
        toast.warning('请选择招标文件和投标文件')
      } else {
        toast.warning('请同时选择商务标和技术标，或改回「整本投标文件」只上传一份')
      }
      return
    }
    if (!projectId) {
      setError('项目ID无效')
      return
    }

    setIsUploading(true)

    try {
      const uploads = await Promise.all(
        slotFiles.map(async ({ slot, file }) => ({
          slot,
          result: await uploadSingleFile(slot, file as EnvelopeUploadedFile),
        })),
      )
      if (uploads.some(({ result }) => !result)) {
        throw new Error('全部文件必须同时上传成功')
      }

      // 文档门禁: 任一文件可疑则弹窗等待用户决策，解析暂不启动
      const items: GateWarningItem[] = []
      const flaggedSlotIds: string[] = []
      const documents: Array<{ slotId: string; documentId: number }> = []
      for (const { slot, result } of uploads) {
        if (!result) continue
        documents.push({ slotId: slot.id, documentId: result.documentId })
        if (result.gateResult && result.gateResult.verdict !== 'pass') {
          const file = slotState(slot.id).file
          items.push({ filename: file?.name ?? slot.docName, gateResult: result.gateResult })
          flaggedSlotIds.push(slot.id)
        }
      }
      if (items.length > 0) {
        setGatePending({ items, documents, flaggedSlotIds })
        return
      }

      await processAllFiles(documents, [])
    } catch (err) {
      toast.error(`上传处理失败: ${err instanceof Error ? err.message : '未知错误'}`)
    } finally {
      setIsUploading(false)
    }
  }

  // 门禁弹窗: 接受警告，继续解析(gate_ack留痕)
  const handleGateContinue = async () => {
    if (!gatePending) return
    const pending = gatePending
    setGateBusy(true)
    setIsUploading(true)
    try {
      setGatePending(null)
      await processAllFiles(pending.documents, pending.flaggedSlotIds)
    } catch (err) {
      toast.error(`处理失败: ${err instanceof Error ? err.message : '未知错误'}`)
    } finally {
      setGateBusy(false)
      setIsUploading(false)
    }
  }

  // 门禁弹窗: 返回更换文档(删除本次上传的全部未解析文档，回到选择状态)
  const handleGateReplace = async () => {
    if (!gatePending || !projectId) return
    const pending = gatePending
    const pid = parseInt(projectId)
    setGateBusy(true)
    try {
      await Promise.all(
        pending.documents.map(({ documentId }) =>
          documentService.deleteDocument(pid, documentId).catch(() => undefined),
        ),
      )
      // 被标记的文件清空待重新选择；未标记的文件回到待上传状态(下次一并重传)
      for (const { slotId } of pending.documents) {
        if (pending.flaggedSlotIds.includes(slotId)) {
          updateSlotFile(slotId, () => null)
        } else {
          updateSlotFile(slotId, (prev) => prev
            ? { ...prev, status: 'pending', documentId: undefined, message: undefined }
            : null)
        }
      }
      setGatePending(null)
      toast.info('已撤销本次上传，请重新选择文档')
    } catch (err) {
      toast.error(`撤销失败: ${err instanceof Error ? err.message : '未知错误'}`)
    } finally {
      setGateBusy(false)
    }
  }

  const handleUploadClick = () => {
    if (!requestProcessTip()) {
      void handleUpload()
    }
  }

  const handleResetUpload = async () => {
    if (!projectId) return
    setIsResettingUpload(true)
    try {
      await documentService.resetBidReviewUpload(parseInt(projectId))
      resetSlots()
      setMode(DEFAULT_ENVELOPE_MODE)
      setHasExistingDocs(false)
      setReplacingSlot(null)
      setRetryState(null)
      setProjectStatus('created')
      setProjectErrorMessage(null)
      toast.success('已重置上传状态，请重新上传招标文件和投标文件')
    } catch (err) {
      toast.error(`重置上传失败: ${err instanceof Error ? err.message : '未知错误'}`)
    } finally {
      setIsResettingUpload(false)
    }
  }

  // ==================== 派生状态 ====================

  const isSomethingLoading =
    isUploading ||
    allSlots.some((slot) => {
      const state = slotState(slot.id)
      return (
        state.file?.status === 'uploading' ||
        state.file?.status === 'processing' ||
        (state.existing != null && state.existing.parsed === 0)
      )
    })

  const tenderState = slotState('tender')
  const bidSlotFacts = bidSlots.map((slot) => {
    const state = slotState(slot.id)
    const exists = Boolean(state.existing || state.file?.documentId)
    const parsed = state.existing?.parsed
      ?? (state.file?.status === 'success' ? 1 : state.file?.documentId ? 0 : null)
    return { exists, parsed }
  })

  const uploadPageState = deriveBidReviewUploadPageState({
    projectStatus,
    tender: {
      exists: Boolean(tenderState.existing || tenderState.file?.documentId),
      parsed: tenderState.existing?.parsed
        ?? (tenderState.file?.status === 'success' ? 1 : tenderState.file?.documentId ? 0 : null),
    },
    bid: {
      anyDoc: bidSlotFacts.some((fact) => fact.exists),
      complete: bidSlotFacts.length > 0 && bidSlotFacts.every((fact) => fact.exists),
      allParsed:
        bidSlotFacts.length > 0 &&
        bidSlotFacts.every((fact) => fact.exists && fact.parsed === 1),
    },
    isUploading,
    isSomethingLoading,
    replacingSlot,
    runMode,
  })

  useEffect(() => {
    if (uploadPageState.mode !== 'redirect' || !uploadPageState.targetRoute) {
      return
    }
    navigate(`/project/${projectId}/${uploadPageState.targetRoute}`)
  }, [
    uploadPageState.mode,
    uploadPageState.targetRoute,
    navigate,
    projectId,
  ])

  // 模式切换: 已有投标文档(落库/已上传)或处理中时禁止切换, 防止混装(模式冲突)
  const modeSwitchDisabled =
    isUploading ||
    isSomethingLoading ||
    replacingSlot != null ||
    ALL_BID_SLOT_IDS.some((slotId) => {
      const state = slotState(slotId)
      return Boolean(state.existing || state.file?.documentId)
    })

  const handleModeChange = (nextMode: string) => {
    if (nextMode === mode || modeSwitchDisabled) return
    // 清理所有投标槽位的本地待传选择(仅 pending 文件, 已落库文档会禁用切换)
    for (const slotId of ALL_BID_SLOT_IDS) {
      patchSlot(slotId, { file: null, processStatus: null, dragging: false })
    }
    setMode(nextMode)
  }

  const anyPendingFile = allSlots.some(
    (slot) => slotState(slot.id).file?.status === 'pending',
  )
  const allSlotsHaveFile = allSlots.every((slot) => slotState(slot.id).file)
  const allExistingParsed =
    tenderState.existing?.parsed === 1 &&
    bidSlots.length > 0 &&
    bidSlots.every((slot) => slotState(slot.id).existing?.parsed === 1)

  const renderSlot = (slot: UploadSlotDescriptor) => {
    const state = slotState(slot.id)
    return (
      <UploadSlotArea
        key={slot.id}
        slotId={slot.id}
        label={slot.label}
        docName={slot.docName}
        file={state.file}
        existingDoc={state.existing}
        processStatus={state.processStatus}
        existingProcessStatus={state.existingProcessStatus}
        isDragging={state.dragging}
        isReplacing={replacingSlot === slot.id}
        isUploading={isUploading}
        isSomethingLoading={isSomethingLoading}
        onDraggingChange={(dragging) => patchSlot(slot.id, { dragging })}
        onFileChosen={(file) => {
          updateSlotFile(slot.id, () => ({
            file,
            name: file.name,
            status: 'pending',
          }))
        }}
        onStartReplace={() => {
          setReplacingSlot(slot.id)
          updateSlotFile(slot.id, () => null)
        }}
        onCancelSelect={() => {
          updateSlotFile(slot.id, () => null)
          if (replacingSlot === slot.id) setReplacingSlot(null)
        }}
        showHeading={slot.id !== 'tender' && slot.id !== 'integral'}
      />
    )
  }

  // ==================== 渲染 ====================

  const recoveryPanel =
    uploadPageState.mode === 'recover_half_upload' ? (
      <UploadRecoveryPanel
        mode="recover_half_upload"
        slotSummaries={allSlots.map((slot) => ({
          slotId: slot.id,
          docName: slot.docName,
          filename: slotState(slot.id).existing?.filename ?? null,
        }))}
        isResetting={isResettingUpload}
        onReset={handleResetUpload}
      />
    ) : uploadPageState.mode === 'failed_recovery' ? (
      <UploadRecoveryPanel
        mode="failed_recovery"
        slotSummaries={[]}
        failureReason={
          activeRetryState?.lastError || projectErrorMessage
            ? resolveDocumentProcessFailureMessage(
                activeRetryState?.lastError || projectErrorMessage,
              )
            : null
        }
        isResetting={isResettingUpload}
        onReset={handleResetUpload}
      />
    ) : null

  return (
    <div>
      <div className="flex justify-between items-center mb-6">
        <div className="flex items-center">
          <button
            onClick={() => navigate(APP_HOME)}
            className="mr-4 p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-md transition-colors"
            title="返回项目列表"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
          </button>
          <h1 className="text-2xl font-bold text-gray-900">投标审查 — 上传文件</h1>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-3">
          {isRunModeChooserVisible(isBidReviewUploadChooserMode(uploadPageState.mode)) ? (
            <RunModeOption
              value={runMode}
              disabled={runModeLocked}
              projectType="bid_review"
              onChange={(next) => { void setRunMode(next) }}
            />
          ) : null}
          {hasExistingDocs && allExistingParsed && projectId && (
            <button
              onClick={() => navigate(bidReviewPostProcessPath(projectId, runMode))}
              className="px-6 py-2 rounded-md text-white bg-green-500 hover:bg-green-600 flex items-center"
            >
              下一步
              <svg className="w-4 h-4 ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14 5l7 7m0 0l-7 7m7-7H3" />
              </svg>
            </button>
          )}
        </div>
      </div>

      <BidReviewUploadView
        error={error}
        retryPanel={
          activeRetryState ? (
            <RetryActionPanel
              state={activeRetryState}
              onRetry={handleRetryDocumentProcess}
              onRefresh={refreshRetryState}
              retrying={retrying}
              refreshing={refreshingRetry}
            />
          ) : null
        }
        recoveryPanel={recoveryPanel}
        tenderSlot={renderSlot(allSlots[0])}
        bidSlotList={<>{bidSlots.map((slot) => renderSlot(slot))}</>}
        showInputs={uploadPageState.canShowUploadInputs}
        skipVlChecked={skipVlForTender}
        skipVlDisabled={isUploading || isSomethingLoading}
        onSkipVlChange={handleSkipVlForTenderChange}
        envelopeMode={mode}
        envelopeDisabled={modeSwitchDisabled}
        onEnvelopeChange={handleModeChange}
        postProcessHint={getPostProcessHint(runMode, 'bid_review')}
        actions={
          <div className="flex items-center justify-end gap-3">
            {uploadPageState.mode === 'replacing' && (
              <button
                onClick={() => void handleReplaceUpload()}
                disabled={!canSubmitReplaceUpload(
                  replacingSlot,
                  replacingSlot ? slotState(replacingSlot).file : null,
                  isUploading || isSomethingLoading,
                )}
                className={`px-6 py-2 rounded-md text-white text-sm ${
                  !canSubmitReplaceUpload(
                    replacingSlot,
                    replacingSlot ? slotState(replacingSlot).file : null,
                    isUploading || isSomethingLoading,
                  )
                    ? 'bg-gray-400 cursor-not-allowed'
                    : 'bg-blue-500 hover:bg-blue-600'
                }`}
              >
                {isUploading ? '处理中...' : '上传并处理该文件'}
              </button>
            )}
            {uploadPageState.mode !== 'replacing' && uploadPageState.canShowUploadInputs && anyPendingFile && (
              <button
                onClick={handleUploadClick}
                disabled={!allSlotsHaveFile || isUploading || isSomethingLoading}
                className={`px-6 py-2 rounded-md text-white text-sm ${
                  !allSlotsHaveFile || isUploading || isSomethingLoading
                    ? 'bg-gray-400 cursor-not-allowed'
                    : 'bg-blue-500 hover:bg-blue-600'
                }`}
              >
                {isUploading ? '处理中...' : '上传并处理'}
              </button>
            )}
          </div>
        }
      />

      <Modal
        visible={showProcessTip}
        title="处理时间说明"
        maskClosable={false}
        footer={
          <button
            onClick={() => {
              dismissProcessTip()
              void handleUpload()
            }}
            className="px-5 py-2 bg-primary text-white rounded-md hover:bg-blue-600 text-sm"
          >
            知道了，开始处理
          </button>
        }
      >
        <p className="text-sm text-gray-800 font-medium leading-relaxed">
          文件上传后，系统将依次执行文档解析、内容分块、AI 逐项分析等多个处理步骤。
          根据文件大小和内容复杂度，整个过程可能需要较长时间，请耐心等待。
        </p>
        <p className="text-sm text-gray-600 mt-3">
          处理期间您可以离开当前页面，稍后通过项目列表随时返回查看进度。
        </p>
      </Modal>

      <DocGateWarningDialog
        open={gatePending != null}
        items={gatePending?.items ?? []}
        busy={gateBusy}
        onContinue={handleGateContinue}
        onReplace={handleGateReplace}
      />
    </div>
  )
}

export default FileUploadPage
