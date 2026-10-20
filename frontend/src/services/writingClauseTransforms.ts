export interface WritingClause {
  id: number
  projectId: number
  documentId: number
  layer: string
  subType: string
  content: string
  rawText: string | null
  /**
   * 默认打开 PDF 的锚点页 (批次首页 / LLM 精确页);
   * 不承担"精确命中页"的展示职责。
   */
  sourcePage: number | null
  /** 批次页范围下界 (面向用户展示的诚实范围) */
  sourcePageStart: number | null
  /** 批次页范围上界 (面向用户展示的诚实范围) */
  sourcePageEnd: number | null
  scoreWeight: number | null
  confirmed: number
  isManual: number
  sortOrder: number
  /** 结构化补充信息(评分项名/满分/卷权重/关联标记等)，来源 metadata_json */
  metadataJson: Record<string, unknown> | null
}

export type TraceabilityStatus = 'has_excerpt' | 'locatable_only' | 'no_evidence'

function toOptionalInt(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v
  return null
}

export function transformWritingClause(raw: Record<string, unknown>): WritingClause {
  const rawTextValue = raw.raw_text
  const rawText =
    typeof rawTextValue === 'string' && rawTextValue.trim() !== ''
      ? rawTextValue
      : null
  const sourcePage = toOptionalInt(raw.source_page)
  let sourcePageStart = toOptionalInt(raw.source_page_start)
  let sourcePageEnd = toOptionalInt(raw.source_page_end)
  // 老数据兼容: 若后端未返回 start/end, 回退为 source_page 的单页范围;
  // 避免前端在过渡期出现 "P?-P?" 之类的空范围展示。
  if (sourcePageStart == null && sourcePageEnd == null && sourcePage != null) {
    sourcePageStart = sourcePage
    sourcePageEnd = sourcePage
  } else if (sourcePageStart == null && sourcePageEnd != null) {
    sourcePageStart = sourcePageEnd
  } else if (sourcePageEnd == null && sourcePageStart != null) {
    sourcePageEnd = sourcePageStart
  }
  return {
    id: raw.id as number,
    projectId: raw.project_id as number,
    documentId: raw.document_id as number,
    layer: raw.layer as string,
    subType: raw.sub_type as string,
    content: raw.content as string,
    rawText,
    sourcePage,
    sourcePageStart,
    sourcePageEnd,
    scoreWeight: (raw.score_weight as number) ?? null,
    confirmed: raw.confirmed as number,
    isManual: (raw.is_manual as number) ?? 0,
    sortOrder: raw.sort_order as number,
    metadataJson:
      raw.metadata_json && typeof raw.metadata_json === 'object'
        ? (raw.metadata_json as Record<string, unknown>)
        : null,
  }
}

/**
 * 计算约束条款的溯源状态。
 *
 * - has_excerpt: 有原文摘录，可直接展示
 * - locatable_only: 无摘录但有页码/页范围，可跳转 PDF 定位
 * - no_evidence: 两者皆无，无法溯源
 */
export function getTraceabilityStatus(clause: WritingClause): TraceabilityStatus {
  if (clause.rawText && clause.rawText.trim() !== '') return 'has_excerpt'
  if (
    clause.sourcePage != null
    || clause.sourcePageStart != null
    || clause.sourcePageEnd != null
  ) {
    return 'locatable_only'
  }
  return 'no_evidence'
}

/**
 * 生成面向用户的页码/页范围标签。
 *
 * - 单页: "P9"
 * - 跨页: "P9-P10"
 * - 仅 source_page: 退化为单页
 * - 均缺失: 返回空串
 */
export function getClausePageLabel(clause: WritingClause): string {
  const start = clause.sourcePageStart ?? clause.sourcePage
  const end = clause.sourcePageEnd ?? clause.sourcePage ?? start
  if (start == null) return ''
  if (end == null || start === end) return `P${start}`
  return `P${start}-P${end}`
}

/**
 * 生成面向用户的"第 N 页"展开文案。
 *
 * - 单页: "第 9 页"
 * - 跨页: "第 9-10 页"
 * - 均缺失: 返回空串
 */
export function getClausePageText(clause: WritingClause): string {
  const start = clause.sourcePageStart ?? clause.sourcePage
  const end = clause.sourcePageEnd ?? clause.sourcePage ?? start
  if (start == null) return ''
  if (end == null || start === end) return `第 ${start} 页`
  return `第 ${start}-${end} 页`
}

/**
 * 返回点击"定位原文"时应打开的 PDF 起始页。
 * 优先 source_page, 其次 source_page_start; 两者都无则返回 null。
 */
export function getClauseJumpPage(clause: WritingClause): number | null {
  return clause.sourcePage ?? clause.sourcePageStart ?? null
}

/** 是否为跨页批次 */
export function isMultiPageClause(clause: WritingClause): boolean {
  const start = clause.sourcePageStart ?? clause.sourcePage
  const end = clause.sourcePageEnd ?? clause.sourcePage
  if (start == null || end == null) return false
  return end > start
}
