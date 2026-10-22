import React from 'react'
import type { ErrorStats } from '../types'
import { formatTimeRangeLabel } from '../utils'

interface ErrorLogStatsProps {
  stats: ErrorStats | null
  loading: boolean
}

export const ErrorLogStats: React.FC<ErrorLogStatsProps> = ({ stats, loading }) => {
  if (loading || !stats) {
    return (
      <div className="grid grid-cols-5 gap-4 mb-6">
        {[1, 2, 3, 4, 5].map(i => (
          <div key={i} className="bg-white rounded-lg border p-4 animate-pulse">
            <div className="h-4 w-20 bg-gray-200 rounded mb-2" />
            <div className="h-7 w-12 bg-gray-200 rounded" />
          </div>
        ))}
      </div>
    )
  }

  const rangeLabel = formatTimeRangeLabel(stats.hours)
  const byCat = stats.by_category || {}
  const businessErrors = (byCat.bid_review || 0) + (byCat.collusion_check || 0) + (byCat.bid_writing || 0)

  const cards = [
    { label: `${rangeLabel}总计`, value: stats.total, color: 'text-gray-900' },
    { label: '未处理', value: stats.unresolved, color: stats.unresolved > 0 ? 'text-red-600' : 'text-green-600' },
    { label: '错误', value: stats.by_level?.ERROR || 0, color: 'text-red-600' },
    { label: '警告', value: stats.by_level?.WARNING || 0, color: 'text-amber-600' },
    { label: '业务相关', value: businessErrors, color: 'text-blue-600' },
  ]

  return (
    <div className="grid grid-cols-5 gap-4 mb-6">
      {cards.map(card => (
        <div key={card.label} className="bg-white rounded-lg border p-4">
          <p className="text-xs text-gray-500 mb-1">{card.label}</p>
          <p className={`text-2xl font-bold ${card.color}`}>{card.value}</p>
        </div>
      ))}
    </div>
  )
}
