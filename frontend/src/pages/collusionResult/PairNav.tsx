import React, { useMemo, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { CollusionPairResult } from '@/services/collusionApi'
import {
  getRiskConfig, RISK_CONFIG,
  EVIDENCE_LEVEL_LABELS,
  type CollusionFilterState, type CollusionFilterCounts,
} from './types'
import {
  pairContainsEvidenceLevel,
  type EvidenceLevelFilter,
} from './evidenceFiltering'

interface PairNavProps {
  sortedPairs: CollusionPairResult[]
  filters: CollusionFilterState
  filterCounts: CollusionFilterCounts
  selectedPairKey: string | null
  onFilterChange: (partial: Partial<CollusionFilterState>) => void
  onSelectPair: (pair: CollusionPairResult) => void
  hideFilters?: boolean
}

interface SectionProps {
  title: string
  defaultOpen?: boolean
  children: React.ReactNode
}

const NavSection: React.FC<SectionProps> = ({ title, defaultOpen = true, children }) => {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="mb-1">
      <button
        className="flex items-center w-full px-3 py-2 text-xs font-semibold text-gray-500 uppercase tracking-wider hover:bg-gray-50"
        onClick={() => setOpen(!open)}
      >
        {open ? <ChevronDown className="h-3 w-3 mr-1" /> : <ChevronRight className="h-3 w-3 mr-1" />}
        {title}
      </button>
      {open && <div className="pb-1">{children}</div>}
    </div>
  )
}

interface NavItemProps {
  label: string
  count: number
  active: boolean
  disabled?: boolean
  onClick: () => void
  dotColor?: string
}

const NavItem: React.FC<NavItemProps> = ({ label, count, active, disabled, onClick, dotColor }) => (
  <button
    className={`flex items-center justify-between w-full px-3 py-1.5 text-sm transition-colors ${
      disabled
        ? 'text-gray-300 cursor-not-allowed'
        : active
          ? 'bg-primary/10 text-primary font-medium'
          : 'text-gray-600 hover:bg-gray-50'
    }`}
    disabled={disabled}
    onClick={() => !disabled && onClick()}
  >
    <span className="flex items-center gap-2 truncate">
      {dotColor && <span className={`inline-block w-2 h-2 rounded-full ${dotColor}`} />}
      {label}
    </span>
    <Badge variant="secondary" className="ml-2 h-5 min-w-6 justify-center text-xs">
      {count}
    </Badge>
  </button>
)

const RISK_DOT_COLORS: Record<string, string> = {
  high: 'bg-red-500',
  medium: 'bg-orange-500',
  low: 'bg-yellow-500',
  none: 'bg-green-500',
  inconclusive: 'bg-gray-400',
}

const EVIDENCE_DOT_COLORS: Record<string, string> = {
  first: 'bg-red-500',
  second: 'bg-orange-500',
  third: 'bg-yellow-500',
}

const PairNav: React.FC<PairNavProps> = ({
  sortedPairs, filters, filterCounts, selectedPairKey,
  onFilterChange, onSelectPair, hideFilters = false,
}) => {
  const filteredPairs = useMemo(() => {
    return sortedPairs.filter(pair => {
      const riskKey = (pair.riskLevel || pair.overallRisk || '').toLowerCase()
      if (filters.risk !== 'all' && riskKey !== filters.risk) return false
      if (!pairContainsEvidenceLevel(pair, filters.evidenceLevel as EvidenceLevelFilter)) {
        return false
      }
      return true
    })
  }, [sortedPairs, filters])

  const hasActiveFilter = filters.risk !== 'all' || filters.evidenceLevel !== 'all'

  const riskKeys = useMemo(() => {
    const available = new Set(sortedPairs.map(p => (p.riskLevel || p.overallRisk || '').toLowerCase()))
    return (['high', 'medium', 'low', 'none', 'inconclusive'] as const).filter(k => available.has(k))
  }, [sortedPairs])

  return (
    <ScrollArea className="h-full min-h-0">
      <div className="py-2">
        {!hideFilters && (
          <>
            <div className="px-3 pb-2 mb-2 border-b">
              <div className="text-xs text-gray-500">
                显示 <span className="font-semibold text-gray-800">{filteredPairs.length}</span> / {sortedPairs.length} 对
              </div>
              {hasActiveFilter && (
                <button
                  className="text-xs text-primary hover:underline mt-1"
                  onClick={() => onFilterChange({ risk: 'all', evidenceLevel: 'all' })}
                >
                  重置全部筛选
                </button>
              )}
            </div>

            <NavSection title="风险等级">
              <NavItem
                label="全部"
                count={filterCounts.byRisk.all || 0}
                active={filters.risk === 'all'}
                onClick={() => onFilterChange({ risk: 'all' })}
              />
              {riskKeys.map(key => (
                <NavItem
                  key={key}
                  label={RISK_CONFIG[key].label}
                  count={filterCounts.byRisk[key] || 0}
                  active={filters.risk === key}
                  disabled={(filterCounts.byRisk[key] || 0) === 0}
                  onClick={() => onFilterChange({ risk: key })}
                  dotColor={RISK_DOT_COLORS[key]}
                />
              ))}
            </NavSection>

            <NavSection title="证据级别">
              <NavItem
                label="全部"
                count={filterCounts.byEvidenceLevel.all || 0}
                active={filters.evidenceLevel === 'all'}
                onClick={() => onFilterChange({ evidenceLevel: 'all' })}
              />
              {(['first', 'second', 'third'] as const).map(key => (
                <NavItem
                  key={key}
                  label={`含${EVIDENCE_LEVEL_LABELS[key].label}`}
                  count={filterCounts.byEvidenceLevel[key] || 0}
                  active={filters.evidenceLevel === key}
                  disabled={(filterCounts.byEvidenceLevel[key] || 0) === 0}
                  onClick={() => onFilterChange({ evidenceLevel: key })}
                  dotColor={EVIDENCE_DOT_COLORS[key]}
                />
              ))}
            </NavSection>
          </>
        )}

        {/* 对比对列表 */}
        <div className="mt-1 border-t pt-2">
          <div className="px-3 py-1 text-xs font-semibold text-gray-500 uppercase tracking-wider">
            对比对
          </div>
          {filteredPairs.length === 0 ? (
            <div className="px-3 py-4 text-center text-gray-400 text-xs">
              当前筛选条件下无匹配
            </div>
          ) : (
            filteredPairs.map(pair => {
              const pairKey = `${pair.docAId}_${pair.docBId}`
              const risk = getRiskConfig(pair.riskLevel || pair.overallRisk)
              const isSelected = selectedPairKey === pairKey
              return (
                <button
                  key={pairKey}
                  className={`w-full text-left px-3 py-2 transition-colors ${
                    isSelected
                      ? 'bg-primary/5 border-l-2 border-l-primary'
                      : 'hover:bg-gray-50 border-l-2 border-l-transparent'
                  }`}
                  onClick={() => onSelectPair(pair)}
                >
                  <div className="flex items-center gap-1.5 mb-0.5">
                    {pair.decisionState === 'decidable' ? (
                      <span className={`px-1.5 py-0 text-[10px] font-semibold rounded ${risk.bg} ${risk.text}`}>
                        {risk.label}
                      </span>
                    ) : (
                      <span className="px-1.5 py-0 text-[10px] font-semibold rounded bg-gray-100 text-gray-600">
                        {pair.decisionState === 'failed' ? '分析失败' : '需复核'}
                      </span>
                    )}
                    {pair.findings.length > 0 && (
                      <span className="text-[10px] text-gray-400">
                        {pair.findings.length}条证据
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-gray-700 truncate">
                    {pair.docAName.replace(/\.pdf$/i, '')} × {pair.docBName.replace(/\.pdf$/i, '')}
                  </div>
                </button>
              )
            })
          )}
        </div>
      </div>
    </ScrollArea>
  )
}

export default PairNav
