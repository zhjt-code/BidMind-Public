/**
 * 职责: 文档质检 DTO 转换、十类到五组映射、标签、默认排序、筛选与计数、质检范围说明
 * 不做: 不发请求、不渲染
 */

import type {
  DocumentQualityData,
  QaEvidence,
  QaFilterCounts,
  QaFilterState,
  QaFinding,
  QaGroupKey,
  QaScanDocument,
} from './types'

// ── 分组与标签 ───────────────────────────────────────────────────────────────

export const CATEGORY_GROUP_MAP: Record<string, QaGroupKey> = {
  structure: 'structure_nav',
  toc: 'structure_nav',
  numbering: 'structure_nav',
  reference: 'structure_nav',
  language: 'content_accuracy',
  consistency: 'content_accuracy',
  duplication: 'duplication_hygiene',
  hygiene: 'duplication_hygiene',
  layout: 'layout',
  integrity: 'page_objects',
}

export const QA_GROUP_LABELS: Record<QaGroupKey, string> = {
  structure_nav: '结构与导航',
  content_accuracy: '内容准确性',
  duplication_hygiene: '重复与残留',
  layout: '版式一致性',
  page_objects: '页面与对象',
}

export const QA_GROUP_ORDER: QaGroupKey[] = [
  'structure_nav', 'content_accuracy', 'duplication_hygiene', 'layout', 'page_objects',
]

/** 十类展示序: 跟随五组, 同组内稳定; 用于同严重度聚簇, 禁止按字母序 */
export const QA_CATEGORY_ORDER = [
  'structure', 'toc', 'numbering', 'reference',
  'language', 'consistency',
  'duplication', 'hygiene',
  'layout',
  'integrity',
] as const

export const QA_CATEGORY_LABELS: Record<string, string> = {
  structure: '结构',
  toc: '目录',
  layout: '版式',
  language: '语言',
  duplication: '重复',
  numbering: '编号',
  consistency: '一致性',
  reference: '引用',
  hygiene: '文档卫生',
  integrity: '页面完整性',
}

export const QA_SEVERITY_LABELS: Record<string, string> = {
  P0: '严重', P1: '高', P2: '中', P3: '低',
}

export const QA_SEVERITY_ORDER = ['P0', 'P1', 'P2', 'P3'] as const

export const QA_CERTAINTY_LABELS: Record<string, string> = {
  certain: '确定',
  probable: '较可信',
  possible: '疑似',
  unknown: '待判断',
}

export const QA_DISPOSITION_LABELS: Record<string, string> = {
  pending: '待处理',
  confirmed: '确认问题',
  false_positive: '误报',
  ignored: '已忽略',
}

export const QA_SCAN_STATUS_LABELS: Record<string, string> = {
  completed: '质检完成',
  degraded: '质检完成(部分检查降级)',
  failed: '质检失败',
  building: '质检进行中',
  missing: '暂无质检结果',
}

export function groupOfCategory(category: string): QaGroupKey {
  return CATEGORY_GROUP_MAP[category] ?? 'page_objects'
}

// ── 质检范围说明 ─────────────────────────────────────────────────────────────

export const QA_IMAGE_PAGE_HINT =
  '质检只检查文件里可读取的文字。扫描件和图片（如证照、盖章页）视为已定稿的正式材料，'
  + '不做编制质量检查。文字被转成图形的页面也按图片处理。'

export interface QaImagePageNotice {
  documentId: number
  text: string
}

/** 每份文件的扫描件/图片页说明; 多份文件(分卷)时前缀卷名 */
export function imagePageNotices(
  documents: QaScanDocument[],
  partLabel: (part: string) => string,
): QaImagePageNotice[] {
  const multiple = documents.length > 1
  return documents
    .filter((doc) => (doc.imagePageCount ?? 0) > 0)
    .map((doc) => {
      const prefix = multiple && doc.bidPart ? `${partLabel(doc.bidPart)}：` : ''
      return {
        documentId: doc.documentId,
        text: `${prefix}其中 ${doc.imagePageCount} 页为扫描件或图片，不在质检范围内`,
      }
    })
}

// ── DTO 转换 ────────────────────────────────────────────────────────────────

function toEvidence(raw: unknown): QaEvidence {
  const data = (raw ?? {}) as Record<string, unknown>
  const items = Array.isArray(data.items) ? data.items : []
  return {
    kind: (data.kind as string) || 'single',
    items: items.map((item) => {
      const record = (item ?? {}) as Record<string, unknown>
      const bbox = Array.isArray(record.bbox) && record.bbox.length === 4
        ? (record.bbox.map(Number) as [number, number, number, number])
        : null
      return {
        documentId: (record.document_id as number | null) ?? null,
        bidPart: (record.bid_part as string | null) ?? null,
        page: (record.page as number | null) ?? null,
        excerpt: String(record.excerpt ?? ''),
        bbox,
        role: String(record.role ?? 'primary'),
      }
    }),
    expected: (data.expected as string | null) ?? null,
    actual: (data.actual as string | null) ?? null,
  }
}

export function transformFinding(raw: Record<string, unknown>): QaFinding {
  return {
    id: raw.id as number,
    ruleCode: String(raw.rule_code ?? ''),
    category: String(raw.category ?? ''),
    severity: String(raw.severity ?? ''),
    certainty: String(raw.certainty ?? ''),
    title: String(raw.title ?? ''),
    message: String(raw.message ?? ''),
    suggestion: (raw.suggestion as string | null) ?? null,
    evidence: toEvidence(raw.evidence),
    documentId: (raw.document_id as number | null) ?? null,
    disposition: String(raw.disposition ?? 'pending'),
    dispositionNote: (raw.disposition_note as string | null) ?? null,
    version: (raw.version as number) ?? 0,
    fingerprint: String(raw.fingerprint ?? ''),
  }
}

export function transformDocumentQuality(
  raw: Record<string, unknown>,
): DocumentQualityData {
  const scan = (raw.scan ?? {}) as Record<string, unknown>
  const summary = (raw.summary ?? {}) as Record<string, unknown>
  const documents = Array.isArray(scan.documents) ? scan.documents : []
  const checkers = Array.isArray(scan.checkers) ? scan.checkers : []
  const findings = Array.isArray(raw.findings) ? raw.findings : []
  return {
    scan: {
      status: String(scan.status ?? 'missing'),
      rulesetVersion: (scan.ruleset_version as string | null) ?? null,
      generatedAt: (scan.generated_at as string | null) ?? null,
      documents: documents.map((doc) => {
        const record = (doc ?? {}) as Record<string, unknown>
        return {
          documentId: record.document_id as number,
          bidPart: (record.bid_part as string | null) ?? null,
          filename: String(record.filename ?? ''),
          pageCount: (record.page_count as number | null) ?? null,
          status: String(record.status ?? 'missing'),
          imagePageCount:
            typeof record.image_page_count === 'number' ? record.image_page_count : null,
        }
      }),
      checkers: checkers.map((checker) => {
        const record = (checker ?? {}) as Record<string, unknown>
        return { code: String(record.code ?? ''), status: String(record.status ?? '') }
      }),
      errorMessage: (scan.error_message as string | null) ?? null,
    },
    summary: {
      total: (summary.total as number) ?? 0,
      byCategory: (summary.by_category as Record<string, number>) ?? {},
      bySeverity: (summary.by_severity as Record<string, number>) ?? {},
      byCertainty: (summary.by_certainty as Record<string, number>) ?? {},
      byDisposition: (summary.by_disposition as Record<string, number>) ?? {},
    },
    findings: findings.map((item) =>
      transformFinding((item ?? {}) as Record<string, unknown>),
    ),
  }
}

// ── 排序 / 筛选 / 计数 ──────────────────────────────────────────────────────

function severityRank(severity: string): number {
  const index = QA_SEVERITY_ORDER.indexOf(severity as (typeof QA_SEVERITY_ORDER)[number])
  return index === -1 ? QA_SEVERITY_ORDER.length : index
}

function categoryRank(category: string): number {
  const index = QA_CATEGORY_ORDER.indexOf(category as (typeof QA_CATEGORY_ORDER)[number])
  return index === -1 ? QA_CATEGORY_ORDER.length : index
}

export function firstEvidencePage(finding: QaFinding): number {
  const pages = finding.evidence.items
    .map((item) => item.page)
    .filter((page): page is number => typeof page === 'number')
  return pages.length ? Math.min(...pages) : Number.MAX_SAFE_INTEGER
}

/** 发现项来源卷: 只收集证据上的 bid_part; 整本(null)不产出, 禁止用 rule_code 冒充分卷 */
export function findingBidParts(finding: QaFinding): string[] {
  const seen = new Set<string>()
  const parts: string[] = []
  for (const item of finding.evidence.items) {
    const part = item.bidPart
    if (!part || seen.has(part)) continue
    seen.add(part)
    parts.push(part)
  }
  return parts
}

/** 默认排序: 待处理优先 -> 严重度 -> 类型聚簇 -> 首证据页升序 -> id */
export function sortFindings(findings: QaFinding[]): QaFinding[] {
  return [...findings].sort((a, b) => {
    const pendingDelta =
      (a.disposition === 'pending' ? 0 : 1) - (b.disposition === 'pending' ? 0 : 1)
    if (pendingDelta !== 0) return pendingDelta
    const severityDelta = severityRank(a.severity) - severityRank(b.severity)
    if (severityDelta !== 0) return severityDelta
    const categoryDelta = categoryRank(a.category) - categoryRank(b.category)
    if (categoryDelta !== 0) return categoryDelta
    const pageDelta = firstEvidencePage(a) - firstEvidencePage(b)
    if (pageDelta !== 0) return pageDelta
    return a.id - b.id
  })
}

export function matchesFilters(finding: QaFinding, filters: QaFilterState): boolean {
  if (filters.group !== 'all' && groupOfCategory(finding.category) !== filters.group) {
    return false
  }
  if (filters.severity !== 'all' && finding.severity !== filters.severity) return false
  if (filters.disposition !== 'all' && finding.disposition !== filters.disposition) {
    return false
  }
  if (filters.certainty !== 'all' && finding.certainty !== filters.certainty) return false
  return true
}

export function filterFindings(
  findings: QaFinding[],
  filters: QaFilterState,
): QaFinding[] {
  return findings.filter((finding) => matchesFilters(finding, filters))
}

/** 联动计数: 每轴计数在"其余轴筛选生效"的子集内统计 */
export function countFindings(
  findings: QaFinding[],
  filters: QaFilterState,
): QaFilterCounts {
  const counts: QaFilterCounts = {
    byGroup: { all: 0 },
    bySeverity: { all: 0 },
    byDisposition: { all: 0 },
    byCertainty: { all: 0 },
  }
  for (const finding of findings) {
    const group = groupOfCategory(finding.category)
    const matchGroup = filters.group === 'all' || group === filters.group
    const matchSeverity = filters.severity === 'all' || finding.severity === filters.severity
    const matchDisposition =
      filters.disposition === 'all' || finding.disposition === filters.disposition
    const matchCertainty =
      filters.certainty === 'all' || finding.certainty === filters.certainty

    if (matchSeverity && matchDisposition && matchCertainty) {
      counts.byGroup[group] = (counts.byGroup[group] || 0) + 1
      counts.byGroup.all += 1
    }
    if (matchGroup && matchDisposition && matchCertainty) {
      counts.bySeverity[finding.severity] = (counts.bySeverity[finding.severity] || 0) + 1
      counts.bySeverity.all += 1
    }
    if (matchGroup && matchSeverity && matchCertainty) {
      counts.byDisposition[finding.disposition] =
        (counts.byDisposition[finding.disposition] || 0) + 1
      counts.byDisposition.all += 1
    }
    if (matchGroup && matchSeverity && matchDisposition) {
      counts.byCertainty[finding.certainty] = (counts.byCertainty[finding.certainty] || 0) + 1
      counts.byCertainty.all += 1
    }
  }
  return counts
}

export function pendingFindingCount(data: DocumentQualityData | null): number {
  if (!data) return 0
  return data.summary.byDisposition.pending ?? 0
}
