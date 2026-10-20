/**
 * B1: 统一失败合同状态类型定义。
 * 对应 docs/failure_contract.md §2.1 + §5.1。
 */

export type FailureState =
  | 'completed'
  | 'degraded'
  | 'needs_review'
  | 'inconclusive'
  | 'failed'

export interface FailureStateConfig {
  label: string
  foreground: string
  background: string
  icon: 'check' | 'triangle' | 'exclamation' | 'question' | 'x-circle'
}

export const FAILURE_STATE_CONFIG: Record<FailureState, FailureStateConfig> = {
  completed: {
    label: '',
    foreground: '',
    background: '',
    icon: 'check',
  },
  degraded: {
    label: '降级分析',
    foreground: 'text-amber-800',
    background: 'bg-amber-50',
    icon: 'triangle',
  },
  needs_review: {
    label: '待复核',
    foreground: 'text-orange-700',
    background: 'bg-orange-50',
    icon: 'exclamation',
  },
  inconclusive: {
    label: '信息不足',
    foreground: 'text-gray-600',
    background: 'bg-gray-100',
    icon: 'question',
  },
  failed: {
    label: '分析失败',
    foreground: 'text-red-800',
    background: 'bg-red-50',
    icon: 'x-circle',
  },
}

export const getFailureStateConfig = (
  state: FailureState | string | undefined,
): FailureStateConfig | null => {
  if (!state || state === 'completed') return null
  return FAILURE_STATE_CONFIG[state as FailureState] ?? null
}
