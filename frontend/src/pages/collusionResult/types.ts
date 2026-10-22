import type { CollusionPairResult } from '@/services/collusionApi'
import type { EvidenceLevelCounts } from './evidenceFiltering'

// ==================== 风险等级配置 ====================

export interface RiskConfig {
  label: string
  bg: string
  text: string
  border: string
  headerBg: string
}

export const RISK_CONFIG: Record<string, RiskConfig> = {
  high:         { label: '高风险',   bg: 'bg-red-50',    text: 'text-red-700',    border: 'border-red-300',    headerBg: 'bg-red-100'    },
  medium:       { label: '中风险',   bg: 'bg-orange-50', text: 'text-orange-700', border: 'border-orange-300', headerBg: 'bg-orange-100' },
  low:          { label: '低风险',   bg: 'bg-yellow-50', text: 'text-yellow-700', border: 'border-yellow-300', headerBg: 'bg-yellow-100' },
  none:         { label: '无风险',   bg: 'bg-green-50',  text: 'text-green-700',  border: 'border-green-200',  headerBg: 'bg-green-100'  },
  inconclusive: { label: '无法判定', bg: 'bg-gray-50',   text: 'text-gray-600',   border: 'border-gray-300',   headerBg: 'bg-gray-100'   },
}

export const getRiskConfig = (risk: string): RiskConfig =>
  RISK_CONFIG[risk?.toLowerCase()] || RISK_CONFIG['inconclusive']

export interface DecisionConfig {
  label: string
  badge: 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning'
  hint?: string
}

export const DECISION_CONFIG: Record<string, DecisionConfig> = {
  clear_no_risk: { label: '未发现串标嫌疑', badge: 'success' },
  suspicious_found: { label: '发现疑似串标', badge: 'destructive' },
  partial_review_needed: { label: '需人工复核', badge: 'warning', hint: '当前仍存在待复核或失败的文件对' },
  analysis_failed: { label: '分析失败', badge: 'outline', hint: '自动化分析未形成有效结论' },
}

export const getDecisionConfig = (decision?: string): DecisionConfig =>
  DECISION_CONFIG[decision || 'partial_review_needed'] || DECISION_CONFIG.partial_review_needed

// recharts 用颜色值
export const RISK_COLORS: Record<string, string> = {
  high:         'hsl(0, 84%, 60%)',
  medium:       'hsl(25, 95%, 53%)',
  low:          'hsl(45, 93%, 47%)',
  none:         'hsl(142, 76%, 36%)',
  inconclusive: 'hsl(215, 16%, 47%)',
}

export const RISK_ORDER: Record<string, number> = {
  high: 0, medium: 1, low: 2, none: 3, inconclusive: 4,
}

// ==================== 维度配置 ====================

export const DIMENSION_LABELS: Record<string, string> = {
  d1PersonnelOverlap:     'D1 人员重叠',
  d2CompanyRelation:      'D2 企业关联',
  d3TextSimilarity:       'D3 文本雷同',
  d4PricePattern:         'D4 报价异常',
  d5QualificationOverlap: 'D5 资质业绩',
  d6FormatSimilarity:     'D6 文档格式',
  d7AbnormalConsistency:  'D7 异常一致',
  d8MetadataSimilarity:   'D8 文档元数据',
}

export const DIMENSION_KEYS = Object.keys(DIMENSION_LABELS) as (keyof typeof DIMENSION_LABELS)[]

export const DIMENSION_SHORT_LABELS: Record<string, string> = {
  d1PersonnelOverlap:     '人员',
  d2CompanyRelation:      '企业',
  d3TextSimilarity:       '文本',
  d4PricePattern:         '报价',
  d5QualificationOverlap: '资质',
  d6FormatSimilarity:     '格式',
  d7AbnormalConsistency:  '一致',
  d8MetadataSimilarity:   '元数据',
}

export const CERT_KEY_MAP: Record<string, string> = {
  d1PersonnelOverlap: 'd1_personnel',
  d2CompanyRelation: 'd2_company',
  d3TextSimilarity: 'd3_text',
  d4PricePattern: 'd4_price',
  d5QualificationOverlap: 'd5_qualification',
  d6FormatSimilarity: 'd6_format',
  d7AbnormalConsistency: 'd7_abnormal',
  d8MetadataSimilarity: 'd8_metadata',
}

// ==================== 证据级别 ====================

export interface EvidenceLevelInfo {
  label: string
  color: string
  tooltip: string
}

export const EVIDENCE_LEVEL_LABELS: Record<string, EvidenceLevelInfo> = {
  first:   { label: '一级证据', color: 'bg-red-100 text-red-700',      tooltip: '直接定性证据，单独可认定串标嫌疑' },
  second:  { label: '二级证据', color: 'bg-orange-100 text-orange-700', tooltip: '强力佐证，>=2项交叉验证可认定' },
  third:   { label: '三级证据', color: 'bg-yellow-100 text-yellow-700', tooltip: '辅助参考，增强判断但不能独立定性' },
  unknown: { label: '参考信息', color: 'bg-gray-100 text-gray-600',     tooltip: '参考性发现' },
}

export const getEvidenceLevelInfo = (level: string): EvidenceLevelInfo =>
  EVIDENCE_LEVEL_LABELS[level] || EVIDENCE_LEVEL_LABELS['unknown']

// ==================== 筛选状态 ====================

export interface CollusionFilterState {
  risk: string
  evidenceLevel: string
}

export interface CollusionFilterCounts {
  byRisk: Record<string, number>
  byEvidenceLevel: EvidenceLevelCounts
}

// ==================== 工具函数 ====================

export const sortPairsByRisk = (pairs: CollusionPairResult[]): CollusionPairResult[] =>
  [...pairs].sort(
    (a, b) => (RISK_ORDER[(a.riskLevel || a.overallRisk)?.toLowerCase()] ?? 5) - (RISK_ORDER[(b.riskLevel || b.overallRisk)?.toLowerCase()] ?? 5)
  )

const stripPdfName = (name: string): string => name.replace(/\.pdf$/i, '')

const IDENTITY_GUARD = '(?=[^0-9年月日份%]|$)'

const replaceDocumentIdentityTokens = (
  text: string,
  docIdToName: Map<number, string>,
  pairOrdinalCount: number,
): string => {
  if (!text || docIdToName.size === 0) return text
  const nameOf = (raw: string): string | undefined => docIdToName.get(Number(raw))
  let result = text
  result = result.replace(
    new RegExp(`(文件对|投标文件|文件|文档)\\s*(\\d+)\\s*[与和、,，]\\s*(\\d+)${IDENTITY_GUARD}`, 'g'),
    (full, prefix: string, left: string, right: string) => {
      const nameA = nameOf(left)
      const nameB = nameOf(right)
      return nameA && nameB ? `${prefix}${nameA}与${nameB}` : full
    },
  )
  result = result.replace(
    new RegExp(`(文件对|投标文件|文件|文档)\\s*(\\d+)${IDENTITY_GUARD}`, 'g'),
    (full, prefix: string, raw: string) => {
      const name = nameOf(raw)
      return name ? `${prefix}${name}` : full
    },
  )
  result = result.replace(
    /(?:doc_id|document_id)\s*[=:：]\s*(\d+)(?![0-9])/gi,
    (full, raw: string) => nameOf(raw) ?? full,
  )
  result = result.replace(
    new RegExp(`([与和、，,])\\s*(\\d+)${IDENTITY_GUARD}`, 'g'),
    (full, conj: string, raw: string) => {
      const name = nameOf(raw)
      return name ? `${conj}${name}` : full
    },
  )
  result = result.replace(
    new RegExp(`(?<!文件)对(\\d+)${IDENTITY_GUARD}`, 'g'),
    (full, raw: string) => {
      const number = Number(raw)
      if (number >= 1 && number <= pairOrdinalCount) return full
      return nameOf(raw) ?? full
    },
  )
  return result
}

/**
 * 构建 "对X" / pair_id / 文档身份编号 -> 文件名 替换函数，并清理孤立引用。
 * 基于按风险排序后的 pairs，将 LLM 输出中的 "对1""对2" 与 doc_id 引用
 * 替换为实际上传文件名。
 */
export const buildPairRefReplacer = (pairResults: CollusionPairResult[]) => {
  const sorted = sortPairsByRisk(pairResults)
  const pairIdMap = new Map(
    pairResults
      .filter((pair) => pair.pairId)
      .map((pair) => [pair.pairId!, `【${stripPdfName(pair.docAName)}×${stripPdfName(pair.docBName)}】`])
  )
  const docIdToName = new Map<number, string>()
  for (const pair of pairResults) {
    docIdToName.set(pair.docAId, stripPdfName(pair.docAName))
    docIdToName.set(pair.docBId, stripPdfName(pair.docBName))
  }
  return (text: string | undefined): string => {
    if (!text) return ''
    let result = text
    pairIdMap.forEach((label, pairId) => {
      result = result.replace(new RegExp(pairId.replace(':', '\\:'), 'g'), label)
    })
    result = replaceDocumentIdentityTokens(result, docIdToName, sorted.length)
    sorted.forEach((pair, idx) => {
      const refNum = idx + 1
      const nameA = stripPdfName(pair.docAName)
      const nameB = stripPdfName(pair.docBName)
      result = result.replace(
        new RegExp(`(?<!文件)对${refNum}(?=[^0-9]|$)`, 'g'),
        `【${nameA}×${nameB}】`
      )
    })
    result = result.replace(/(?<!文件)对(\d+)(?=[^0-9年月日份%]|$)/g, '（引用无效）')
    result = result.replace(/（引用无效）([、，,]\s*（引用无效）)+/g, '（引用无效）')
    result = result.replace(/[、，,]\s*（引用无效）/g, '')
    result = result.replace(/（证据来源：\s*（引用无效）\s*）/g, '')
    return result.trim()
  }
}

/** 获取对比对的维度得分数组（用于雷达图） */
export const getPairDimensionScores = (pair: CollusionPairResult) =>
  DIMENSION_KEYS.map(key => ({
    key,
    label: DIMENSION_LABELS[key],
    shortLabel: DIMENSION_SHORT_LABELS[key],
    score: pair[key as keyof CollusionPairResult] as number | undefined | null,
  }))

/** 热力图颜色梯度: 绿(0-0.3) -> 黄(0.3-0.6) -> 红(0.6-1.0) */
export const getHeatmapColor = (value: number | null): string => {
  if (value === null || value === undefined) return '#f3f4f6'
  const v = Math.max(0, Math.min(1, value))
  if (v < 0.3) {
    const t = v / 0.3
    const r = Math.round(34 + t * (234 - 34))
    const g = Math.round(197 + t * (179 - 197))
    const b = Math.round(94 + t * (8 - 94))
    return `rgb(${r},${g},${b})`
  }
  if (v < 0.6) {
    const t = (v - 0.3) / 0.3
    const r = Math.round(234 + t * (239 - 234))
    const g = Math.round(179 - t * (179 - 68))
    const b = Math.round(8 + t * (68 - 8))
    return `rgb(${r},${g},${b})`
  }
  const t = (v - 0.6) / 0.4
  const r = Math.round(239 - t * (239 - 185))
  const g = Math.round(68 - t * (68 - 28))
  const b = Math.round(68 - t * (68 - 28))
  return `rgb(${r},${g},${b})`
}

// B1: 串标结果失败合同扩展（FAILURE_STATE_ENABLED=true 时才出现）
export interface CollusionFailureStateFields {
  failureState?: string
  failureReason?: string
  confidence?: number
  gateTrace?: unknown
  failureReviewRequired?: number
  reviewedAt?: string
  reviewedBy?: number
}

// ==================== 证据按维度二级分组 ====================

export interface DimensionSection<T> {
  dimension: string
  label: string
  findings: T[]
  defaultOpen: boolean
}

const DIMENSION_ORDER = ['D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'uncertain']
const DEFAULT_OPEN_DIMENSIONS = new Set(['D1', 'D2', 'D3', 'D4'])

const FINDING_DIM_FALLBACK: Record<string, string> = {
  D1: 'D1 人员重叠',
  D2: 'D2 企业关联',
  D3: 'D3 文本雷同',
  D4: 'D4 报价异常',
  D5: 'D5 资质业绩',
  D6: 'D6 文档格式',
  D7: 'D7 异常一致',
  D8: 'D8 文档元数据',
  uncertain: '待确认',
}

export const normalizeFindingDimension = (raw: string | null | undefined): string => {
  if (!raw) return 'uncertain'
  const upper = raw.toUpperCase().trim()
  if (/^D[1-8]$/.test(upper)) return upper
  return 'uncertain'
}

export const groupFindingsByDimension = <T extends { dimension?: string | null }>(
  findings: T[],
): DimensionSection<T>[] => {
  const buckets = new Map<string, T[]>()
  for (const f of findings) {
    const dim = normalizeFindingDimension(f.dimension)
    const list = buckets.get(dim)
    if (list) list.push(f)
    else buckets.set(dim, [f])
  }

  const sections: DimensionSection<T>[] = []
  for (const dim of DIMENSION_ORDER) {
    const items = buckets.get(dim)
    if (!items || items.length === 0) continue
    sections.push({
      dimension: dim,
      label: FINDING_DIM_FALLBACK[dim] || dim,
      findings: items,
      defaultOpen: DEFAULT_OPEN_DIMENSIONS.has(dim),
    })
  }
  return sections
}

/** 获取pair的最高证据级别 */
export const getHighestEvidenceLevel = (pair: CollusionPairResult): string => {
  const order: Record<string, number> = { first: 0, second: 1, third: 2, unknown: 3 }
  let highest = 'unknown'
  let highestOrder = 99
  for (const f of pair.findings) {
    const o = order[f.level] ?? 99
    if (o < highestOrder) {
      highestOrder = o
      highest = f.level
    }
  }
  return highest
}
