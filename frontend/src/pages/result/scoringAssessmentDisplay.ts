import { sanitizeUserFacingText } from '../../services/progressStatusTransforms.ts'

const STATUS_LABELS: Record<string, string> = {
  met: '达标',
  partial: '部分达标',
  not: '未达标',
  unknown: '待定',
}

const RULE_TEXT_KEYS = [
  'rule_excerpt',
  'ruleExcerpt',
  'text',
  'content',
  'description',
  'label',
  'name',
]

export function getScoringStatusLabel(status: string | undefined): string {
  return STATUS_LABELS[status ?? ''] ?? '待定'
}

export function formatScoringRuleText(rule: unknown): string {
  if (typeof rule === 'string') {
    return sanitizeUserFacingText(rule) || '评分规则已结构化记录'
  }

  if (!rule || typeof rule !== 'object') {
    return '评分规则已结构化记录'
  }

  const record = rule as Record<string, unknown>
  for (const key of RULE_TEXT_KEYS) {
    const value = record[key]
    if (typeof value === 'string' && value.trim()) {
      return sanitizeUserFacingText(value) || '评分规则已结构化记录'
    }
  }

  return '评分规则已结构化记录'
}
