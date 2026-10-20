/**
 * 职责: 解析后端 requirements 响应中的评分三态权威结论 scoring_verdict（verdict 单源）。
 * 不做: 不推导三态结论本身、不渲染 UI、不解析评分报告（totals/ledger 归 requirementReviewTransforms）。
 *
 * 后端 VLM 救援触发与前端横幅必须消费同一结论；null 表示无评分数据（横幅本就不渲染）。
 */
export type ScoringVerdict = 'green' | 'blue' | 'amber'

export function transformScoringVerdict(raw: unknown): ScoringVerdict | null {
  return raw === 'green' || raw === 'blue' || raw === 'amber' ? raw : null
}
