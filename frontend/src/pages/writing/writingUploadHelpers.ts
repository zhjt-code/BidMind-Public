/**
 * WritingUpload 纯函数辅助 + 类型 + 常量。
 *
 * 按 R2-9 §1.1 决议（Q1=B，WritingUpload 不强行套通用 hook），仅把 6 个
 * 状态判定纯函数 + 1 个 UploadStage 类型 + 3 个常量数组从 WritingUpload.tsx
 * 内部辅助迁出本文件，便于独立 node:test 覆盖；pollUploadStatus 主体保留
 * 在 WritingUpload，规避 教训。
 *
 * 0 React/service 依赖，纯数据处理。
 */

/** 标书上传页 8 个 UI 阶段状态机（与原 WritingUpload.tsx 内联定义 byte-equal） */
export type UploadStage =
  | 'idle'
  | 'uploading'
  | 'processing'
  | 'extracting'
  | 'awaiting_extract'
  | 'done'
  | 'error'
  | 'already_done'

/** 仅 idle 可展示推进方式；处理进度、完成回看、失败均隐藏。 */
export function isWritingUploadChooserStage(stage: UploadStage): boolean {
  return stage === 'idle'
}

/**
 * 标书上传期 writingService.getStatus().currentStage 的 5 个值；当其落在此
 * 集合时，refreshRetryState 选择 writing_tender_process 节点而非 writing_extract。
 */
export const WRITING_TENDER_PROCESS_STAGES = [
  'parsing',
  'vl_recognition',
  'chunking',
  'vectorizing',
  'doc_completed',
] as const

/**
 * 上传页"已完成"判定的 projectStatus 集合；任一命中表示约束提取已完成或
 * 处于后续阶段，UI 应直接进入 'already_done' 跳转到 clauses 页。
 */
export const WRITING_DONE_PROJECT_STATUSES = [
  'extracted',
  'outlining',
  'outlined',
  'matching',
  'matched',
  'writing',
  'composed',
  'formatting',
  'completed',
] as const

/**
 * 上传期 projectStatus 集合，命中时 resumeUploadPoll 应恢复轮询。
 */
export const RESUME_UPLOAD_PROJECT_STATUSES = ['uploading', 'parsing'] as const

/** 与 pollUploadStatus / refreshRetryState 等共享的状态快照子集 */
export interface UploadStatusFacts {
  status?: string
  overallStatus?: string
  currentStage?: string
  overallProgress?: number
}

/**
 * 当前 currentStage 是否属于 writing_tender_process 节点的轮询区间。
 *
 * refreshRetryState 用此判定来决定查询哪个 retry node。
 */
export function isWritingTenderProcessStage(
  currentStage: string | null | undefined,
): boolean {
  if (!currentStage) return false
  return (WRITING_TENDER_PROCESS_STAGES as readonly string[]).includes(currentStage)
}

/**
 * projectStatus 是否表示约束提取已完成（或处于后续编写阶段）。
 *
 * WritingUpload useEffect 用此判定来决定是否直接进入 'already_done'。
 */
export function isWritingDoneProjectStatus(
  projectStatus: string | null | undefined,
): boolean {
  if (!projectStatus) return false
  return (WRITING_DONE_PROJECT_STATUSES as readonly string[]).includes(projectStatus)
}

/**
 * projectStatus 是否需要 resumeUploadPoll 接管（uploading / parsing）。
 */
export function isResumableUploadProjectStatus(
  projectStatus: string | null | undefined,
): boolean {
  if (!projectStatus) return false
  return (RESUME_UPLOAD_PROJECT_STATUSES as readonly string[]).includes(projectStatus)
}

/**
 * 是否应自动从"上传完成"切换到"提取约束"阶段。
 *
 * pollUploadStatus 内部判定：文档已就绪则自动 triggerExtract。
 * 就绪：status='parsed'，或兼容存量 status='uploading' 且
 * （currentStage='doc_completed' 或 overallProgress >= 50）。
 *
 * blockedByBalance 为 true 时（余额门禁阻断，含刷新后会话快照恢复的阻断）
 * 一律不自动提取，等待用户充值后手动继续 。
 */
export function shouldAutoTriggerExtract(
  raw: UploadStatusFacts,
  blockedByBalance = false,
): boolean {
  if (blockedByBalance) return false
  if (raw.status === 'parsed') return true
  return (
    raw.status === 'uploading' &&
    (raw.currentStage === 'doc_completed' ||
      (typeof raw.overallProgress === 'number' && raw.overallProgress >= 50))
  )
}

export function isAwaitingExtractProjectStatus(
  projectStatus: string | null | undefined,
): boolean {
  return projectStatus === 'parsed'
}

/** 轮询命中"提取已完成"终态：raw.status === 'extracted' */
export function isPollUploadExtracted(raw: UploadStatusFacts): boolean {
  return raw.status === 'extracted'
}

/**
 * 轮询命中"失败"终态：三选一命中
 * - raw.status === 'failed'
 * - raw.overallStatus === 'failed'
 * - raw.currentStage === 'failed'
 */
export function isPollUploadFailed(raw: UploadStatusFacts): boolean {
  return (
    raw.status === 'failed' ||
    raw.overallStatus === 'failed' ||
    raw.currentStage === 'failed'
  )
}

const IMAGE_RECOGNITION_LABELS = new Set(['图片识别', 'VL图片识别'])

export function omitSkippedImageRecognitionSteps<T extends { label?: string }>(
  steps: T[] | undefined,
  skipped: boolean,
): T[] | undefined {
  if (!skipped || !steps) return steps
  return steps.filter((step) => !IMAGE_RECOGNITION_LABELS.has(step.label || ''))
}
