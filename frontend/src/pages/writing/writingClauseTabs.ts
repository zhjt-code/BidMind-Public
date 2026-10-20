import type { WritingClause } from '../../services/writingClauseTransforms'

export const INDUSTRY_PROFILE_TAB = 'industry_profile'
export const INDUSTRY_PROFILE_LAYER = 'generation_guide'

export const LAYER_TABS = [
  { value: 'hard_constraint', label: '合规要求' },
  { value: 'structure_constraint', label: '内容要求' },
  { value: 'generation_guide', label: '写作引导' },
  { value: INDUSTRY_PROFILE_TAB, label: '行业画像' },
]

export const SUB_TYPE_LABELS: Record<string, string> = {
  disqualification: '废标条款',
  fixed_template: '固定模板',
  format_rule: '格式规定',
  submission_rule: '提交规则',
  submission_notice: '提交注意事项',
  directory_rule: '目录规则',
  material_required: '必备材料',
  content_required: '内容要求',
  table_format: '表格格式',
  scoring_criterion: '评分标准',
  project_context: '项目背景',
  emphasis_point: '重点事项',
  differentiation_hint: '差异化提示',
  industry_profile: '行业画像',
  bid_structure_analysis: '标书结构',
}

export const SUB_TYPE_OPTIONS_BY_LAYER: Record<string, string[]> = {
  hard_constraint: ['disqualification', 'fixed_template', 'format_rule', 'submission_rule', 'submission_notice'],
  structure_constraint: ['directory_rule', 'material_required', 'content_required', 'table_format'],
  generation_guide: ['scoring_criterion', 'project_context', 'emphasis_point', 'differentiation_hint', 'industry_profile'],
}

export const HIDDEN_WRITING_CLAUSE_SUB_TYPES = new Set(['bid_structure_analysis', 'bid_directory_spec'])

// 覆盖统计分母排除集，与后端 clause_coverage_checker.HIDDEN_COVERAGE_SUB_TYPES /
// material_matcher._EXCLUDED_SUB_TYPES 单一同源。这些约束不参与"约束-章节"正文匹配，
// 各走旁路消费通道，不应计入"约束消费"覆盖分母。区别于 HIDDEN_WRITING_CLAUSE_SUB_TYPES：
// 后者仅控制约束页 Tab 可见性，提交规则/提交注意事项等仍需在约束页正常展示。
export const COVERAGE_HIDDEN_WRITING_CLAUSE_SUB_TYPES = new Set([
  'bid_structure_analysis',
  'bid_directory_spec',
  'render_profile',
  'industry_profile',
  'submission_notice',
  'submission_rule',
])

export function isIndustryProfileClause(clause: WritingClause): boolean {
  return clause.subType === INDUSTRY_PROFILE_TAB
}

export function isVisibleWritingClause(clause: WritingClause): boolean {
  return !HIDDEN_WRITING_CLAUSE_SUB_TYPES.has(clause.subType)
}

export function isCoverageCountedClause(clause: WritingClause): boolean {
  if (clause.metadataJson?.writing_relevance_source !== 'manual'
    && ['reference', 'check_only'].includes(String(clause.metadataJson?.writing_relevance || ''))) {
    return false
  }
  return !COVERAGE_HIDDEN_WRITING_CLAUSE_SUB_TYPES.has(clause.subType)
}

export function isClauseVisibleInTab(clause: WritingClause, activeTab: string): boolean {
  if (!isVisibleWritingClause(clause)) return false
  if (activeTab === INDUSTRY_PROFILE_TAB) return isIndustryProfileClause(clause)
  if (activeTab === INDUSTRY_PROFILE_LAYER) {
    return clause.layer === INDUSTRY_PROFILE_LAYER && !isIndustryProfileClause(clause)
  }
  return clause.layer === activeTab
}

export function getVisibleClausesForTab(clauses: WritingClause[], activeTab: string): WritingClause[] {
  return clauses.filter((clause) => isClauseVisibleInTab(clause, activeTab))
}

export function getClauseTabCount(clauses: WritingClause[], tabValue: string): number {
  return getVisibleClausesForTab(clauses, tabValue).length
}

export function getClauseTabDisplayLabel(clauses: WritingClause[], tab: { value: string; label: string }): string {
  if (tab.value === INDUSTRY_PROFILE_TAB) return tab.label
  return `${tab.label} (${getClauseTabCount(clauses, tab.value)})`
}

export function shouldShowClauseGroupCount(subType: string): boolean {
  return subType !== INDUSTRY_PROFILE_TAB
}

export function shouldShowClauseGroupHeading(subType: string): boolean {
  return subType !== INDUSTRY_PROFILE_TAB
}

export function getVisibleClauseCount(clauses: WritingClause[]): number {
  return clauses.filter(isVisibleWritingClause).length
}

export function getClauseCreateTarget(activeTab: string, subType?: string): { layer: string; subType?: string } {
  if (activeTab === INDUSTRY_PROFILE_TAB) {
    return { layer: INDUSTRY_PROFILE_LAYER, subType: INDUSTRY_PROFILE_TAB }
  }
  return { layer: activeTab, subType }
}

export function getClauseLayerDisplayLabel(layer: string, subType?: string): string {
  const tabValue = subType === INDUSTRY_PROFILE_TAB ? INDUSTRY_PROFILE_TAB : layer
  return LAYER_TABS.find((tab) => tab.value === tabValue)?.label || layer
}
