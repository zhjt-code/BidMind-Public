import { api } from './httpClient'
import type { ApiResponse } from './apiTypes'
import { transformCollusionPairResultRaw } from './collusionTransforms'
import { mapCollusionResultsRaw } from './collusionResultsMapper'
import { mapProgressStatusSteps, sanitizeProgressText, unwrapProgressStatusPayload } from './progressStatusTransforms'
import { transformRetryState, type WritingRetryState } from './writingRetryTransforms'
import type { FailureState } from '@/types/failureState'

export interface CollusionStepStatus {
  label: string
  status: 'waiting' | 'running' | 'completed' | 'failed'
  progress: number
  detail?: string
}

export interface CollusionStatus {
  projectId: number
  overallStatus: 'idle' | 'running' | 'completed' | 'failed'
  overallProgress: number
  steps: CollusionStepStatus[]
  totalDocuments: number
  totalBidDocs: number
  totalPairs: number
  extractedCount: number
  comparedCount: number
  totalLlmPairs: number
  skippedPairs: number
  failedPairs: number
  totalGroups: number
  globalFeaturesDone: boolean
  clusterDone: boolean
  hasTenderDoc: boolean
  errorMessage?: string
  message?: string
  isRecovering: boolean
  recoveryCount: number
  retryable?: boolean
  failedNode?: string | null
  targetId?: number | null
  failedStage?: string | null
  retryActionLabel?: string
  preservedOutputs?: string[]
  resetScope?: string[]
  lastAttempt?: number
  retryRunning?: boolean
  lastError?: string | null
}

export interface EvidenceRef {
  docId?: number
  docName?: string
  quote?: string
  location?: string
}

export interface CollusionFinding {
  dimension: string
  level: string
  description: string
  detail?: string
  evidence?: string
  quoteA?: string
  pageA?: string
  quoteB?: string
  pageB?: string
  pageRef?: string
  similarity?: number
  certainty?: number
  evidences?: EvidenceRef[]
  involvedDocIds?: number[]
  involvedDocNames?: string[]
  // 跨字段串联引擎填充字段（仅 risk_pattern 非空时才是跨字段命中）
  riskPattern?: string
  sourceFieldA?: string
  sourceFieldB?: string
  legalReference?: string
}

// P2-3: 关注项（attention_items）— 后端产出、前端按类别渲染
export interface AttentionItem {
  category: string
  dimension?: string
  summary: string
  detail?: string
  sourceReason?: string
}

// 重合片段：向量预筛选的高相似 chunk 对，含"疑似通用模板"标注，供人工甄别重合内容
export interface EvidenceChunk {
  textA: string
  textB: string
  pageA?: number
  pageB?: number
  score?: number
  literalScore?: number
  boilerplateFlag: boolean
  kbRuleMatch: boolean
}

export interface CollusionPairResult {
  pairId?: string
  docAId: number
  docBId: number
  docAName: string
  docBName: string
  overallRisk: string
  riskLevel?: string
  similarityScore?: number
  d1PersonnelOverlap?: number
  d2CompanyRelation?: number
  d3TextSimilarity?: number
  d4PricePattern?: number
  d5QualificationOverlap?: number
  d6FormatSimilarity?: number
  d7AbnormalConsistency?: number
  d8MetadataSimilarity?: number
  findings: CollusionFinding[]
  evidenceChunks?: EvidenceChunk[]
  analysisMode: string
  reviewState?: string
  analysisState: string
  decisionState: string
  failureState?: FailureState
  qualityFlags: string[]
  consistencyFlags: string[]
  riskExplanation: string
  dimensionCertainties?: Record<string, number>
  // P2-3: 被门控降级或被业务规则过滤、但仍有提醒价值的条目
  attentionItems?: AttentionItem[]
}

export interface GroupRoleItem {
  docId: number
  companyName: string
  role: string
  roleEvidence: string
}

export interface CollusionGroupResult {
  groupIndex: number
  memberDocIds: number[]
  memberNames: string[]
  riskLevel: string
  groupConfidence?: number
  uncertainEdgeCount: number
  roles: GroupRoleItem[]
  collusionStrategy?: string
  keyEvidenceSummary?: string
  investigationFocus: string[]
  relatedPairIds: string[]
  networkDescription?: string
  findings: CollusionFinding[]
}

export interface GlobalFindingItem {
  type: string
  involvedDocIds: number[]
  involvedNames: string[]
  description: string
}

export interface CoverageStats {
  totalPairs: number
  analyzedPairs: number
  llmAnalyzedPairs: number
  algorithmOnlyPairs: number
  skippedPairs: number
  failedPairs: number
  inconclusivePairs: number
  reviewRequiredPairs: number
  clearPairs: number
  suspiciousPairs: number
  coverageRatio: number
}

export interface SuspectedGroupSummary {
  documentIds: number[]
  documentNames: string[]
  riskLevel: string
  reason: string
}

export interface CollusionResults {
  projectId: number
  overallRisk: string
  projectDecision: string
  projectRiskLevel?: string | null
  decisionConfidence: string
  reviewRequired: boolean
  decisionBasis: string
  decisionEngineVersion: number
  overallRiskBasis: string
  riskDistributionBasis: string
  riskConsistencyStatus: string
  riskConsistencyNote?: string
  hasTenderDoc: boolean
  summarySourceVersion: number
  dataQualityFlags: string[]
  profileQualityFlags: string[]
  coverageStats: CoverageStats
  industryProfileSnapshot?: Record<string, unknown> | null
  industryProfileConfidence?: string
  /** 行业画像状态: success / llm_returned_unknown / insufficient_signals / llm_failed */
  industryProfileStatus?: string
  industryProfileSignals: Record<string, unknown>[]
  /** 后端按规则拼装的事实说明段（与 LLM 研判段 synthesisSummary 并列两路真源） */
  factualExplanation?: string
  /** 后端按规则拼装的事实说明条目（供 UI 编号展示，不含使用限制提示） */
  factualExplanationItems: string[]
  /** NxN 可疑度矩阵（certainty 加权: max(score*certainty)），非原始向量相似度 */
  similarityMatrix: (number | null)[][]
  documentLabels: string[]
  pairResults: CollusionPairResult[]
  suspectedGroups: SuspectedGroupSummary[]
  synthesisSummary?: string
  narrativeSummary?: string
  investigationSuggestions?: string[]
  extractionWarnings?: string[]
  groups: CollusionGroupResult[]
  globalFindings: GlobalFindingItem[]
}

// 转换单对对比结果
export const transformPairResult = (r: Record<string, unknown>): CollusionPairResult =>
  transformCollusionPairResultRaw(r) as CollusionPairResult

export const collusionService = {
  startAnalysis: async (projectId: number) => {
    return api.post(`/projects/${projectId}/collusion/analyze`)
  },

  getStatus: async (projectId: number): Promise<CollusionStatus> => {
    const response = await api.get<ApiResponse<Record<string, unknown>>>(
      `/projects/${projectId}/collusion/status`,
    )
    const d = unwrapProgressStatusPayload(response, '串标状态')
    return {
      projectId: d.project_id as number,
      overallStatus: d.overall_status as CollusionStatus['overallStatus'],
      overallProgress: d.overall_progress as number,
      steps: mapProgressStatusSteps(d.steps).map((step) => ({
        label: step.label,
        status: step.status as CollusionStepStatus['status'],
        progress: step.progress,
        detail: step.detail,
      })),
      totalDocuments: d.total_documents as number,
      totalBidDocs: d.total_bid_docs as number,
      totalPairs: d.total_pairs as number,
      extractedCount: d.extracted_count as number,
      comparedCount: d.compared_count as number,
      totalLlmPairs: (d.total_llm_pairs as number) || 0,
      skippedPairs: (d.skipped_pairs as number) || 0,
      failedPairs: (d.failed_pairs as number) || 0,
      totalGroups: (d.total_groups as number) || 0,
      globalFeaturesDone: (d.global_features_done as boolean) || false,
      clusterDone: (d.cluster_done as boolean) || false,
      hasTenderDoc: d.has_tender_doc as boolean,
      errorMessage: d.error_message as string | undefined,
      message: sanitizeProgressText(d.message as string | undefined) || undefined,
      isRecovering: (d.is_recovering as boolean) || false,
      recoveryCount: (d.recovery_count as number) || 0,
      retryable: (d.retryable as boolean) || false,
      failedNode: (d.failed_node as string | null) ?? null,
      targetId: (d.target_id as number | null) ?? null,
      failedStage: (d.failed_stage as string | null) ?? null,
      retryActionLabel: (d.retry_action_label as string | undefined) || undefined,
      preservedOutputs: Array.isArray(d.preserved_outputs)
        ? (d.preserved_outputs as string[])
        : [],
      resetScope: Array.isArray(d.reset_scope) ? (d.reset_scope as string[]) : [],
      lastAttempt: (d.last_attempt as number) || 0,
      retryRunning: Boolean(d.retry_running),
      lastError: (d.last_error as string | null) ?? null,
    }
  },

  getResults: async (projectId: number): Promise<CollusionResults> => {
    const response = await api.get<ApiResponse<Record<string, unknown>>>(
      `/projects/${projectId}/collusion/results`,
    )
    return mapCollusionResultsRaw(response.data) as CollusionResults
  },

  getReportDownloadUrl: (projectId: number) => {
    return `/api/projects/${projectId}/collusion/report/download`
  },

  getRetryState: async (projectId: number): Promise<WritingRetryState> => {
    const response = await api.get<ApiResponse<Record<string, unknown>>>(
      `/projects/${projectId}/collusion/retry-state`,
    )
    return transformRetryState(unwrapProgressStatusPayload(response, '串标重试状态'))
  },

  retryAnalysis: async (
    projectId: number,
    nodeKey?: string | null,
    targetId?: number | null,
  ): Promise<WritingRetryState> => {
    const response = await api.post<ApiResponse<Record<string, unknown>>>(
      `/projects/${projectId}/collusion/retry`,
      nodeKey || targetId != null ? { node_key: nodeKey, target_id: targetId } : {},
    )
    return transformRetryState(unwrapProgressStatusPayload(response, '串标重试触发'))
  },
}
