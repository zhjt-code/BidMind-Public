import React from 'react'

export type VerdictType = 'improving' | 'stable' | 'warn' | 'critical' | 'neutral'

const VERDICT_STYLES: Record<VerdictType, { bg: string; text: string; label: string; dot: string }> = {
  improving: { bg: 'bg-emerald-50', text: 'text-emerald-700', label: '向好', dot: 'bg-emerald-500' },
  stable: { bg: 'bg-gray-50', text: 'text-gray-600', label: '平稳', dot: 'bg-gray-400' },
  warn: { bg: 'bg-amber-50', text: 'text-amber-700', label: '注意', dot: 'bg-amber-500' },
  critical: { bg: 'bg-rose-50', text: 'text-rose-700', label: '恶化', dot: 'bg-rose-500' },
  neutral: { bg: 'bg-slate-50', text: 'text-slate-500', label: '中性', dot: 'bg-slate-400' },
}

interface Props {
  verdict: VerdictType
  size?: 'sm' | 'md'
}

const HealthBadge: React.FC<Props> = ({ verdict, size = 'sm' }) => {
  const style = VERDICT_STYLES[verdict] || VERDICT_STYLES.neutral
  const pad = size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-2.5 py-1 text-sm'
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full ${pad} ${style.bg} ${style.text} font-medium`}>
      <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
      {style.label}
    </span>
  )
}

export default HealthBadge
