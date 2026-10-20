/**
 * 职责: 值班/调试档状态条与业务域身份井的 Tailwind class 映射
 * 不做: KPI 口径、请求、状态文案
 */
import type { VerdictType } from './components/HealthBadge'

export const KPI_FRAME = 'bg-white rounded-lg border border-gray-200 p-4 border-l-4'

const BAR: Record<VerdictType, string> = {
  improving: 'border-l-emerald-500',
  stable: 'border-l-slate-300',
  warn: 'border-l-amber-500',
  critical: 'border-l-rose-500',
  neutral: 'border-l-slate-300',
}

const LABEL: Record<VerdictType, string> = {
  improving: 'text-emerald-700',
  stable: 'text-slate-500',
  warn: 'text-amber-700',
  critical: 'text-rose-700',
  neutral: 'text-slate-500',
}

export function verdictBarClass(verdict: VerdictType): string {
  return BAR[verdict]
}

export function verdictLabelClass(verdict: VerdictType): string {
  return LABEL[verdict]
}

export function verdictValueClass(verdict: VerdictType): string {
  if (verdict === 'critical' || verdict === 'warn') return LABEL[verdict]
  return 'text-gray-900'
}

export const FLOW_IDENTITY: Record<'tender' | 'collusion' | 'writer', { well: string; icon: string }> = {
  tender: { well: 'bg-blue-50 border-blue-100/80', icon: 'text-primary' },
  collusion: { well: 'bg-[#F6F1FC] border-[#E3D5F5]/80', icon: 'text-[#9056D8]' },
  writer: { well: 'bg-teal-50 border-teal-100/80', icon: 'text-teal-600' },
}
