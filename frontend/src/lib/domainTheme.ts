/**
 * 职责: 三大业务域身份色的零依赖决策源
 * 不做: 不生成 Tailwind class、不承担分类色或状态语义色
 *
 * 串标实色锚在紫蓝深端。亮端只用于大图标渐变，徽标与图表不用亮端，
 * 避免贴近审查蓝和站内靛蓝。处理中/风险/警告继续用橙色，不进本文件。
 */

export type DomainProjectType = 'bid_writing' | 'bid_review' | 'collusion_check'
export type DomainAccent = 'teal' | 'blue' | 'iris'

export const DOMAIN_ACCENT = {
  bid_writing: 'teal',
  bid_review: 'blue',
  collusion_check: 'iris',
} as const satisfies Record<DomainProjectType, DomainAccent>

export const DOMAIN_HEX = {
  bid_writing: '#14b8a6',
  bid_review: '#2563eb',
  collusion_check: '#9056D8',
} as const satisfies Record<DomainProjectType, string>

/** 图标渐变亮端。写标、审查与实色同相；串标亮端单独给出。 */
export const DOMAIN_HEX_HIGHLIGHT = {
  bid_writing: '#14b8a6',
  bid_review: '#2563eb',
  collusion_check: '#7165EA',
} as const satisfies Record<DomainProjectType, string>

export const DOMAIN_LABEL = {
  bid_writing: '标书编写',
  bid_review: '投标审查',
  collusion_check: '串标审查',
} as const satisfies Record<DomainProjectType, string>
