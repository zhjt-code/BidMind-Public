import {
  sanitizeUserFacingList,
  sanitizeUserFacingText,
} from '../../services/progressStatusTransforms.ts'

export interface ReviewResult {
  id: number
  category: string
/**
 * 分类依据（评分换轴 P3 前向兼容）：后端命中的分类关键词。
 * null=后端零命中（评分项视图归"未分类"桶）；undefined=旧数据无此字段（按 category 正常归类）。
 */
  categoryBasis?: string | null
  requirementSummary: string
  requirementDescription?: string | null
  responseSummary: string
  responseContent?: string | null
  complianceLevel: 'fully' | 'partially' | 'not' | 'missing'
  /** 审查未产出有效符合结论（超时/输出不合规/兜底失败）。不是第五种符合程度。 */
  pendingJudgment?: boolean
  score: number
  comment: string
  riskLevel: 'none' | 'low' | 'medium' | 'high'
  canonicalKey?: string
  factKey?: string
  familyType?: 'performance' | 'audit_finance' | 'qualification' | 'generic' | string
  attributionType?: 'shared' | 'independent' | 'none' | string
  consistencyFlag?: 'aligned' | 'needs_review' | 'conflicted' | 'cross_conflict' | 'unchecked' | string
  mainRequirementId?: number | null
  subjectScope?: 'enterprise' | 'personnel' | 'both' | 'generic' | string
  evidenceRelation?: 'shared' | 'independent' | 'undetermined' | string
  reconcileNote?: string
  sourcePages?: string | null
  sourcePageNumbers?: number[]
  /** 投标信封: 响应归因文档 id(整本/历史数据回退页面级 bidDocumentId) */
  sourceDocumentId?: number | null
  /** 来源卷角色列表(commercial/technical), 整本为空 */
  sourceParts?: string[]
  /** 页码定位 [{documentId, part, page}], 供按文档跳转预览 */
  sourcePageLocations?: Array<{
    documentId: number
    part: string | null
    page: number
    excerpt?: string | null
  }>
  sourcePageExcerpts?: Array<{
    documentId: number
    page: number
    excerpt: string
  }>
  requirementSourcePage?: number | null
  requirementSourcePages?: number[]
  requirementKeywords?: string[]
  requirementSubRequirements?: string[]
  requirementMergedCount?: number | null
  responseCompleteness?: string | null
  responseMissingElements?: string[]
  responseLinkedTo?: number | null
  responseEvidenceAnchor?: string | null
  relevanceScore?: number | null
  relevanceNote?: string | null

  // P2 闭环：评分项满分/预估得分
  requirementKind?: string
  maxScore?: number | null
  isKeyReview?: boolean
  /** 要求当前评审意义（P2.2）：非 actionable 且非评分链路的明细行不计入汇总评分。 */
  reviewRelevance?: string
  estimatedScore?: number | null
  // 评分项卷归属（按卷聚合；供结果页"评分项视图"按卷分组下钻）
  volume?: string | null
  /** 评分项卷原文标签：展示优先使用，缺失回退 volume 映射。 */
  volumeLabel?: string | null
  volumeMax?: number | null
  volumeWeight?: number | null
  /** 采购包/标段归属（多包文档按包下钻；单包文档为 null）。 */
  lot?: string | null

  // Wave2/S9: 评分项矩阵字段（ruleAssessments=null 时降级展示 comment）
  /** 评分项名称（后端 score_item_name；缺省时回退 requirementSummary）。 */
  scoreItemName?: string | null
  /** 评分标准条目（供矩阵视图"评审标准"列展示，string[] 或 unknown[]）。 */
  scoreRules?: unknown[]
  /** 扣分标准条目。 */
  deductionRules?: unknown[]
  /** 逐档判定结果（null 表示老数据，降级展示 comment）。 */
  ruleAssessments?: Array<{
    rule_excerpt?: string
    status?: 'met' | 'partial' | 'not' | 'unknown' | string
    points?: number | null
    reason?: string
    evidence_pages?: number[]
  }> | null

  // B1: 失败合同字段（FAILURE_STATE_ENABLED=true 时才出现）
  failureState?: string
  failureReason?: string
  confidence?: number
  gateTrace?: unknown
  reviewRequired?: number
  reviewedAt?: string
  reviewedBy?: number
}

export interface VolumeScore {
  estimated: number
  max: number
  count: number
  /** 卷权重(0-1, 加权制下存在; 单一制为 null/缺省)。 */
  weight?: number | null
}

/** 单采购包核计：多包文档结果页按包独立展示预估/满分。 */
export interface LotScore {
  estimated: number
  max: number
  count: number
  /** 该包内已产出预估得分的评分项数（0=待重新审查，预估显示为 —）。 */
  estimatedCount: number
  /** 该包满分结构是否可信（单一100制/折算成功）；false 时该包按卷参考。 */
  reliable: boolean
  /** 是否为"未识别归属"护栏桶（上游丢 lot 时 fail-visible，须警示人工核对）。 */
  unassigned: boolean
  /** 包内按卷明细（键为中文卷名）。 */
  byVolume: Record<string, VolumeScore>
}

export interface ReviewSummary {
  totalScore: number
  averageScore: number
  weightedScore: number
  conclusion: 'pass' | 'fail' | 'pending'
  conclusionReason: string
  totalCount: number
  compliantCount: number
  highRiskCount: number
  // P2 闭环：评分项预估总得分（AI 预估，仅供参考）
  scoringItemCount: number
  estimatedItemCount: number
  estimatedTotalScore: number
  maxTotalScore: number
  estimatedScoreByVolume: Record<string, VolumeScore>
  scoringTotalReliable: boolean
  /** 按采购包核计：仅多包（>=2）文档非空，展示时优先于统一总分。 */
  estimatedScoreByLot: Record<string, LotScore>
}

export interface FilterState {
  compliance: string
  risk: string
  category: string
  /** 评分项视图开关：true 时列表仅保留评分项全集（对齐后端 is_scoring_universe_member）。 */
  scoringOnly: boolean
/**
 * 评分项视图下的评分卷桶筛选（原文组标签轴）：后端卷桶键（多包为"包·卷"）；
 * null=全部评分项。非评分项视图忽略。
 */
  scoringVolume: string | null
}

export interface FilterCounts {
  byCompliance: Record<string, number>
  byRisk: Record<string, number>
  byCategory: Record<string, number>
  /** 评分卷桶计数（仅评分项视图有意义，键为卷桶键）。 */
  byVolume: Record<string, number>
}

import { categoryLabels, getCategoryLabel } from '../../lib/constants.ts'
export { categoryLabels, getCategoryLabel }

export const complianceLevelLabels: Record<string, string> = {
  fully: '完全符合',
  partially: '部分符合',
  not: '不符合',
  missing: '未响应',
}

export const riskLevelLabels: Record<string, string> = {
  none: '无风险',
  low: '低风险',
  medium: '中风险',
  high: '高风险',
}

export const responseCompletenessLabels: Record<string, string> = {
  full: '完整响应',
  partial: '部分响应',
  form_only: '仅形式响应',
}

export const subjectScopeLabels: Record<string, string> = {
  enterprise: '企业',
  personnel: '人员',
  both: '双归因',
  generic: '待识别',
}

export const evidenceRelationLabels: Record<string, string> = {
  shared: '共享',
  independent: '独立',
  undetermined: '待判定',
}

export const transformResult = (item: Record<string, unknown>): ReviewResult => ({
  id: item.id as number,
  category: item.category as string,
  // 旧数据缺失该字段时保持 undefined（不显示未分类桶）；null 表示后端零命中。
  categoryBasis: item.category_basis as string | null | undefined,
  requirementSummary: item.requirement_summary as string,
  requirementDescription: item.requirement_description as string | null | undefined,
  responseSummary: item.response_summary as string,
  responseContent: item.response_content as string | null | undefined,
  complianceLevel: item.compliance_level as ReviewResult['complianceLevel'],
  pendingJudgment: Boolean(item.pending_judgment),
  score: item.score as number,
  comment: sanitizeUserFacingText(item.comment as string),
  riskLevel: item.risk_level as ReviewResult['riskLevel'],
  canonicalKey: item.canonical_key as string | undefined,
  factKey: item.fact_key as string | undefined,
  familyType: item.family_type as ReviewResult['familyType'],
  attributionType: item.attribution_type as ReviewResult['attributionType'],
  consistencyFlag: item.consistency_flag as ReviewResult['consistencyFlag'],
  mainRequirementId: item.main_requirement_id as number | null | undefined,
  subjectScope: item.subject_scope as ReviewResult['subjectScope'],
  evidenceRelation: item.evidence_relation as ReviewResult['evidenceRelation'],
  reconcileNote: item.reconcile_note
    ? sanitizeUserFacingText(item.reconcile_note as string)
    : undefined,
  sourcePages: item.source_pages as string | null | undefined,
  sourcePageNumbers: (item.source_page_numbers as number[] | undefined) || [],
  sourceDocumentId: item.source_document_id as number | null | undefined,
  sourceParts: (item.source_parts as string[] | undefined) || [],
  sourcePageLocations: (
    (item.source_page_locations as Array<{
      document_id: number
      part: string | null
      page: number
      excerpt?: string | null
    }> | undefined) || []
  ).map((location) => ({
    documentId: location.document_id,
    part: location.part,
    page: location.page,
    ...(location.excerpt ? { excerpt: location.excerpt } : {}),
  })),
  sourcePageExcerpts: (
    (item.source_page_excerpts as Array<{
      document_id: number
      page: number
      excerpt: string
    }> | undefined) || []
  ).map((row) => ({
    documentId: row.document_id,
    page: row.page,
    excerpt: row.excerpt,
  })),
  requirementSourcePage: item.requirement_source_page as number | null | undefined,
  requirementSourcePages: (item.requirement_source_pages as number[] | undefined) || [],
  requirementKeywords: (item.requirement_keywords as string[] | undefined) || [],
  requirementSubRequirements: (item.requirement_sub_requirements as string[] | undefined) || [],
  requirementMergedCount: item.requirement_merged_count as number | null | undefined,
  responseCompleteness: item.response_completeness as string | null | undefined,
  responseMissingElements: sanitizeUserFacingList(
    item.response_missing_elements as string[] | undefined,
  ),
  responseLinkedTo: item.response_linked_to as number | null | undefined,
  responseEvidenceAnchor: item.response_evidence_anchor as string | null | undefined,
  relevanceScore: item.relevance_score as number | null | undefined,
  relevanceNote: item.relevance_note as string | null | undefined,
  requirementKind: (item.requirement_kind as string | undefined) || 'normal',
  maxScore: item.max_score as number | null | undefined,
  isKeyReview: Boolean(item.is_key_review),
  reviewRelevance: (item.review_relevance as string | undefined) || 'actionable',
  estimatedScore: item.estimated_score as number | null | undefined,
  volume: item.volume as string | null | undefined,
  volumeLabel: item.volume_label as string | null | undefined,
  volumeMax: item.volume_max as number | null | undefined,
  volumeWeight: item.volume_weight as number | null | undefined,
  lot: item.lot as string | null | undefined,
  scoreItemName: item.score_item_name as string | null | undefined,
  scoreRules: (item.score_rules as unknown[] | undefined) || [],
  deductionRules: (item.deduction_rules as unknown[] | undefined) || [],
  ruleAssessments: Array.isArray(item.rule_assessments)
    ? (item.rule_assessments as NonNullable<ReviewResult['ruleAssessments']>).map((assessment) => ({
      ...assessment,
      reason: assessment.reason ? sanitizeUserFacingText(assessment.reason) : assessment.reason,
    }))
    : item.rule_assessments as ReviewResult['ruleAssessments'],
})

/** 后端 estimated_score_by_lot 单包条目 → LotScore（snake_case 键归一）。 */
const transformLotScore = (raw: Record<string, unknown>): LotScore => ({
  estimated: Number(raw.estimated) || 0,
  max: Number(raw.max) || 0,
  count: Number(raw.count) || 0,
  estimatedCount: Number(raw.estimated_count) || 0,
  reliable: Boolean(raw.reliable),
  unassigned: Boolean(raw.unassigned),
  byVolume: (raw.by_volume as Record<string, VolumeScore> | undefined) ?? {},
})

export const transformSummary = (data: Record<string, unknown>): ReviewSummary => ({
  totalScore: data.total_score as number,
  averageScore: data.average_score as number,
  weightedScore: (data.weighted_score as number | undefined) ?? (data.average_score as number),
  conclusion: data.conclusion as ReviewSummary['conclusion'],
  conclusionReason: data.conclusion_reason as string,
  totalCount: data.total_count as number,
  compliantCount: data.compliant_count as number,
  highRiskCount: data.high_risk_count as number,
  scoringItemCount: (data.scoring_item_count as number | undefined) ?? 0,
  estimatedItemCount: (data.estimated_item_count as number | undefined) ?? 0,
  estimatedTotalScore: (data.estimated_total_score as number | undefined) ?? 0,
  maxTotalScore: (data.max_total_score as number | undefined) ?? 0,
  estimatedScoreByVolume:
    (data.estimated_score_by_volume as Record<string, VolumeScore> | undefined) ?? {},
  scoringTotalReliable: (data.scoring_total_reliable as boolean | undefined) ?? true,
  estimatedScoreByLot: Object.fromEntries(
    Object.entries(
      (data.estimated_score_by_lot as Record<string, Record<string, unknown>> | undefined) ?? {},
    ).map(([lot, raw]) => [lot, transformLotScore(raw)]),
  ),
})

export const getComplianceBadgeVariant = (level: string): 'success' | 'warning' | 'destructive' | 'secondary' => {
  switch (level) {
    case 'fully': return 'success'
    case 'partially': return 'warning'
    case 'not': return 'destructive'
    default: return 'secondary'
  }
}

export const getRiskColor = (level: string): string => {
  switch (level) {
    case 'high': return 'text-red-600'
    case 'medium': return 'text-yellow-600'
    case 'low': return 'text-blue-600'
    default: return 'text-green-600'
  }
}

export const getConclusionStyle = (conclusion: string): string => {
  switch (conclusion) {
    case 'pass': return 'bg-green-500'
    case 'fail': return 'bg-red-500'
    default: return 'bg-yellow-500'
  }
}

export const getConclusionText = (conclusion: string): string => {
  switch (conclusion) {
    case 'pass': return '通过'
    case 'fail': return '不通过'
    default: return '待澄清'
  }
}

/**
 * P2.2: 是否为"参考条款·不计分"明细行——要求当前评审意义已为参考条款
 * （人工降级或存量折叠项），该行保留展示但不计入汇总评分。
 * 评分项铁律: scoring_item / isKeyReview 恒计分，永不视为参考条款。
 */
export const isNonScoredReference = (result: ReviewResult): boolean => {
  if (result.requirementKind === 'scoring_item' || result.isKeyReview) return false
  return (result.reviewRelevance || 'actionable') !== 'actionable'
}

export const isPerformanceCategory = (category: string): boolean =>
  getCategoryLabel(category) === '业绩要求'

export const isAuditFinanceFamily = (result: ReviewResult): boolean =>
  result.familyType === 'audit_finance'

export const shouldShowEvidenceTag = (result: ReviewResult): boolean => {
  if (!(isPerformanceCategory(result.category) || isAuditFinanceFamily(result))) return false
  return result.evidenceRelation === 'shared' || result.evidenceRelation === 'independent'
}

export const COMPLIANCE_ORDER: Record<ReviewResult['complianceLevel'], number> = {
  not: 1,
  partially: 2,
  fully: 3,
  missing: 4,
}

export const CATEGORY_ORDER: Record<string, number> = {
  QUAL: 1, COMM: 2, TECH: 3, STAFF: 4, FORMAT: 5,
  qualification: 1, commercial: 2, technical: 3, staffing: 4, format: 5,
  PERF: 6, performance: 6,
}

export const COMPLIANCE_COLORS: Record<string, string> = {
  fully: 'hsl(142, 76%, 36%)',
  partially: 'hsl(38, 92%, 50%)',
  not: 'hsl(0, 84%, 60%)',
  missing: 'hsl(215, 16%, 47%)',
}

export const RISK_COLORS: Record<string, string> = {
  high: 'hsl(0, 84%, 60%)',
  medium: 'hsl(38, 92%, 50%)',
  low: 'hsl(217, 91%, 60%)',
  none: 'hsl(142, 76%, 36%)',
}

// ==================== 事实组聚合（同一证据事实跨多个审查项的归并） ====================

export interface FactGroup {
  groupKey: string
  primary: ReviewResult
  members: ReviewResult[]
  worstCompliance: ReviewResult['complianceLevel']
  worstRisk: ReviewResult['riskLevel']
  hasConflict: boolean
  needsReview: boolean
}

const RISK_ORDER_FOR_GROUP: Record<ReviewResult['riskLevel'], number> = {
  high: 0,
  medium: 1,
  low: 2,
  none: 3,
}

const getDescriptionLength = (r: ReviewResult): number =>
  (r.requirementDescription || r.requirementSummary || '').length

const pickPrimary = (members: ReviewResult[]): ReviewResult => {
  const heads = members.filter(m => m.mainRequirementId == null)
  const candidates = heads.length > 0 ? heads : members
  return candidates.reduce(
    (a, b) => (getDescriptionLength(a) >= getDescriptionLength(b) ? a : b),
    candidates[0],
  )
}

export const groupResultsByFact = (results: ReviewResult[]): FactGroup[] => {
  const buckets = new Map<string, ReviewResult[]>()
  const order: string[] = []

  for (const r of results) {
    const key = r.factKey || r.canonicalKey || `result-${r.id}`
    const bucket = buckets.get(key)
    if (bucket) {
      bucket.push(r)
    } else {
      buckets.set(key, [r])
      order.push(key)
    }
  }

  const groups: FactGroup[] = []
  for (const key of order) {
    const members = buckets.get(key) || []
    if (members.length === 0) continue

    const primary = pickPrimary(members)

    let worstCompliance: ReviewResult['complianceLevel'] = members[0].complianceLevel
    let worstRisk: ReviewResult['riskLevel'] = members[0].riskLevel
    let hasConflict = false
    let needsReview = false

    for (const m of members) {
      if (COMPLIANCE_ORDER[m.complianceLevel] < COMPLIANCE_ORDER[worstCompliance]) {
        worstCompliance = m.complianceLevel
      }
      if ((RISK_ORDER_FOR_GROUP[m.riskLevel] ?? 99) < (RISK_ORDER_FOR_GROUP[worstRisk] ?? 99)) {
        worstRisk = m.riskLevel
      }
      if (m.consistencyFlag === 'cross_conflict' || m.consistencyFlag === 'conflicted') {
        hasConflict = true
      }
      if (m.consistencyFlag === 'needs_review') {
        needsReview = true
      }
    }

    groups.push({
      groupKey: key,
      primary,
      members,
      worstCompliance,
      worstRisk,
      hasConflict,
      needsReview,
    })
  }

  return groups
}
