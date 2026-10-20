/**
 * 职责: 执行投标审查单槽位失败文档更换（上传 + 解析 + 刷新文档列表）
 * 不做: 不管理 React 状态容器本身以外的页面路由/半成品重置/信封齐套判定
 */
import type { DocGateResult } from '@/services/documentApi'

/** 更换槽位描述: 槽位 id + 上传所需的 docType/bidPart */
export interface ReplaceSlotDescriptor {
  id: string
  docType: 'tender' | 'bid'
  /** 投标分卷角色(招标与整本为 null) */
  bidPart: string | null
}

/** 与页面 EnvelopeUploadedFile.status 对齐，禁止用 string 以免 setState 无法传入 tsc */
export type ReplaceUploadFileStatus =
  | 'pending'
  | 'uploading'
  | 'processing'
  | 'success'
  | 'error'

export interface ReplaceUploadFile {
  file: File
  name: string
  status: ReplaceUploadFileStatus
  documentId?: number
  message?: string
}

export interface ExistingDocumentSnapshot {
  id: number
  filename: string
  file_size: number
  created_at: string
  parsed: number
  bid_part?: string | null
  lastError?: string
}

export interface RunReplaceFailedUploadDeps {
  projectId: number
  slot: ReplaceSlotDescriptor
  file: ReplaceUploadFile
  skipVlForTender: boolean
  uploadFile: (
    projectId: number,
    file: File,
    docType: 'tender' | 'bid',
    bidPart?: string | null,
  ) => Promise<{ data?: { id?: number; gate_result?: DocGateResult | null } }>
  processDocument: (
    projectId: number,
    documentId: number,
    skipVl?: boolean,
    gateAck?: boolean,
  ) => Promise<unknown>
  getDocuments: (projectId: number) => Promise<{
    data?: {
      tender?: ExistingDocumentSnapshot
      bid?: ExistingDocumentSnapshot
      bids?: ExistingDocumentSnapshot[]
    }
  }>
  getProjectStatus: (projectId: number) => Promise<string>
  startPolling: (
    slotId: string,
    documentId: number,
  ) => Promise<void>
  setFileStatus: (
    updater: (prev: ReplaceUploadFile | null) => ReplaceUploadFile | null,
  ) => void
}

export interface RunReplaceFailedUploadResult {
  tender: ExistingDocumentSnapshot | null
  /** 全部投标文档(整本 1 条 / 分卷 N 条); 齐套判定由调用方按信封契约执行 */
  bids: ExistingDocumentSnapshot[]
  projectStatus: string
}

export async function runReplaceFailedUpload(
  deps: RunReplaceFailedUploadDeps,
): Promise<RunReplaceFailedUploadResult> {
  const {
    projectId,
    slot,
    file,
    skipVlForTender,
    uploadFile,
    processDocument,
    getDocuments,
    getProjectStatus,
    startPolling,
    setFileStatus,
  } = deps

  setFileStatus((prev) => (prev ? { ...prev, status: 'uploading' } : null))
  const uploadResponse = await uploadFile(
    projectId, file.file, slot.docType, slot.bidPart,
  )
  const documentId = uploadResponse.data?.id
  if (!documentId) {
    throw new Error('上传响应中没有文档ID')
  }

  setFileStatus((prev) =>
    prev
      ? { ...prev, status: 'processing', documentId, message: '正在处理...' }
      : null,
  )

  const skipVl = slot.docType === 'tender' ? skipVlForTender : false
  await processDocument(projectId, documentId, skipVl, false)
  await startPolling(slot.id, documentId)

  const docs = await getDocuments(projectId)
  const tender = docs.data?.tender ?? null
  const bids =
    docs.data?.bids ?? (docs.data?.bid ? [docs.data.bid] : [])
  const projectStatus = await getProjectStatus(projectId)

  return {
    tender,
    bids,
    projectStatus,
  }
}
