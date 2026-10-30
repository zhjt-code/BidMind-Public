/**
 * 职责: 把「审查未产出有效符合结论」从符合程度轴上拆开。
 * 不做: 不改 complianceLevel 枚举；不收录 needs_review / 交叉冲突等人审项。
 */

export const PENDING_JUDGMENT_FILTER = 'pending'

export const PENDING_JUDGMENT_LABEL = '待判定'

export const PENDING_JUDGMENT_TOOLTIP =
  '系统未能完成该项审查，需人工核对。不代表投标文件未响应。'

/** 皇家紫 #722169：与部分符合的黄、不符合的正红区分，表示「需处理但不是符合结论」。 */
export const PENDING_JUDGMENT_HEX = '#722169'

export const PENDING_JUDGMENT_DOT = 'bg-[#722169]'

export const PENDING_JUDGMENT_COLOR = PENDING_JUDGMENT_HEX

export const PENDING_JUDGMENT_BADGE_CLASS =
  'border-transparent bg-[#722169] text-white shadow-sm hover:bg-[#722169]/80'

export const COMPLIANCE_NAV_VALUES = [
  'pending',
  'not',
  'partially',
  'fully',
  'missing',
] as const

export type PendingJudgmentFields = {
  pendingJudgment?: boolean
  complianceLevel: string
}

export function isPendingJudgment(result: { pendingJudgment?: boolean }): boolean {
  return result.pendingJudgment === true
}

export function matchesComplianceFilter(
  result: PendingJudgmentFields,
  filter: string,
): boolean {
  if (filter === 'all') return true
  if (filter === PENDING_JUDGMENT_FILTER) return isPendingJudgment(result)
  if (isPendingJudgment(result)) return false
  return result.complianceLevel === filter
}

export function complianceCountKey(result: PendingJudgmentFields): string {
  return isPendingJudgment(result) ? PENDING_JUDGMENT_FILTER : result.complianceLevel
}

export function getComplianceDisplayLabel(
  result: PendingJudgmentFields,
  labels: Record<string, string>,
): string {
  if (isPendingJudgment(result)) return PENDING_JUDGMENT_LABEL
  return labels[result.complianceLevel] || result.complianceLevel
}

export function complianceSortRank(
  result: PendingJudgmentFields,
  order: Record<string, number>,
): number {
  if (isPendingJudgment(result)) return 0
  return order[result.complianceLevel] ?? 99
}

export function isSubstantiveGap(result: PendingJudgmentFields): boolean {
  if (isPendingJudgment(result)) return false
  return result.complianceLevel === 'not' || result.complianceLevel === 'missing'
}
