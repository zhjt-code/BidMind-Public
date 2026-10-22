import { DOMAIN_HEX, DOMAIN_LABEL } from '../../../lib/domainTheme.ts'

export const PROJECT_TYPE_STYLE = {
  bid_writing: { label: DOMAIN_LABEL.bid_writing, color: DOMAIN_HEX.bid_writing, softClass: 'bg-teal-50 text-teal-700 border-teal-200' },
  bid_review: { label: DOMAIN_LABEL.bid_review, color: DOMAIN_HEX.bid_review, softClass: 'bg-blue-50 text-blue-700 border-blue-200' },
  collusion_check: { label: DOMAIN_LABEL.collusion_check, color: DOMAIN_HEX.collusion_check, softClass: 'bg-[#F6F1FC] text-[#6C40A2] border-[#E3D5F5]' },
} as const

export const STATUS_GROUP_STYLE = {
  pending: { label: '待处理', color: '#94a3b8', softClass: 'bg-slate-50 text-slate-600 border-slate-200' },
  processing: { label: '处理中', color: '#f97316', softClass: 'bg-orange-50 text-orange-700 border-orange-200' },
  completed: { label: '已完成', color: '#22c55e', softClass: 'bg-green-50 text-green-700 border-green-200' },
  failed: { label: '失败项目', color: '#ef4444', softClass: 'bg-red-50 text-red-700 border-red-200' },
} as const

export type ProjectTypeKey = keyof typeof PROJECT_TYPE_STYLE
export type StatusGroupKey = keyof typeof STATUS_GROUP_STYLE

export function getProjectTypeColor(key: string): string {
  return PROJECT_TYPE_STYLE[key as ProjectTypeKey]?.color ?? '#64748b'
}

export function getStatusGroupColor(key: string): string {
  return STATUS_GROUP_STYLE[key as StatusGroupKey]?.color ?? '#64748b'
}

export function getDashboardScopeLabel(scope: 'mine' | 'all'): string {
  return scope === 'all' ? '全系统项目' : '我的项目'
}
