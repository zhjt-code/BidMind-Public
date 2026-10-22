import React, { useState, useCallback, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { documentService, type DocGateResult, type ProcessStatusData } from '@/services/documentApi'
import { collusionService } from '@/services/collusionApi'
import { projectService } from '@/services/projectApi'
import type { WritingRetryState } from '@/services/writingRetryTransforms'
import FileProcessProgress from '@/components/FileProcessProgress'
import { Modal } from '@/components'
import DocGateWarningDialog from '@/components/DocGateWarningDialog'
import { useLimitedDisplayDialog } from '@/hooks/useLimitedDisplayDialog'
import { formatFileSize, formatDate, getPollingInterval } from '@/lib/formatters'
import { isPaymentEnabled } from '@/config/features'
import { useInsufficientBalanceGate } from '@/hooks/useInsufficientBalanceGate'
import BalanceGateCard from '@/components/BalanceGateCard'
import { getBalanceGateCopy } from '@/lib/balanceGateCopy'
import {
  shouldAutoStartCollusionAnalysis,
  isUploadAndProcessEnabled,
} from './collusionUploadState'
import { useDocumentProcessPolling } from './upload/hooks/useDocumentProcessPolling'
import { APP_HOME } from '@/config/routes'
import { checkDuplicateFiles } from './collusionUploadParts/duplicate'
import { buildGateReplacePlan } from './collusionUploadParts/gateReplacePlan'
import DuplicateWarningDialog from './collusionUploadParts/DuplicateWarningDialog'
import {
  persistCollusionSkipVl,
  readPersistedCollusionSkipVl,
} from './collusionUploadParts/skipVlState'
import SkipVlOption from './collusionUploadParts/SkipVlOption'
import FieldHintButton from '@/components/upload/FieldHintButton'
import TruncatedHoverText from '@/components/upload/TruncatedHoverText'
import {
  BID_IMAGE_PACE_HINT,
  IMAGE_PACE_LABEL,
  TENDER_IMAGE_PACE_HINT,
} from '@/components/upload/documentPace'
import RetryActionPanel from '@/pages/writing/RetryActionPanel'
import KnowledgeUsagePanel from '@/components/KnowledgeUsagePanel'
import { toExistingDocumentFailure } from './fileUploadParts/existingDocFailure'
import {
  formatFailureMessage,
  resolveDocumentProcessFailureMessage,
} from '@/lib/formatFailureMessage'
import {
  UPLOAD_ACCEPT,
  UPLOAD_FORMAT_HINT,
  UPLOAD_FORMAT_TOAST,
  isAllowedUploadFile,
} from '@/lib/uploadAccept'

// ==================== 类型 ====================

interface UploadedFile {
  key: string
  file: File
  name: string
  type: 'tender' | 'bid'
  status: 'pending' | 'uploading' | 'processing' | 'success' | 'error'
  documentId?: number
  message?: string
}

interface ExistingDocument {
  id: number
  filename: string
  file_size: number
  created_at: string
  doc_type: string
  parsed: number
  /** : 文档处理失败原因 */
  lastError?: string
}

const MIN_BID = 2
const MAX_BID = 15
const TENDER_RECOMMENDED_HINT =
  '不上传也可分析。上传后系统能区分招标要求造成的正常一致与真实串通，误判会明显降低。'
const PROCESS_TIP_STORAGE_KEY = 'bidmind_processing_tip_display_count'
const PROCESS_TIP_MAX_DISPLAYS = 2

// 已上传待处理的文件条目(门禁决策与解析阶段共用)
interface GateUploadedEntry {
  key: string
  type: 'tender' | 'bid'
  name: string
  documentId: number
  gateResult: DocGateResult | null
}

// ==================== 主组件 ====================

const CollusionUploadPage: React.FC = () => {
  const { id: projectId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  // 余额门禁迁移到共享 gate；阻断态落 sessionStorage，刷新后仍截断自动启动
  const gate = useInsufficientBalanceGate({
    projectId: projectId ? parseInt(projectId) : NaN,
    nodeKey: 'collusion_start',
    paymentEnabled: isPaymentEnabled,
    toastPrefix: '启动失败',
  })

  // 招标文件（可选，1份）
  const [tenderFile, setTenderFile] = useState<UploadedFile | null>(null)
  const [existingTender, setExistingTender] = useState<ExistingDocument | null>(null)
  const [tenderProcessStatus, setTenderProcessStatus] = useState<ProcessStatusData | null>(null)
  const [isDraggingTender, setIsDraggingTender] = useState(false)

  // 投标文件（必选，2-15份）
  const [bidFiles, setBidFiles] = useState<UploadedFile[]>([])
  const [existingBids, setExistingBids] = useState<ExistingDocument[]>([])
  const [bidProcessStatuses, setBidProcessStatuses] = useState<Record<string, ProcessStatusData | null>>({})
  const [isDraggingBid, setIsDraggingBid] = useState(false)

  // 图片识别开关按文件类型拆分：招标默认跳过（与 FileUpload/WritingUpload 对齐加速）；
  // 投标默认不跳过（串标证据常依赖扫描件/盖章页）。用户显式选择后在当前会话内保持。
  const [skipTenderVl, setSkipTenderVl] = useState<boolean>(() => {
    const pidNum = projectId ? parseInt(projectId) : NaN
    const persisted = readPersistedCollusionSkipVl(pidNum, 'tender')
    return persisted ?? true
  })
  const [skipBidVl, setSkipBidVl] = useState<boolean>(() => {
    const pidNum = projectId ? parseInt(projectId) : NaN
    const persisted = readPersistedCollusionSkipVl(pidNum, 'bid')
    return persisted ?? false
  })

  const handleSkipTenderVlChange = useCallback((checked: boolean) => {
    setSkipTenderVl(checked)
    const pidNum = projectId ? parseInt(projectId) : NaN
    persistCollusionSkipVl(pidNum, 'tender', checked)
  }, [projectId])

  const handleSkipBidVlChange = useCallback((checked: boolean) => {
    setSkipBidVl(checked)
    const pidNum = projectId ? parseInt(projectId) : NaN
    persistCollusionSkipVl(pidNum, 'bid', checked)
  }, [projectId])

  // 文档门禁警告弹窗状态
  const [gatePending, setGatePending] = useState<{
    flagged: GateUploadedEntry[]
    clean: GateUploadedEntry[]
  } | null>(null)
  const [gateBusy, setGateBusy] = useState(false)

  // 重名警告弹窗状态
  const [showDuplicateWarning, setShowDuplicateWarning] = useState(false)
  const [duplicateWarningData, setDuplicateWarningData] = useState<{
    duplicateNames: string[]
    filesToAdd: File[]
  } | null>(null)

  // 已有文档的处理进度（导航返回后恢复进度条用）
  const [existingBidProcessStatuses, setExistingBidProcessStatuses] = useState<Record<number, ProcessStatusData | null>>({})
  const [existingTenderProcessStatus, setExistingTenderProcessStatus] = useState<ProcessStatusData | null>(null)

  const [isUploading, setIsUploading] = useState(false)
  const [isStartingAnalysis, setIsStartingAnalysis] = useState(false)
  // React.StrictMode + effect 依赖变动会导致 handleStartAnalysis 在
  // setIsStartingAnalysis(true) 异步落盘前被重入两次，两个并发 POST /analyze 中第二个
  // 命中后端"已在进行中"分支报 400。组件实例级 ref 同步生效，作为单次启动的硬护栏；
  // 组件重新挂载时 ref 自然归零，保留 的"返回页面可再次自动流转"语义。
  const startInFlightRef = useRef(false)
  const {
    open: showProcessTip,
    requestOpen: requestProcessTip,
    dismiss: dismissProcessTip,
  } = useLimitedDisplayDialog(PROCESS_TIP_STORAGE_KEY, PROCESS_TIP_MAX_DISPLAYS)
  // 项目状态横幅（reviewing / failed 时显示提示，不自动跳转）
  const [projectStatus, setProjectStatus] = useState<string | null>(null)
  const [projectErrorMessage, setProjectErrorMessage] = useState<string | null>(null)
  const [retryState, setRetryState] = useState<WritingRetryState | null>(null)
  const [retrying, setRetrying] = useState(false)
  const [refreshingRetry, setRefreshingRetry] = useState(false)

  const activeRetryState =
    retryState?.failedNode === 'document_process'
      ? retryState
      : null

  const refreshRetryState = useCallback(async () => {
    if (!projectId) return
    setRefreshingRetry(true)
    try {
      const state = await collusionService.getRetryState(parseInt(projectId))
      setRetryState(state)
    } catch {
      setRetryState(null)
    } finally {
      setRefreshingRetry(false)
    }
  }, [projectId])

  // 文档解析进度轮询（与 FileUpload 共用同一 hook；mountedRef / pollingRef /
  // cleanup useEffect 全部由 hook 内部接管，三处 setTimeout 链消除）。
  const docPolling = useDocumentProcessPolling({
    projectId: projectId ? parseInt(projectId) : null,
    getInterval: getPollingInterval,
    onRefreshRetryState: refreshRetryState,
  })

  // 阻止全局拖入
  useEffect(() => {
    const prevent = (e: DragEvent) => e.preventDefault()
    document.addEventListener('dragover', prevent)
    document.addEventListener('drop', prevent)
    return () => {
      document.removeEventListener('dragover', prevent)
      document.removeEventListener('drop', prevent)
    }
  }, [])

  // projectId 变化时（极少触发，但与 writing 模块对齐保持健壮性）
  // 从 sessionStorage 重新读取并同步本地状态，避免显示与持久化偏好脱节
  useEffect(() => {
    const pidNum = projectId ? parseInt(projectId) : NaN
    const persistedTender = readPersistedCollusionSkipVl(pidNum, 'tender')
    const persistedBid = readPersistedCollusionSkipVl(pidNum, 'bid')
    if (persistedTender !== null) {
      setSkipTenderVl(persistedTender)
    }
    if (persistedBid !== null) {
      setSkipBidVl(persistedBid)
    }
  }, [projectId])

  // 加载已有文档，不做任何自动跳转（避免循环导航问题）
  useEffect(() => {
    const init = async () => {
      if (!projectId) return
      try {
        // 只记录状态用于显示横幅，所有导航由用户主动触发
        const project = await projectService.getProject(parseInt(projectId))
        setProjectStatus(project.status)
        setProjectErrorMessage(project.errorMessage ?? null)

        // 加载文档列表
        const docsResp = await documentService.getDocuments(parseInt(projectId))
        const data = (docsResp as { data?: {
          tender?: ExistingDocument
          bids?: ExistingDocument[]
          bid?: ExistingDocument
        } }).data

        if (data?.tender) setExistingTender(data.tender)
        const bids = data?.bids && data.bids.length > 0
          ? data.bids
          : data?.bid ? [data.bid] : []
        setExistingBids(bids)

        // 对未解析完的文件启动轮询
        for (const doc of bids) {
          if (!doc.parsed) {
            startExistingDocPolling('bid', doc.id)
          }
        }
        if (data?.tender && !data.tender.parsed) {
          startExistingDocPolling('tender', data.tender.id)
        }
        await refreshRetryState()
      } catch {
        // 忽略加载错误
      }
    }
    init()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, refreshRetryState])


  // 对已存在但仍在处理中的文件轮询（接入 useDocumentProcessPolling）
  const startExistingDocPolling = (type: 'tender' | 'bid', documentId: number) => {
    if (!projectId) return
    docPolling.startPoll({
      documentId,
      pollKey: `existing_${type}_${documentId}`,
      onTick: (d) => {
        if (type === 'bid') {
          setExistingBidProcessStatuses((prev) => ({ ...prev, [documentId]: d }))
        } else {
          setExistingTenderProcessStatus(d)
        }
      },
      onCompleted: () => {
        if (type === 'bid') {
          setExistingBids((prev) =>
            prev.map((doc) => doc.id === documentId ? { ...doc, parsed: 1 } : doc)
          )
          setExistingBidProcessStatuses((prev) => { const n = { ...prev }; delete n[documentId]; return n })
        } else {
          setExistingTender((prev) => prev ? { ...prev, parsed: 1 } : null)
          setExistingTenderProcessStatus(null)
        }
      },
      onFailed: (errMsg) => {
        const failure = toExistingDocumentFailure(errMsg)
        if (type === 'bid') {
          setExistingBids((prev) =>
            prev.map((doc) => doc.id === documentId ? { ...doc, ...failure } : doc)
          )
          setExistingBidProcessStatuses((prev) => { const n = { ...prev }; delete n[documentId]; return n })
        } else {
          setExistingTender((prev) => prev ? { ...prev, ...failure } : null)
          setExistingTenderProcessStatus(null)
        }
        // onRefreshRetryState 由 hook 内部触发；无需在此重复调用
      },
    })
  }

  // 计算总投标数（已有 + 新上传成功）
  const totalBidCount = existingBids.length + bidFiles.filter((f) => f.status !== 'error').length
  const pendingBidCount = bidFiles.filter((f) => f.status === 'pending').length
  const pendingTenderFile = tenderFile?.status === 'pending' ? 1 : 0
  const hasPendingFiles = pendingBidCount + pendingTenderFile > 0

  const isAnyProcessing =
    bidFiles.some((f) => f.status === 'uploading' || f.status === 'processing') ||
    (tenderFile?.status === 'uploading' || tenderFile?.status === 'processing')

  const canUploadAndProcess = isUploadAndProcessEnabled({
    isUploading,
    isAnyProcessing,
    totalBidCount,
    minBid: MIN_BID,
  })

  const allExistingBidsParsed = existingBids.every((b) => b.parsed === 1)
  const canStartAnalysis =
    totalBidCount >= MIN_BID &&
    !isAnyProcessing &&
    !isUploading &&
    allExistingBidsParsed &&
    bidFiles.every((f) => f.status === 'success' || f.status === 'pending' || f.status === 'error')
    // pending 文件还未上传，不能启动分析
    && bidFiles.filter((f) => f.status === 'pending').length === 0

  const isSomethingLoading =
    isAnyProcessing ||
    existingBids.some((b) => b.parsed === 0) ||
    (existingTender != null && existingTender.parsed === 0)

  // ==================== 文件处理 ====================

  // 检测重名文件（按 §1.2 决议：纯函数迁出到 collusionUploadParts/duplicate.ts）
  const detectDuplicates = useCallback((files: File[]) => {
    const existingNames = [
      ...existingBids.map((d) => d.filename),
      ...bidFiles.filter((f) => f.status !== 'error').map((f) => f.name),
    ]
    return checkDuplicateFiles(files, existingNames)
  }, [existingBids, bidFiles])

  // 实际添加文件到列表
  const addBidFiles = useCallback((files: File[]) => {
    setBidFiles((prev) => [
      ...prev,
      ...files.map((f) => ({
        key: `bid_${Date.now()}_${Math.random()}`,
        file: f,
        name: f.name,
        type: 'bid' as const,
        status: 'pending' as const,
      })),
    ])
  }, [])

  // 用户确认继续上传重名文件
  const handleConfirmDuplicate = useCallback(() => {
    if (duplicateWarningData) {
      addBidFiles(duplicateWarningData.filesToAdd)
    }
    setShowDuplicateWarning(false)
    setDuplicateWarningData(null)
  }, [duplicateWarningData, addBidFiles])

  // 用户取消上传
  const handleCancelDuplicate = useCallback(() => {
    setShowDuplicateWarning(false)
    setDuplicateWarningData(null)
  }, [])

  const handleTenderFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    if (!isAllowedUploadFile(file.name)) {
      toast.warning(UPLOAD_FORMAT_TOAST)
      return
    }
    setTenderFile({ key: `tender_${Date.now()}`, file, name: file.name, type: 'tender', status: 'pending' })
    e.target.value = ''
  }, [])

  const handleBidFilesSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || [])
    if (!files.length) return

    const currentTotal = existingBids.length + bidFiles.filter((f) => f.status !== 'error').length
    const available = MAX_BID - currentTotal
    if (available <= 0) {
      toast.warning(`最多上传 ${MAX_BID} 份投标文件，当前已有 ${currentTotal} 份`)
      e.target.value = ''
      return
    }

    const toAdd = files.slice(0, available)
    const invalid = toAdd.filter((f) => !isAllowedUploadFile(f.name))
    if (invalid.length) {
      toast.warning(UPLOAD_FORMAT_TOAST)
      e.target.value = ''
      return
    }

    // 检测重名
    const { duplicateNames, filesToAdd } = detectDuplicates(toAdd)
    
    if (duplicateNames.length > 0) {
      // 有重名，弹出确认框
      setDuplicateWarningData({ duplicateNames, filesToAdd })
      setShowDuplicateWarning(true)
    } else {
      // 无重名，直接添加
      addBidFiles(filesToAdd)
    }

    e.target.value = ''
  }, [existingBids.length, bidFiles, detectDuplicates, addBidFiles])

  const handleTenderDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setIsDraggingTender(false)
    if (isUploading || tenderFile) return
    const file = e.dataTransfer.files?.[0]
    if (!file) return
    if (!isAllowedUploadFile(file.name)) {
      toast.warning(UPLOAD_FORMAT_TOAST)
      return
    }
    setTenderFile({ key: `tender_${Date.now()}`, file, name: file.name, type: 'tender', status: 'pending' })
  }, [isUploading, tenderFile])

  const handleBidDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setIsDraggingBid(false)
    if (isUploading) return
    const files = Array.from(e.dataTransfer.files || [])
    if (!files.length) return

    const currentTotal = existingBids.length + bidFiles.filter((f) => f.status !== 'error').length
    const available = MAX_BID - currentTotal
    if (available <= 0) {
      toast.warning(`最多上传 ${MAX_BID} 份投标文件`)
      return
    }

    const toAdd = files.filter((f) => isAllowedUploadFile(f.name)).slice(0, available)
    if (!toAdd.length) {
      toast.warning(UPLOAD_FORMAT_TOAST)
      return
    }

    // 检测重名
    const { duplicateNames, filesToAdd } = detectDuplicates(toAdd)
    
    if (duplicateNames.length > 0) {
      // 有重名，弹出确认框
      setDuplicateWarningData({ duplicateNames, filesToAdd })
      setShowDuplicateWarning(true)
    } else {
      // 无重名，直接添加
      addBidFiles(filesToAdd)
    }
  }, [isUploading, existingBids.length, bidFiles, detectDuplicates, addBidFiles])

  const removeBidFile = (key: string) => {
    setBidFiles((prev) => prev.filter((f) => f.key !== key))
  }

  // ==================== 上传逻辑 ====================

  // 新上传文件的进度轮询（接入 useDocumentProcessPolling，保留 Promise 包装
  // 让 uploadSingleFile 流程能 await，与原行为 byte-equal）
  const startPolling = (
    pollKey: string,
    documentId: number,
    onStatusUpdate: (d: ProcessStatusData) => void,
    onComplete: () => void,
    onFail: (msg: string) => void,
  ): Promise<void> => {
    return new Promise((resolve, reject) => {
      docPolling.startPoll({
        documentId,
        pollKey,
        onTick: onStatusUpdate,
        onCompleted: () => {
          onComplete()
          resolve()
        },
        onFailed: (msg) => {
          onFail(msg)
          // onRefreshRetryState 由 hook 内部触发
          reject(new Error(msg))
        },
      })
    })
  }

  const markDocumentRetryPolling = (documentId: number | null | undefined) => {
    if (!documentId) return
    if (existingTender?.id === documentId) {
      setExistingTender((prev) => prev ? { ...prev, parsed: 0 } : prev)
      startExistingDocPolling('tender', documentId)
      return
    }
    if (existingBids.some((doc) => doc.id === documentId)) {
      setExistingBids((prev) =>
        prev.map((doc) => doc.id === documentId ? { ...doc, parsed: 0 } : doc)
      )
      startExistingDocPolling('bid', documentId)
      return
    }

    const pendingTender = tenderFile?.documentId === documentId
    if (pendingTender) {
      setTenderFile((prev) => prev ? { ...prev, status: 'processing', message: '重试处理中...' } : prev)
      void startPolling(
        `tender_${documentId}`,
        documentId,
        setTenderProcessStatus,
        () => {
          setTenderFile((prev) => prev ? { ...prev, status: 'success', message: '处理完成' } : null)
          setTenderProcessStatus(null)
        },
        (msg) => {
          setTenderFile((prev) => prev ? { ...prev, status: 'error', message: msg } : null)
          setTenderProcessStatus(null)
        },
      ).catch(() => undefined)
      return
    }

    const pendingBid = bidFiles.find((file) => file.documentId === documentId)
    if (pendingBid) {
      setBidFiles((prev) =>
        prev.map((file) =>
          file.documentId === documentId ? { ...file, status: 'processing', message: '重试处理中...' } : file
        )
      )
      void startPolling(
        `bid_${documentId}`,
        documentId,
        (d) => setBidProcessStatuses((prev) => ({ ...prev, [pendingBid.key]: d })),
        () => {
          setBidFiles((prev) =>
            prev.map((file) =>
              file.documentId === documentId ? { ...file, status: 'success', message: '处理完成' } : file
            )
          )
          setBidProcessStatuses((prev) => { const next = { ...prev }; delete next[pendingBid.key]; return next })
        },
        (msg) => {
          setBidFiles((prev) =>
            prev.map((file) =>
              file.documentId === documentId ? { ...file, status: 'error', message: msg } : file
            )
          )
          setBidProcessStatuses((prev) => { const next = { ...prev }; delete next[pendingBid.key]; return next })
        },
      ).catch(() => undefined)
    }
  }

  const handleRetryDocumentProcess = async () => {
    if (!projectId || !activeRetryState) return
    setRetrying(true)
    try {
      const nextState = await collusionService.retryAnalysis(
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

  // 阶段1: 仅上传落库(返回门禁结果，解析由调用方按门禁决策触发)
  const uploadOnly = async (uploadedFile: UploadedFile): Promise<GateUploadedEntry> => {
    if (!projectId) throw new Error('项目ID无效')

    if (uploadedFile.type === 'bid') {
      setBidFiles((prev) =>
        prev.map((f) => f.key === uploadedFile.key ? { ...f, status: 'uploading' } : f)
      )
    } else {
      setTenderFile((prev) => prev ? { ...prev, status: 'uploading' } : null)
    }

    let documentId: number
    let gateResult: DocGateResult | null = null
    try {
      const uploadResp = await documentService.uploadFile(
        parseInt(projectId),
        uploadedFile.file,
        uploadedFile.type
      )
      const respData = (uploadResp as {
        data?: { id?: number; gate_result?: DocGateResult | null }
      }).data
      if (!respData?.id) throw new Error('上传响应中没有文档ID')
      documentId = respData.id
      gateResult = respData.gate_result ?? null
    } catch (err) {
      const msg = err instanceof Error ? err.message : '上传失败'
      if (uploadedFile.type === 'bid') {
        setBidFiles((prev) =>
          prev.map((f) => f.key === uploadedFile.key ? { ...f, status: 'error', message: msg } : f)
        )
      } else {
        setTenderFile((prev) => prev ? { ...prev, status: 'error', message: msg } : null)
      }
      throw err
    }

    if (uploadedFile.type === 'bid') {
      setBidFiles((prev) =>
        prev.map((f) => f.key === uploadedFile.key ? { ...f, status: 'processing', documentId } : f)
      )
    } else {
      setTenderFile((prev) => prev ? { ...prev, status: 'processing', documentId } : null)
    }

    return {
      key: uploadedFile.key,
      type: uploadedFile.type,
      name: uploadedFile.name,
      documentId,
      gateResult,
    }
  }

  // 阶段2: 触发解析并轮询进度
  const processAndPoll = async (entry: GateUploadedEntry, gateAck: boolean): Promise<void> => {
    if (!projectId) return

    const shouldSkipVl = entry.type === 'tender' ? skipTenderVl : skipBidVl
    await documentService.processDocument(
      parseInt(projectId), entry.documentId, shouldSkipVl, gateAck,
    )

    const pollKey = `${entry.type}_${entry.documentId}`
    await startPolling(
      pollKey,
      entry.documentId,
      (d) => {
        if (entry.type === 'bid') {
          setBidProcessStatuses((prev) => ({ ...prev, [entry.key]: d }))
        } else {
          setTenderProcessStatus(d)
        }
      },
      () => {
        if (entry.type === 'bid') {
          setBidFiles((prev) =>
            prev.map((f) => f.key === entry.key ? { ...f, status: 'success', message: '处理完成' } : f)
          )
          setBidProcessStatuses((prev) => { const n = { ...prev }; delete n[entry.key]; return n })
        } else {
          setTenderFile((prev) => prev ? { ...prev, status: 'success', message: '处理完成' } : null)
          setTenderProcessStatus(null)
        }
      },
      (msg) => {
        if (entry.type === 'bid') {
          setBidFiles((prev) =>
            prev.map((f) => f.key === entry.key ? { ...f, status: 'error', message: msg } : f)
          )
          setBidProcessStatuses((prev) => { const n = { ...prev }; delete n[entry.key]; return n })
        } else {
          setTenderFile((prev) => prev ? { ...prev, status: 'error', message: msg } : null)
          setTenderProcessStatus(null)
        }
      },
    )
  }

  const handleUploadAll = async () => {
    const pendingFiles = [
      ...(tenderFile?.status === 'pending' ? [tenderFile] : []),
      ...bidFiles.filter((f) => f.status === 'pending'),
    ]
    if (!pendingFiles.length) return
    setIsUploading(true)
    try {
      const uploaded = await Promise.all(pendingFiles.map(uploadOnly))

      // 文档门禁: 存在可疑文档时弹窗等待用户决策，解析暂不启动
      const flagged = uploaded.filter(
        (u) => u.gateResult && u.gateResult.verdict !== 'pass'
      )
      if (flagged.length > 0) {
        const clean = uploaded.filter((u) => !flagged.includes(u))
        setGatePending({ flagged, clean })
        return
      }

      await Promise.all(uploaded.map((u) => processAndPoll(u, false)))
    } catch (err) {
      toast.error(`上传失败: ${err instanceof Error ? err.message : '未知错误'}`)
    } finally {
      setIsUploading(false)
    }
  }

  // 门禁弹窗: 接受警告，全部继续解析(可疑文档带gate_ack留痕)
  const handleGateContinue = async () => {
    if (!gatePending) return
    const pending = gatePending
    setGateBusy(true)
    setIsUploading(true)
    try {
      setGatePending(null)
      await Promise.all([
        ...pending.flagged.map((u) => processAndPoll(u, true)),
        ...pending.clean.map((u) => processAndPoll(u, false)),
      ])
    } catch (err) {
      toast.error(`处理失败: ${err instanceof Error ? err.message : '未知错误'}`)
    } finally {
      setGateBusy(false)
      setIsUploading(false)
    }
  }

  // 门禁弹窗: 返回更换文档(: 阻断全过程——本批全部文档一并撤销,
  // 干净文件回到待上传态, 待用户补齐后重新点击"上传并处理", 使 MIN_BID
  // 门禁与"招标文件作为白名单"的意图重新生效; 禁止对任何文档发起解析)
  const handleGateReplace = async () => {
    if (!gatePending || !projectId) return
    const pending = gatePending
    const pid = parseInt(projectId)
    setGateBusy(true)
    try {
      setGatePending(null)
      const plan = buildGateReplacePlan(pending.flagged, pending.clean)

      await Promise.all(
        plan.deleteDocumentIds.map((documentId) =>
          documentService.deleteDocument(pid, documentId).catch(() => undefined)
        )
      )

      // 可疑文档: 从列表移除待更换
      if (plan.clearTender) {
        setTenderFile(null)
      }
      for (const key of plan.removeBidKeys) {
        removeBidFile(key)
      }

      // 干净文档: 回到待上传状态(下轮与更换后的文档一并重传)
      if (plan.resetTenderToPending) {
        setTenderFile((prev) => prev
          ? { ...prev, status: 'pending', documentId: undefined, message: undefined }
          : null)
      }
      if (plan.resetBidKeysToPending.length > 0) {
        const resetKeys = new Set(plan.resetBidKeysToPending)
        setBidFiles((prev) => prev.map((f) => resetKeys.has(f.key)
          ? { ...f, status: 'pending', documentId: undefined, message: undefined }
          : f))
      }

      toast.info('已撤销本次全部上传，请更换文档后重新点击"上传并处理"')
    } catch (err) {
      toast.error(`处理失败: ${err instanceof Error ? err.message : '未知错误'}`)
    } finally {
      setGateBusy(false)
      setIsUploading(false)
    }
  }

  const handleUploadAllClick = () => {
    if (!requestProcessTip()) {
      void handleUploadAll()
    }
  }

  const handleStartAnalysis = async () => {
    if (!projectId || !canStartAnalysis) return
    if (startInFlightRef.current) return
    startInFlightRef.current = true
    setIsStartingAnalysis(true)
    try {
      const latestProject = await projectService.getProject(parseInt(projectId))
      setProjectStatus(latestProject.status)
      setProjectErrorMessage(latestProject.errorMessage ?? null)
      if (['reviewing', 'completed', 'failed'].includes(latestProject.status)) {
        // 任务已在运行或已有结果：余额阻断态失去意义，清除后跳转
        gate.clear()
        navigate(
          latestProject.status === 'reviewing'
            ? `/project/${projectId}/collusion/progress`
            : `/project/${projectId}/collusion/result`
        )
        return
      }
      await collusionService.startAnalysis(parseInt(projectId))
      gate.clear()
      navigate(`/project/${projectId}/collusion/progress`)
    } catch (err) {
      // 余额 403 由共享 gate 接管（落状态+会话快照+单次 toast+充值弹窗），非余额错误保持原路径
      if (!gate.capture(err)) {
        toast.error(`启动失败: ${err instanceof Error ? err.message : '未知错误'}`)
      }
      setIsStartingAnalysis(false)
      startInFlightRef.current = false
    }
  }

  useEffect(() => {
    if (shouldAutoStartCollusionAnalysis({
      canStartAnalysis,
      isSomethingLoading,
      isUploading,
      projectStatus,
      isStartingAnalysis,
      analysisBlockedByBalance: gate.isBlocked,
    })) {
      handleStartAnalysis()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canStartAnalysis, isSomethingLoading, isUploading, projectStatus, isStartingAnalysis, gate.isBlocked])

  // ==================== 渲染辅助 ====================


  const getFileStatusIcon = (status: UploadedFile['status']) => {
    if (status === 'success') {
      return (
        <svg className="w-4 h-4 text-green-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
        </svg>
      )
    }
    if (status === 'error') {
      return (
        <svg className="w-4 h-4 text-red-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      )
    }
    if (status === 'uploading' || status === 'processing') {
      return <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-blue-500 flex-shrink-0" />
    }
    return (
      <svg className="w-4 h-4 text-gray-300 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
    )
  }

  const remainingBidSlots = MAX_BID - existingBids.length - bidFiles.filter((f) => f.status !== 'error').length

  return (
    <div>
      {/* 标题栏 */}
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
          <h1 className="text-2xl font-bold text-gray-900">串标审查 — 上传文件</h1>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-3">
          {projectId && (
            <KnowledgeUsagePanel
              projectId={parseInt(projectId)}
              flow="collusion_check"
            />
          )}
          {/* 下一步：上传页的下一步永远是进度页，与 FileUpload 同款样式 */}
          {(projectStatus === 'completed' || projectStatus === 'reviewing') && (
            <button
              onClick={() => navigate(`/project/${projectId}/collusion/progress`)}
              className="px-6 py-2 rounded-md text-white bg-green-500 hover:bg-green-600 flex items-center"
            >
              下一步
              <svg className="w-4 h-4 ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14 5l7 7m0 0l-7 7m7-7H3" />
              </svg>
            </button>
          )}
          {projectStatus !== 'completed' && projectStatus !== 'reviewing' && canStartAnalysis && (
            <button
              onClick={handleStartAnalysis}
              disabled={isStartingAnalysis}
              className={`px-6 py-2 rounded-md text-white flex items-center ${
                isStartingAnalysis ? 'bg-gray-400 cursor-not-allowed' : 'bg-green-500 hover:bg-green-600'
              }`}
            >
              {isStartingAnalysis ? '启动中...' : '下一步'}
              {!isStartingAnalysis && (
                <svg className="w-4 h-4 ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14 5l7 7m0 0l-7 7m7-7H3" />
                </svg>
              )}
            </button>
          )}
        </div>
      </div>

      {/* 项目状态提示横幅（非 completed 状态的提示，不强制跳转） */}
      {projectStatus === 'completed' && (
        <div className="mb-4 p-4 bg-green-50 border border-green-200 rounded-lg flex items-center justify-between">
          <div className="flex items-center gap-2">
            <svg className="w-4 h-4 text-green-600 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
            <p className="text-sm text-green-800 font-medium">串标分析已完成</p>
          </div>
          <button
            onClick={() => navigate(`/project/${projectId}/collusion/result`)}
            className="text-sm text-green-700 underline hover:text-green-900 flex-shrink-0 ml-4"
          >
            查看分析结果
          </button>
        </div>
      )}
      {projectStatus === 'reviewing' && (
        <div className="mb-4 p-4 bg-[#F6F1FC] border border-[#E3D5F5] rounded-lg flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-[#9056D8] flex-shrink-0" />
            <p className="text-sm text-[#6C40A2] font-medium">串标分析正在进行中</p>
          </div>
          <button
            onClick={() => navigate(`/project/${projectId}/collusion/progress`)}
            className="text-sm text-[#6C40A2] underline hover:text-[#412761] flex-shrink-0 ml-4"
          >
            查看分析进度
          </button>
        </div>
      )}
      {projectStatus === 'failed' && (
        <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg flex items-center justify-between gap-4">
          <div className="min-w-0">
            <p className="text-sm text-red-800 font-medium">上次分析失败</p>
            <p className="text-sm text-red-700 mt-1">
              原因: {formatFailureMessage(activeRetryState?.lastError || projectErrorMessage)}
            </p>
            <p className="text-xs text-red-600 mt-1">
              可查看失败结果，或重新上传文件后再次启动分析。
            </p>
          </div>
          <button
            onClick={() => navigate(`/project/${projectId}/collusion/result`)}
            className="text-sm text-red-700 underline hover:text-red-900 flex-shrink-0"
          >
            查看失败结果
          </button>
        </div>
      )}
      {gate.blocked && (
        <BalanceGateCard
          layout="inline"
          semantic="completed"
          copy={getBalanceGateCopy('collusion_start')}
          message={gate.blocked.message}
          paymentEnabled={isPaymentEnabled}
          continueBusy={isStartingAnalysis}
          onRecharge={gate.openRecharge}
          onContinue={() => { void handleStartAnalysis() }}
        />
      )}

      {activeRetryState && (
        <div className="max-w-2xl mx-auto">
          <RetryActionPanel
            state={activeRetryState}
            onRetry={handleRetryDocumentProcess}
            onRefresh={refreshRetryState}
            retrying={retrying}
            refreshing={refreshingRetry}
          />
        </div>
      )}

      <div className="max-w-2xl mx-auto">
      <div className="space-y-6">
        {/* 招标文件区（可选） */}
        <div className="bg-white rounded-lg shadow p-6">
          <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-base font-semibold text-gray-800">招标文件 (PDF/Word)</h2>
              <span className="text-xs text-blue-600 bg-blue-50 px-2 py-0.5 rounded font-medium">建议 · 1份</span>
              <FieldHintButton label={TENDER_RECOMMENDED_HINT}>{TENDER_RECOMMENDED_HINT}</FieldHintButton>
            </div>
            <SkipVlOption
              id="skip-tender-vl"
              checked={skipTenderVl}
              disabled={isUploading || isSomethingLoading}
              onChange={handleSkipTenderVlChange}
              label={IMAGE_PACE_LABEL}
              description={TENDER_IMAGE_PACE_HINT}
            />
          </div>

          {existingTender && !tenderFile ? (
            <div className={`border-2 border-dashed rounded-lg p-4 ${
              existingTender.parsed === 1 ? 'border-green-200 bg-green-50'
              : existingTender.parsed === -1 ? 'border-red-200 bg-red-50'
              : 'border-blue-200 bg-blue-50'
            }`}>
              <div className="flex items-start gap-3">
                {existingTender.parsed === 1
                  ? <svg className="w-5 h-5 text-green-500 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
                  : existingTender.parsed === -1
                  ? <svg className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
                  : <svg className="w-5 h-5 text-blue-500 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
                }
                <div className="flex-1 min-w-0">
                  <TruncatedHoverText text={existingTender.filename} className="block font-medium text-gray-800 text-sm truncate" />
                  <p className="text-xs text-gray-500 mt-0.5">
                    {formatFileSize(existingTender.file_size)} · {formatDate(existingTender.created_at)}
                  </p>
                  <p className="text-xs mt-0.5">
                    {existingTender.parsed === 1
                      ? <span className="text-green-600">已处理</span>
                      : existingTender.parsed === -1
                      ? <span className="text-red-600">处理失败</span>
                      : <span className="text-blue-600 flex items-center gap-1"><span className="animate-spin inline-block w-2.5 h-2.5 border-b border-blue-600 rounded-full" />处理中...</span>
                    }
                  </p>
                  {existingTender.parsed === -1 && (
                    <p className="text-xs text-red-700 mt-1">
                      原因: {resolveDocumentProcessFailureMessage(existingTender.lastError)}
                    </p>
                  )}
                  {existingTender.parsed === 0 && (
                    <FileProcessProgress accentColor="iris" processStatus={existingTenderProcessStatus} />
                  )}
                </div>
                <button
                  onClick={() => setExistingTender(null)}
                  className="text-xs text-blue-600 hover:text-blue-800 flex-shrink-0"
                  disabled={isUploading}
                >
                  更换
                </button>
              </div>
            </div>
          ) : tenderFile ? (
            <div className={`border-2 border-dashed rounded-lg p-4 ${
              tenderFile.status === 'success' ? 'border-green-200 bg-green-50'
              : tenderFile.status === 'error' ? 'border-red-200 bg-red-50'
              : 'border-blue-200 bg-blue-50'
            }`}>
              <div className="flex items-start gap-3">
                {getFileStatusIcon(tenderFile.status)}
                <div className="flex-1 min-w-0">
                  <TruncatedHoverText text={tenderFile.name} className="block font-medium text-gray-800 text-sm truncate" />
                  <p className="text-xs text-gray-500 mt-0.5">
                    {tenderFile.status === 'uploading' ? '上传中...'
                      : tenderFile.status === 'processing' ? '处理中...'
                      : tenderFile.status === 'success' ? '处理完成'
                      : tenderFile.status === 'error' ? tenderFile.message || '失败'
                      : '待上传'}
                  </p>
                  {tenderFile.status === 'processing' && <FileProcessProgress accentColor="iris" processStatus={tenderProcessStatus} />}
                </div>
                {tenderFile.status === 'pending' && (
                  <button
                    onClick={() => setTenderFile(null)}
                    className="text-xs text-gray-400 hover:text-red-500 flex-shrink-0"
                    disabled={isUploading}
                  >
                    移除
                  </button>
                )}
              </div>
            </div>
          ) : !isUploading && !isSomethingLoading ? (
            // Fix2: 上传/处理期间隐藏入口；Fix8: 统一样式与 FileUpload 一致
            <div
              className={`border-2 border-dashed rounded-lg p-6 transition-all duration-150 ${
                isDraggingTender ? 'border-primary bg-blue-50 scale-[1.01]' : 'border-gray-300 hover:border-blue-400'
              }`}
              onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; setIsDraggingTender(true) }}
              onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setIsDraggingTender(false) }}
              onDrop={handleTenderDrop}
            >
              {isDraggingTender ? (
                <>
                  <svg className="w-10 h-10 mx-auto mb-2 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                  </svg>
                  <p className="text-primary font-medium text-center">松开鼠标即可上传</p>
                </>
              ) : (
                <>
                  <svg className="w-10 h-10 mx-auto mb-2 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                  </svg>
                  <p className="text-gray-500 mb-3 text-center">将招标文件（PDF/Word）拖拽到此处，或</p>
                  <div className="flex justify-center">
                    <input type="file" accept={UPLOAD_ACCEPT} id="tender-file" className="hidden" onChange={handleTenderFileSelect} />
                    <label htmlFor="tender-file" className="inline-block px-4 py-2 rounded-md cursor-pointer bg-primary text-white hover:bg-blue-600 text-sm">
                      点击选择文件
                    </label>
                  </div>
                  <p className="text-xs text-gray-400 mt-2 text-center">{UPLOAD_FORMAT_HINT}</p>
                </>
              )}
            </div>
          ) : null}
        </div>

        {/* 投标文件区（必选，2-15份） */}
        <div className="bg-white rounded-lg shadow p-6">
          <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-base font-semibold text-gray-800">投标文件 (PDF/Word)</h2>
              <span className="text-xs text-blue-600 bg-blue-50 px-2 py-0.5 rounded font-medium">必选 · {MIN_BID}–{MAX_BID}份</span>
            </div>
            <SkipVlOption
              id="skip-bid-vl"
              checked={skipBidVl}
              disabled={isUploading || isSomethingLoading}
              onChange={handleSkipBidVlChange}
              label={IMAGE_PACE_LABEL}
              description={BID_IMAGE_PACE_HINT}
            />
          </div>

          {/* 文件列表（支持滚动） */}
          {(existingBids.length > 0 || bidFiles.length > 0) && (
            <div className={`space-y-2 mb-3 ${existingBids.length + bidFiles.length > 8 ? 'max-h-[400px] overflow-y-auto pr-1' : ''}`}>
              {/* 已有文件列表 */}
              {existingBids.length > 0 && (
                <div className="space-y-2">
                  {existingBids.map((doc) => (
                    <div key={doc.id} className={`flex items-start gap-3 p-3 border rounded-lg ${
                      doc.parsed === 1 ? 'bg-green-50 border-green-200'
                      : doc.parsed === -1 ? 'bg-red-50 border-red-200'
                      : 'bg-blue-50 border-blue-200'
                    }`}>
                      {doc.parsed === 1
                        ? <svg className="w-4 h-4 text-green-500 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
                        : doc.parsed === -1
                        ? <svg className="w-4 h-4 text-red-500 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
                        : <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-blue-500 flex-shrink-0 mt-0.5" />
                      }
                      <div className="flex-1 min-w-0">
                        <TruncatedHoverText text={doc.filename} className="block text-sm text-gray-800 truncate" />
                        <p className="text-xs text-gray-500">
                          {formatFileSize(doc.file_size)} · {doc.parsed === 1 ? '已处理' : doc.parsed === -1 ? '处理失败' : '处理中...'}
                        </p>
                        {doc.parsed === -1 && (
                          <p className="text-xs text-red-700 mt-1">
                            原因: {resolveDocumentProcessFailureMessage(doc.lastError)}
                          </p>
                        )}
                        {doc.parsed === 0 && (
                          <FileProcessProgress accentColor="iris" processStatus={existingBidProcessStatuses[doc.id] || null} />
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* 新增文件列表 */}
              {bidFiles.length > 0 && (
                <div className="space-y-2">
                  {bidFiles.map((f) => (
                    <div key={f.key} className={`flex items-start gap-3 p-3 border rounded-lg ${
                      f.status === 'success' ? 'bg-green-50 border-green-200'
                      : f.status === 'error' ? 'bg-red-50 border-red-200'
                      : 'bg-gray-50 border-gray-200'
                    }`}>
                      {getFileStatusIcon(f.status)}
                      <div className="flex-1 min-w-0">
                        <TruncatedHoverText text={f.name} className="block text-sm text-gray-800 truncate" />
                        <p className="text-xs text-gray-500">
                          {f.status === 'uploading' ? '上传中...'
                            : f.status === 'processing' ? '处理中...'
                            : f.status === 'success' ? '处理完成'
                            : f.status === 'error' ? f.message || '失败'
                            : '待上传'}
                        </p>
                        {f.status === 'processing' && <FileProcessProgress accentColor="iris" processStatus={bidProcessStatuses[f.key] || null} />}
                      </div>
                      {f.status === 'pending' && (
                        <button
                          onClick={() => removeBidFile(f.key)}
                          className="text-xs text-gray-400 hover:text-red-500 flex-shrink-0"
                          disabled={isUploading}
                        >
                          移除
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Fix2: 上传/处理期间隐藏入口；Fix8: 统一样式 */}
          {remainingBidSlots > 0 && !isUploading && !isSomethingLoading && (
            <div
              className={`border-2 border-dashed rounded-lg p-6 transition-all duration-150 ${
                isDraggingBid ? 'border-primary bg-blue-50 scale-[1.01]' : 'border-gray-300 hover:border-blue-400'
              }`}
              onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; setIsDraggingBid(true) }}
              onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setIsDraggingBid(false) }}
              onDrop={handleBidDrop}
            >
              {isDraggingBid ? (
                <>
                  <svg className="w-10 h-10 mx-auto mb-2 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                  </svg>
                  <p className="text-primary font-medium text-center">松开鼠标即可上传</p>
                </>
              ) : (
                <>
                  <svg className="w-10 h-10 mx-auto mb-2 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                  </svg>
                  <p className="text-gray-500 mb-3 text-center">
                    将投标文件（PDF/Word）拖拽到此处，或
                    {remainingBidSlots < MAX_BID && <span className="text-gray-400 text-xs ml-1">（还可添加 {remainingBidSlots} 份）</span>}
                  </p>
                  <div className="flex justify-center">
                    <input
                      type="file" accept={UPLOAD_ACCEPT} id="bid-files" className="hidden"
                      multiple onChange={handleBidFilesSelect}
                    />
                    <label htmlFor="bid-files" className="inline-block px-4 py-2 rounded-md cursor-pointer bg-primary text-white hover:bg-blue-600 text-sm">
                      点击选择文件
                    </label>
                  </div>
                  <p className="text-xs text-gray-400 mt-2 text-center">{UPLOAD_FORMAT_HINT}，可多选</p>
                </>
              )}
            </div>
          )}

          {/* 数量提示: reviewing/completed 状态下已无上传需求，隐藏计数器 */}
          {projectStatus !== 'reviewing' && projectStatus !== 'completed' && (
            <>
              <div className="mt-3 flex items-center gap-2">
                <div className={`h-2 rounded-full flex-1 bg-gray-200 overflow-hidden`}>
                  <div
                    className={`h-full rounded-full transition-all ${
                      totalBidCount < MIN_BID ? 'bg-red-400' : 'bg-green-500'
                    }`}
                    style={{ width: `${Math.min((totalBidCount / MAX_BID) * 100, 100)}%` }}
                  />
                </div>
                <span className={`text-xs font-medium ${totalBidCount < MIN_BID ? 'text-red-600' : 'text-green-600'}`}>
                  {totalBidCount}/{MAX_BID} 份
                </span>
              </div>
              {totalBidCount < MIN_BID && (
                <p className="text-xs text-red-500 mt-1">至少需要 {MIN_BID} 份投标文件才能开始分析</p>
              )}
            </>
          )}
        </div>

        {/* 操作按钮区：仅保留上传并处理，处理完成后自动启动分析 */}
        <div className="flex justify-end items-center">
          {hasPendingFiles && (
            <button
              onClick={handleUploadAllClick}
              disabled={!canUploadAndProcess}
              title={
                totalBidCount < MIN_BID
                  ? `至少需要 ${MIN_BID} 份投标文件才能开始上传`
                  : undefined
              }
              className={`px-5 py-2 rounded-md text-white text-sm ${
                !canUploadAndProcess
                  ? 'bg-gray-400 cursor-not-allowed'
                  : 'bg-blue-500 hover:bg-blue-600'
              }`}
            >
              {isUploading ? '上传中...' : '上传并处理'}
            </button>
          )}
        </div>
      </div>

      <p className="mt-3 text-xs text-muted-foreground">
        全部处理完后自动开始分析，通常需要 5–15 分钟。
      </p>
      </div>

      <Modal
        visible={showProcessTip}
        title="处理时间说明"
        maskClosable={false}
        footer={
          <button
            onClick={() => {
              dismissProcessTip()
              void handleUploadAll()
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

      <DuplicateWarningDialog
        open={showDuplicateWarning}
        duplicateNames={duplicateWarningData?.duplicateNames ?? []}
        onConfirm={handleConfirmDuplicate}
        onCancel={handleCancelDuplicate}
      />

      <DocGateWarningDialog
        open={gatePending != null}
        items={(gatePending?.flagged ?? []).map((u) => ({
          filename: u.name,
          gateResult: u.gateResult!,
        }))}
        busy={gateBusy}
        onContinue={handleGateContinue}
        onReplace={handleGateReplace}
      />
    </div>
  )
}

export default CollusionUploadPage
