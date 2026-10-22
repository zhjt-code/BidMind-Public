/**
 * 职责: 投标审查上传页模式判定（含单侧更换、投标信封分卷）
 * 不做: 不发起上传/解析请求，不渲染 UI
 */
export interface BidReviewUploadDocState {
  exists: boolean
  parsed: number | null
}

/** 投标侧信封聚合态(整本=1 槽, 分卷=N 槽, 由页面按槽位聚合) */
export interface BidEnvelopeSideState {
  /** 任一投标文档已存在(含刚落库未解析) */
  anyDoc: boolean
  /** 信封齐套(整本 1 份 或 分卷各 1 份) */
  complete: boolean
  /** 信封内全部文档 parsed=1 */
  allParsed: boolean
}

export type BidReviewUploadRouteTarget = 'requirements' | 'progress' | 'result'

export type BidReviewUploadPageMode =
  | 'upload'
  | 'processing'
  | 'replacing'
  | 'recover_half_upload'
  | 'failed_recovery'
  | 'redirect'

/** 更换槽位 id: 'tender' | 'integral' | 'commercial' | 'technical' */
export type BidReviewReplaceSlot = string

export interface BidReviewUploadPageInput {
  projectStatus: string | null
  tender: BidReviewUploadDocState
  bid: BidEnvelopeSideState
  isUploading: boolean
  isSomethingLoading: boolean
  /** : 单侧更换时不得误判为半成品 */
  replacingSlot?: BidReviewReplaceSlot | null
  runMode?: string | null
}

export interface BidReviewUploadPageState {
  mode: BidReviewUploadPageMode
  canShowUploadInputs: boolean
  targetRoute?: BidReviewUploadRouteTarget
}

const REQUIREMENT_STATUSES = new Set(['parsed', 'extracting', 'extracted'])

function hasDocumentSet(
  tender: BidReviewUploadDocState,
  bid: BidEnvelopeSideState,
): boolean {
  return tender.exists && bid.complete
}

/** 半成品: 已有部分文档但未齐套(缺招标 / 缺整本 / 分卷缺卷, 同形态) */
function hasPartialSet(
  tender: BidReviewUploadDocState,
  bid: BidEnvelopeSideState,
): boolean {
  return (tender.exists || bid.anyDoc) && !hasDocumentSet(tender, bid)
}

function isParsed(doc: BidReviewUploadDocState): boolean {
  return doc.exists && doc.parsed === 1
}

export function bidReviewNextRouteTarget(runMode?: string | null): BidReviewUploadRouteTarget {
  return runMode === 'express' ? 'progress' : 'requirements'
}

export function bidReviewPostProcessPath(projectId: string, runMode?: string | null): string {
  return `/project/${projectId}/${bidReviewNextRouteTarget(runMode)}`
}

/** 仅普通上传选择态展示推进方式；处理进度、更换、恢复、跳转均隐藏。 */
export function isBidReviewUploadChooserMode(mode: BidReviewUploadPageMode): boolean {
  return mode === 'upload'
}

export function deriveBidReviewUploadPageState(
  input: BidReviewUploadPageInput,
): BidReviewUploadPageState {
  const {
    projectStatus,
    tender,
    bid,
    isUploading,
    isSomethingLoading,
    replacingSlot = null,
    runMode = 'guided',
  } = input
  const confirmTarget = bidReviewNextRouteTarget(runMode)

  if (projectStatus === 'reviewing') {
    return { mode: 'redirect', canShowUploadInputs: false, targetRoute: 'progress' }
  }
  if (projectStatus === 'completed') {
    return { mode: 'redirect', canShowUploadInputs: false, targetRoute: 'result' }
  }
  if (projectStatus && REQUIREMENT_STATUSES.has(projectStatus)) {
    return { mode: 'redirect', canShowUploadInputs: false, targetRoute: confirmTarget }
  }

  if (isUploading) {
    return { mode: 'processing', canShowUploadInputs: false }
  }

  // 单侧更换：保留其余已有文档，允许本槽位重新选择并上传
  if (replacingSlot) {
    return { mode: 'replacing', canShowUploadInputs: true }
  }

  if (hasPartialSet(tender, bid)) {
    return { mode: 'recover_half_upload', canShowUploadInputs: false }
  }

  const hasSet = hasDocumentSet(tender, bid)
  const setParsed = isParsed(tender) && bid.allParsed
  if (hasSet && setParsed && !isUploading && !isSomethingLoading) {
    return { mode: 'redirect', canShowUploadInputs: false, targetRoute: confirmTarget }
  }

  if (hasSet || isSomethingLoading || isUploading) {
    return { mode: 'processing', canShowUploadInputs: false }
  }

  if (projectStatus === 'failed') {
    return { mode: 'failed_recovery', canShowUploadInputs: false }
  }

  return { mode: 'upload', canShowUploadInputs: true }
}
