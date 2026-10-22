/**
 * 4 个统计筛选卡片：高置信 / 低置信 / 冲突 / 全局。
 * 从 WritingBinding.tsx L197-235 整段迁出（行为 byte-equal）。
 */

import React from 'react'
import type { BindingFilter } from '../types'

export interface BindingFilterCardsProps {
  highConf: number
  lowConf: number
  conflictCount: number
  globalCount: number
  confidenceFilter: BindingFilter
  onFilterClick: (filter: NonNullable<BindingFilter>) => void
}

export const BindingFilterCards: React.FC<BindingFilterCardsProps> = ({
  highConf, lowConf, conflictCount, globalCount, confidenceFilter, onFilterClick,
}) => {
  return (
    <div className="grid grid-cols-4 gap-4 mb-6">
      <button
        onClick={() => onFilterClick('high')}
        className={`bg-green-50 border border-green-200 rounded-lg p-4 text-center cursor-pointer transition hover:bg-green-100 ${
          confidenceFilter === 'high' ? 'ring-2 ring-green-400 ring-offset-2' : ''
        }`}
      >
        <p className="text-2xl font-bold text-green-700">{highConf}</p>
        <p className="text-sm text-green-600">高置信{confidenceFilter === 'high' ? ' (筛选中)' : ''}</p>
      </button>
      <button
        onClick={() => onFilterClick('low')}
        className={`bg-amber-50 border border-amber-200 rounded-lg p-4 text-center cursor-pointer transition hover:bg-amber-100 ${
          confidenceFilter === 'low' ? 'ring-2 ring-amber-400 ring-offset-2' : ''
        }`}
      >
        <p className="text-2xl font-bold text-amber-700">{lowConf}</p>
        <p className="text-sm text-amber-600">低置信{confidenceFilter === 'low' ? ' (筛选中)' : ''}</p>
      </button>
      <button
        onClick={() => onFilterClick('conflict')}
        className={`bg-red-50 border border-red-200 rounded-lg p-4 text-center cursor-pointer transition hover:bg-red-100 ${
          confidenceFilter === 'conflict' ? 'ring-2 ring-red-400 ring-offset-2' : ''
        }`}
      >
        <p className="text-2xl font-bold text-red-700">{conflictCount}</p>
        <p className="text-sm text-red-600">冲突{confidenceFilter === 'conflict' ? ' (筛选中)' : ''}</p>
      </button>
      <button
        onClick={() => onFilterClick('global')}
        className={`bg-slate-50 border border-slate-200 rounded-lg p-4 text-center cursor-pointer transition hover:bg-slate-100 ${
          confidenceFilter === 'global' ? 'ring-2 ring-slate-400 ring-offset-2' : ''
        }`}
      >
        <p className="text-2xl font-bold text-slate-700">{globalCount}</p>
        <p className="text-sm text-slate-600">全局约束{confidenceFilter === 'global' ? ' (筛选中)' : ''}</p>
      </button>
    </div>
  )
}
