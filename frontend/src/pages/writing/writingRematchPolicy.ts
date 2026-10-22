/**
 * 重新匹配决策策略（纯函数）。
 *
 * 职责: 承载"保留人工审核重新匹配"与"重置审核并重新匹配"两种模式的文案与判定。
 * 不做: 不发起请求、不持有组件状态、不直接操作 store。
 *
 * : 默认重新匹配保留人工 confirmed/rejected，仅重算 suggested；
 * 只有用户显式选择"重置审核"才清空全部。文案必须区分两种模式，避免误导。
 */

export type RematchMode = 'keep' | 'reset'

export interface RematchModeContent {
  title: string
  description: string
  confirmLabel: string
  danger: boolean
}

const REMATCH_CONTENT: Record<RematchMode, RematchModeContent> = {
  keep: {
    title: '重新匹配',
    description:
      '将保留你已确认和已拒绝的绑定，仅对未处理（建议）的绑定重新计算。适合补充素材或微调后再次匹配。',
    confirmLabel: '保留审核并重新匹配',
    danger: false,
  },
  reset: {
    title: '重置审核并重新匹配',
    description:
      '将清空全部绑定（含已确认与已拒绝），完全重新匹配。此前的人工审核结果不可恢复，请谨慎操作。',
    confirmLabel: '清空并重新匹配',
    danger: true,
  },
}

export function getRematchModeContent(mode: RematchMode): RematchModeContent {
  return REMATCH_CONTENT[mode]
}

export function rematchResetFlag(mode: RematchMode): boolean {
  return mode === 'reset'
}
