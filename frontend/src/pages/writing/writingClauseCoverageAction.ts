export type CoverageBindingFocus = 'pendingAssignment' | 'unmatched'

export interface CoverageActionCounts {
  pendingAssignment: number
  unmatched: number
}

export interface CoverageAction {
  label: string
  focus: CoverageBindingFocus
}

export function getCoverageAction(counts: CoverageActionCounts): CoverageAction | null {
  if (counts.pendingAssignment > 0) {
    return { label: '处理待分配约束', focus: 'pendingAssignment' }
  }
  if (counts.unmatched > 0) {
    return { label: '查看未匹配约束', focus: 'unmatched' }
  }
  return null
}
