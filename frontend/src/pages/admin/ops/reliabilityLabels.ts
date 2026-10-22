/**
 * LLM 可靠性面板专用标签字典。
 * 与 labels.ts 中的任务 outcome（success/failure）口径不同，切勿混用。
 */

export type LlmLabelState = 'labeled' | 'not_collected' | 'unrecognized'

export const LLM_OUTCOME_LABELS: Record<string, string> = {
  valid: '有效',
  empty: '空响应',
  invalid_json: 'JSON非法',
  schema_invalid: 'Schema失败',
  http_error: 'HTTP错误',
  transport_error: '网络错误',
  timeout: '超时',
  rate_limited: '限流',
  quota_exhausted: '配额耗尽',
  auth_error: '鉴权失败',
  degraded_plain_chat: '降级普通对话',
  cancelled: '已取消',
}

export const LLM_GROUP_OPTIONS: Array<{ key: string; label: string }> = [
  { key: 'purpose', label: '用途' },
  { key: 'model', label: '模型' },
  { key: 'provider', label: '厂商' },
  { key: 'config', label: '配置' },
  { key: 'node', label: '节点' },
  { key: 'flow', label: '业务流' },
]

export const LLM_LABEL_STATE_LABELS: Record<LlmLabelState, string> = {
  labeled: '已标注',
  not_collected: '未采集',
  unrecognized: '未识别',
}

export const LLM_COVERAGE_DIM_LABELS: Record<string, string> = {
  node: '节点',
  purpose: '用途',
  config: '配置',
  provider: '厂商',
}

export const LLM_PURPOSE_LABELS: Record<string, string> = {
  review: '审查',
  vision: '视觉',
  judge: '评判',
  extract: '提取',
}

/** 与 Settings AI 池区块顺序一致：视觉 → 提取 → 审查 */
export const LLM_PURPOSE_POOL_ORDER: readonly string[] = [
  'vision',
  'extract',
  'review',
]

export function purposePoolSortIndex(purpose: string): number {
  const idx = LLM_PURPOSE_POOL_ORDER.indexOf(purpose)
  return idx >= 0 ? idx : LLM_PURPOSE_POOL_ORDER.length
}

export const LLM_ADVICE_ACTION_LABELS: Record<string, string> = {
  promote: '提升为主用',
  demote: '降权',
  keep: '保持',
  insufficient_data: '数据不足',
}

export const LLM_ADVICE_STATUS_LABELS: Record<string, string> = {
  pending: '待确认',
  applied: '已写回',
  rolled_back: '已回滚',
  dismissed: '已忽略',
}

export const LLM_ADVICE_RULE_LABELS: Record<string, string> = {
  no_candidates: '无可用配置候选',
  all_insufficient_sample: '样本均不足',
  promote_best_valid_rate: '最优有效率达标，建议提升',
  best_already_preferred: '最优已是首选，保持',
  demote_worst_degraded: '最差劣化，建议降权',
  no_action_needed: '暂无需调整',
  node_insufficient_sample: '节点样本不足',
  node_readonly_best: '节点级只读建议',
  test_promote: '测试提升规则',
}

export const LLM_OPS_WINDOW_LABELS: Record<string, string> = {
  '1h': '近1小时',
  '6h': '近6小时',
  '24h': '近24小时',
  '7d': '近7天',
}

export function formatLlmOutcomeLabel(outcome: string): string {
  return LLM_OUTCOME_LABELS[outcome] || outcome
}

export function formatLlmLabelState(state?: string | null): string {
  if (!state) return LLM_LABEL_STATE_LABELS.not_collected
  return LLM_LABEL_STATE_LABELS[state as LlmLabelState] || state
}

export function formatCoveragePct(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`
}

export function isFailureOutcome(outcome: string): boolean {
  return outcome !== 'valid'
}

export function formatAdviceActionLabel(action?: string | null): string {
  if (!action) return '未知动作'
  return LLM_ADVICE_ACTION_LABELS[action] || '未知动作'
}

export function formatAdviceStatusLabel(status?: string | null): string {
  if (!status) return '未知状态'
  return LLM_ADVICE_STATUS_LABELS[status] || '未知状态'
}

export function formatAdviceRuleLabel(ruleId?: string | null): string {
  if (!ruleId) return '未知规则'
  return LLM_ADVICE_RULE_LABELS[ruleId] || '未知规则'
}

export function formatPurposeLabel(purpose?: string | null): string {
  if (!purpose) return '未采集'
  return LLM_PURPOSE_LABELS[purpose] || purpose
}

export function formatOpsWindowLabel(window?: string | null): string {
  if (!window) return '未知窗口'
  return LLM_OPS_WINDOW_LABELS[window] || window
}
