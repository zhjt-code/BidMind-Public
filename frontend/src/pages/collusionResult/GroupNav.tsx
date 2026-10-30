import React from 'react'
import type { CollusionGroupResult } from '@/services/collusionApi'

const RISK_BADGE: Record<string, { bg: string; text: string; label: string }> = {
  high: { bg: 'bg-red-100', text: 'text-red-700', label: '高风险' },
  medium: { bg: 'bg-orange-100', text: 'text-orange-700', label: '中风险' },
  low: { bg: 'bg-yellow-100', text: 'text-yellow-700', label: '低风险' },
}

interface GroupNavProps {
  groups: CollusionGroupResult[]
  selectedGroupIdx: number | null
  onSelectGroup: (idx: number) => void
}

const GroupNav: React.FC<GroupNavProps> = ({ groups, selectedGroupIdx, onSelectGroup }) => {
  if (groups.length === 0) return null

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide px-2">
        嫌疑群组 ({groups.length})
      </h3>
      {groups.map((group) => {
        const isSelected = selectedGroupIdx === group.groupIndex
        const badge = RISK_BADGE[group.riskLevel] || RISK_BADGE.medium

        return (
          <div
            key={group.groupIndex}
            className={`w-full text-left p-3 rounded-lg border transition-colors ${
              isSelected
                ? 'border-[#9056D8] bg-[#F6F1FC] shadow-xs'
                : 'border-gray-200 bg-white hover:border-[#C4B0EA] hover:bg-[#F6F1FC]/70'
            }`}
          >
            <button
              type="button"
              onClick={() => onSelectGroup(group.groupIndex)}
              className="w-full text-left"
            >
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-sm font-medium text-gray-900">
                  群组 {group.groupIndex + 1}
                </span>
                <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${badge.bg} ${badge.text}`}>
                  {badge.label}
                </span>
              </div>
              <div className="flex flex-wrap gap-1 text-xs text-gray-600">
                {group.memberNames.slice(0, 4).map((name, i) => (
                  <span key={`${group.groupIndex}-member-${name}-${i}`} className="inline-flex items-center">
                    {i > 0 && <span className="mr-1 text-gray-400">+</span>}
                    <span className="font-medium truncate max-w-[80px] inline-block align-bottom" title={name}>{name}</span>
                  </span>
                ))}
                {group.memberNames.length > 4 && (
                  <span className="text-gray-400">+{group.memberNames.length - 4}</span>
                )}
              </div>
              {group.relatedPairIds.length > 0 && (
                <div className="mt-1 text-xs text-gray-400">
                  {group.relatedPairIds.length} 对关联证据
                </div>
              )}
              {(group.groupConfidence !== undefined || group.uncertainEdgeCount > 0) && (
                <div className="mt-1 text-[11px] text-gray-500">
                  可信度 {group.groupConfidence !== undefined ? `${Math.round(group.groupConfidence * 100)}%` : '--'}
                  {group.uncertainEdgeCount > 0 ? ` · 待复核边 ${group.uncertainEdgeCount}` : ''}
                </div>
              )}
            </button>
          </div>
        )
      })}
    </div>
  )
}

export default GroupNav
