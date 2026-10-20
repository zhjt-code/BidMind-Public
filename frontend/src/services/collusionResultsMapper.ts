import { transformCollusionPairResultRaw, transformCollusionFindingRaw } from './collusionTransforms.ts'
import { sanitizeUserFacingText, sanitizeUserFacingList } from './progressStatusTransforms.ts'

// A3: 结果文本的前端兜底闸门 — 与后端 i18n.internal_to_display 同义。
// 后端已统一清洗，此处幂等兜底，避免新枚举值在后端尚未补齐映射时复发。
const sx = (text: string | null | undefined): string => sanitizeUserFacingText(text)
const sxList = (items: string[] | null | undefined): string[] =>
  sanitizeUserFacingList(items)

export const mapCollusionResultsRaw = (d: Record<string, unknown>) => ({
  projectId: d.project_id as number,
  overallRisk: d.overall_risk as string,
  projectDecision: (d.project_decision as string) || 'partial_review_needed',
  projectRiskLevel: (d.project_risk_level as string | null | undefined) ?? null,
  decisionConfidence: (d.decision_confidence as string) || 'low',
  reviewRequired: Boolean(d.review_required),
  decisionBasis: (d.decision_basis as string) || 'pairs',
  decisionEngineVersion: (d.decision_engine_version as number) || 1,
  overallRiskBasis: (d.overall_risk_basis as string) || 'pairs',
  riskDistributionBasis: (d.risk_distribution_basis as string) || 'pairs',
  riskConsistencyStatus: (d.risk_consistency_status as string) || 'aligned',
  riskConsistencyNote: sx(d.risk_consistency_note as string | undefined) || undefined,
  hasTenderDoc: d.has_tender_doc as boolean,
  summarySourceVersion: (d.summary_source_version as number) || 1,
  dataQualityFlags: sxList((d.data_quality_flags as string[]) || []),
  profileQualityFlags: sxList((d.profile_quality_flags as string[]) || []),
  coverageStats: {
    totalPairs: ((d.coverage_stats as Record<string, unknown> | undefined)?.total_pairs as number) || 0,
    analyzedPairs: ((d.coverage_stats as Record<string, unknown> | undefined)?.analyzed_pairs as number) || 0,
    llmAnalyzedPairs: ((d.coverage_stats as Record<string, unknown> | undefined)?.llm_analyzed_pairs as number) || 0,
    algorithmOnlyPairs: ((d.coverage_stats as Record<string, unknown> | undefined)?.algorithm_only_pairs as number) || 0,
    skippedPairs: ((d.coverage_stats as Record<string, unknown> | undefined)?.skipped_pairs as number) || 0,
    failedPairs: ((d.coverage_stats as Record<string, unknown> | undefined)?.failed_pairs as number) || 0,
    inconclusivePairs: ((d.coverage_stats as Record<string, unknown> | undefined)?.inconclusive_pairs as number) || 0,
    reviewRequiredPairs: ((d.coverage_stats as Record<string, unknown> | undefined)?.review_required_pairs as number) || 0,
    clearPairs: ((d.coverage_stats as Record<string, unknown> | undefined)?.clear_pairs as number) || 0,
    suspiciousPairs: ((d.coverage_stats as Record<string, unknown> | undefined)?.suspicious_pairs as number) || 0,
    coverageRatio: ((d.coverage_stats as Record<string, unknown> | undefined)?.coverage_ratio as number) || 0,
  },
  industryProfileSnapshot: (d.industry_profile_snapshot as Record<string, unknown> | null) || null,
  industryProfileConfidence: d.industry_profile_confidence as string | undefined,
  industryProfileStatus: d.industry_profile_status as string | undefined,
  industryProfileSignals: (d.industry_profile_signals as Record<string, unknown>[]) || [],
  factualExplanation: sx(d.factual_explanation as string | undefined) || undefined,
  factualExplanationItems: sxList((d.factual_explanation_items as string[]) || []),
  similarityMatrix: d.similarity_matrix as (number | null)[][],
  documentLabels: d.document_labels as string[],
  pairResults: ((d.pair_results as unknown[]) || []).map((r) =>
    transformCollusionPairResultRaw(r as Record<string, unknown>)
  ),
  suspectedGroups: ((d.suspected_groups as unknown[]) || []).map((g) => {
    const group = g as Record<string, unknown>
    return {
      documentIds: (group.document_ids as number[]) || [],
      documentNames: (group.document_names as string[]) || [],
      riskLevel: (group.risk_level as string) || 'medium',
      reason: sx(group.reason as string) || '',
    }
  }),
  groups: ((d.groups as unknown[]) || []).map((g) => {
    const gr = g as Record<string, unknown>
    return {
      groupIndex: gr.group_index as number,
      memberDocIds: (gr.member_doc_ids as number[]) || [],
      memberNames: (gr.member_names as string[]) || [],
      riskLevel: gr.risk_level as string,
      groupConfidence: gr.group_confidence as number | undefined,
      uncertainEdgeCount: (gr.uncertain_edge_count as number) || 0,
      roles: ((gr.roles as unknown[]) || []).map((r) => {
        const role = r as Record<string, unknown>
        return {
          docId: role.doc_id as number,
          companyName: role.company_name as string || '',
          role: role.role as string || '角色不明',
          roleEvidence: sx(role.role_evidence as string) || '',
        }
      }),
      collusionStrategy: sx(gr.collusion_strategy as string) || undefined,
      keyEvidenceSummary: sx(gr.key_evidence_summary as string) || undefined,
      investigationFocus: sxList((gr.investigation_focus as string[]) || []),
      relatedPairIds: (gr.related_pair_ids as string[]) || [],
      networkDescription: sx(gr.network_description as string) || undefined,
      findings: ((gr.findings as unknown[]) || []).map(transformCollusionFindingRaw),
    }
  }),
  globalFindings: ((d.global_findings as unknown[]) || []).map((f) => {
    const fi = f as Record<string, unknown>
    return {
      type: fi.type as string,
      involvedDocIds: (fi.involved_doc_ids as number[]) || [],
      involvedNames: (fi.involved_names as string[]) || [],
      description: sx(fi.description as string) || '',
    }
  }),
  synthesisSummary: sx(d.synthesis_summary as string) || undefined,
  narrativeSummary: sx(d.narrative_summary as string) || undefined,
  investigationSuggestions: sxList((d.investigation_suggestions as string[]) || []),
  extractionWarnings: sxList((d.extraction_warnings as string[]) || []),
})
