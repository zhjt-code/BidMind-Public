import { sanitizeUserFacingText, sanitizeUserFacingList } from './progressStatusTransforms.ts'
import { getCrossFieldRiskPatternLabel } from '../lib/constants.ts'

// A3: 串标证据 / 文件对的前端兜底中文化闸门 — 与后端 i18n 同义、幂等。
const sx = (text: string | null | undefined): string =>
  sanitizeUserFacingText(text)
const sxList = (items: string[] | null | undefined): string[] =>
  sanitizeUserFacingList(items)

// 纯内部诊断标记：对用户无展示价值，前端直接丢弃（对齐 移除无感知价值字段）。
const INTERNAL_QUALITY_FLAGS = new Set(['flat_finding_recovered'])

/**
 * pair 级 quality_flags 专用清洗：
 * 1) 丢弃纯内部诊断标记（如 flat_finding_recovered）；
 * 2) 跨字段命中标记 `cross_field:<risk_pattern>` 翻译为中文提示；
 * 3) 其余按通用中文化闸门兜底，杜绝英文标识裸出。
 */
function sanitizeQualityFlags(flags: string[] | null | undefined): string[] {
  if (!flags || flags.length === 0) return []
  const out: string[] = []
  for (const raw of flags) {
    const flag = String(raw ?? '').trim()
    if (!flag || INTERNAL_QUALITY_FLAGS.has(flag)) continue
    if (flag.startsWith('cross_field:')) {
      out.push(`跨字段命中：${getCrossFieldRiskPatternLabel(flag.slice('cross_field:'.length))}`)
      continue
    }
    const cleaned = sanitizeUserFacingText(flag)
    if (cleaned) out.push(cleaned)
  }
  return out
}

export function transformCollusionFindingRaw(f: unknown) {
  const fi = f as Record<string, unknown>
  return {
    dimension: fi.dimension as string,
    level: fi.level as string,
    description: sx(fi.description as string),
    detail: sx(fi.detail as string) || undefined,
    evidence: sx(fi.evidence as string) || undefined,
    quoteA: fi.quote_a as string | undefined,
    pageA: fi.page_a as string | undefined,
    quoteB: fi.quote_b as string | undefined,
    pageB: fi.page_b as string | undefined,
    pageRef: fi.page_ref as string | undefined,
    similarity: fi.similarity as number | undefined,
    certainty: fi.certainty as number | undefined,
    evidences: ((fi.evidences as unknown[]) || []).map((ev) => {
      const e = ev as Record<string, unknown>
      return {
        docId: e.doc_id as number | undefined,
        docName: e.doc_name as string | undefined,
        quote: e.quote as string | undefined,
        location: e.location as string | undefined,
      }
    }),
    involvedDocIds: (fi.involved_doc_ids as number[]) || [],
    involvedDocNames: (fi.involved_doc_names as string[]) || [],
    riskPattern: fi.risk_pattern as string | undefined,
    sourceFieldA: fi.source_field_a as string | undefined,
    sourceFieldB: fi.source_field_b as string | undefined,
    legalReference: fi.legal_reference as string | undefined,
  }
}

export function transformCollusionPairResultRaw(r: Record<string, unknown>) {
  return {
    pairId: r.pair_id as string | undefined,
    docAId: r.doc_a_id as number,
    docBId: r.doc_b_id as number,
    docAName: r.doc_a_name as string,
    docBName: r.doc_b_name as string,
    overallRisk: r.overall_risk as string,
    riskLevel: (r.risk_level as string | null | undefined) ?? undefined,
    similarityScore: r.similarity_score as number | undefined,
    d1PersonnelOverlap: r.d1_personnel_overlap as number | undefined,
    d2CompanyRelation: r.d2_company_relation as number | undefined,
    d3TextSimilarity: r.d3_text_similarity as number | undefined,
    d4PricePattern: r.d4_price_pattern as number | undefined,
    d5QualificationOverlap: r.d5_qualification_overlap as number | undefined,
    d6FormatSimilarity: r.d6_format_similarity as number | undefined,
    d7AbnormalConsistency: r.d7_abnormal_consistency as number | undefined,
    d8MetadataSimilarity: r.d8_metadata_similarity as number | undefined,
    findings: ((r.findings as unknown[]) || []).map(transformCollusionFindingRaw),
    analysisMode: (r.analysis_mode as string) || 'algorithm_only',
    reviewState: r.review_state as string | undefined,
    analysisState: (r.analysis_state as string) || 'completed',
    decisionState: (r.decision_state as string) || 'decidable',
    failureState: (r.failure_state as string | undefined) ?? (r.failureState as string | undefined),
    qualityFlags: sanitizeQualityFlags((r.quality_flags as string[]) || []),
    consistencyFlags: sxList((r.consistency_flags as string[]) || []),
    riskExplanation: sx(r.risk_explanation as string) || '',
    dimensionCertainties: r.dimension_certainties as Record<string, number> | undefined,
    // 重合片段（向量预筛选高相似 chunk 对，含疑似通用模板标注），供人工甄别"哪几段重合"
    evidenceChunks: ((r.evidence_chunks as unknown[]) || []).map((ec) => {
      const e = ec as Record<string, unknown>
      return {
        textA: sx(e.text_a as string) || '',
        textB: sx(e.text_b as string) || '',
        pageA: (e.page_a as number | null | undefined) ?? undefined,
        pageB: (e.page_b as number | null | undefined) ?? undefined,
        score: (e.score as number | null | undefined) ?? undefined,
        literalScore: (e.literal_score as number | null | undefined) ?? undefined,
        boilerplateFlag: Boolean(e.boilerplate_flag),
        kbRuleMatch: Boolean(e.kb_rule_match),
      }
    }),
    // P2-3: 后端 /results 已组装 attention_items（business_relationship / unverified_quote 等），
    // 映射为前端 camelCase 结构供 pair 详情页渲染
    attentionItems: ((r.attention_items as unknown[]) || []).map((ai) => {
      const a = ai as Record<string, unknown>
      return {
        category: (a.category as string) || '',
        dimension: a.dimension as string | undefined,
        summary: sx(a.summary as string) || '',
        detail: sx(a.detail as string) || undefined,
        sourceReason: sx(a.source_reason as string) || undefined,
      }
    }),
  }
}
