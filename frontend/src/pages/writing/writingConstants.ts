/**
 * 标书编写模块 -- 状态顺序定义(单一数据源)
 *
 * useWritingGuard / WritingClauses / 其他需要状态比较的位置统一引用此文件，
 * 避免多处维护同一套映射导致遗漏或不一致。
 */

export const WRITING_STATUS_ORDER: Record<string, number> = {
  created: 0,
  uploading: 1,
  parsing: 2,
  parsed: 3,
  extracting: 4,
  extracted: 5,
  outlining: 6,
  outlined: 7,
  matching: 8,
  matched: 9,
  writing: 10,
  composed: 11,
  formatting: 12,
  completed: 13,
  failed: -1,
}

export const WRITING_PAGE_MIN_STATUS = {
  materials: 'outlined',
  binding: 'outlined',
} as const

export const WRITING_EXPRESS_PIPELINE_STATUSES = new Set([
  'extracted',
  'outlining',
  'outlined',
  'matching',
  'matched',
  'writing',
])

export function isWritingExpressPipeline(
  status: string,
  runMode?: string | null,
): boolean {
  return runMode === 'express' && (
    WRITING_EXPRESS_PIPELINE_STATUSES.has(status) || status === 'failed'
  )
}

export function getWritingProjectRoute(
  projectId: number,
  status: string,
  runMode: string = 'guided',
): string {
  if (isWritingExpressPipeline(status, runMode)) {
    return `/project/${projectId}/writing/progress`
  }
  switch (status) {
    case 'created':
    case 'uploading':
    case 'parsing':
    case 'parsed':
    case 'extracting':
      return `/project/${projectId}/writing/upload`
    case 'extracted':
      return `/project/${projectId}/writing/clauses`
    case 'outlining':
    case 'outlined':
      return `/project/${projectId}/writing/outline`
    case 'matching':
    case 'matched':
      return `/project/${projectId}/writing/binding`
    case 'writing':
      return `/project/${projectId}/writing/compose`
    case 'composed':
    case 'formatting':
    case 'completed':
      return `/project/${projectId}/writing/preview`
    case 'failed':
      return `/project/${projectId}/writing/upload`
    default:
      return `/project/${projectId}/writing/upload`
  }
}

/**
 * 判断 status 是否 >= target 所代表的阶段。
 * failed 始终返回 false（不视为任何有效阶段的后续）。
 */
export function isAtLeast(status: string | null | undefined, target: string): boolean {
  if (!status) return false
  const cur = WRITING_STATUS_ORDER[status]
  const tgt = WRITING_STATUS_ORDER[target]
  if (cur == null || tgt == null) return false
  if (cur < 0) return false
  return cur >= tgt
}
