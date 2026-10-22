import React, { useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { QaFilterCounts, QaFilterState } from './types'
import {
  QA_CERTAINTY_LABELS,
  QA_DISPOSITION_LABELS,
  QA_GROUP_LABELS,
  QA_GROUP_ORDER,
  QA_SEVERITY_LABELS,
  QA_SEVERITY_ORDER,
} from './documentQualityTransforms'

/**
 * 职责: 文档质检左栏筛选导航(五组分类/严重度/处置/确定性四轴)
 * 不做: 不做筛选计算(计数由父级按联动口径传入)
 */

interface DocumentQualityNavProps {
  filters: QaFilterState
  counts: QaFilterCounts
  onFilterChange: (partial: Partial<QaFilterState>) => void
  shownCount: number
  totalCount: number
}

const NavSection: React.FC<{ title: string; children: React.ReactNode }> = ({
  title, children,
}) => {
  const [open, setOpen] = useState(true)
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

const NavItem: React.FC<{
  label: string
  count: number
  active: boolean
  onClick: () => void
  dotColor?: string
}> = ({ label, count, active, onClick, dotColor }) => {
  const disabled = count === 0 && !active
  return (
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
      <Badge variant="secondary" className="ml-2 h-5 min-w-[1.5rem] justify-center text-xs">
        {count}
      </Badge>
    </button>
  )
}

const SEVERITY_DOTS: Record<string, string> = {
  P0: 'bg-red-600', P1: 'bg-orange-500', P2: 'bg-amber-400', P3: 'bg-gray-400',
}
const DISPOSITION_DOTS: Record<string, string> = {
  pending: 'bg-amber-500',
  confirmed: 'bg-red-500',
  false_positive: 'bg-green-500',
  ignored: 'bg-gray-400',
}

const DocumentQualityNav: React.FC<DocumentQualityNavProps> = ({
  filters, counts, onFilterChange, shownCount, totalCount,
}) => {
  const hasActiveFilter =
    filters.group !== 'all' || filters.severity !== 'all'
    || filters.disposition !== 'all' || filters.certainty !== 'all'

  return (
    <ScrollArea className="h-full min-h-0">
      <div className="py-2">
        <div className="px-3 pb-2 mb-2 border-b">
          <div className="text-xs text-gray-500">
            显示 <span className="font-semibold text-gray-800">{shownCount}</span> / {totalCount} 项
          </div>
          {hasActiveFilter && (
            <button
              className="text-xs text-primary hover:underline mt-1"
              onClick={() => onFilterChange({
                group: 'all', severity: 'all', disposition: 'all', certainty: 'all',
              })}
            >
              重置全部筛选
            </button>
          )}
        </div>

        <NavSection title="问题分组">
          <NavItem
            label="全部"
            count={counts.byGroup.all || 0}
            active={filters.group === 'all'}
            onClick={() => onFilterChange({ group: 'all' })}
          />
          {QA_GROUP_ORDER.map(group => (
            <NavItem
              key={group}
              label={QA_GROUP_LABELS[group]}
              count={counts.byGroup[group] || 0}
              active={filters.group === group}
              onClick={() => onFilterChange({ group })}
            />
          ))}
        </NavSection>

        <NavSection title="严重度">
          <NavItem
            label="全部"
            count={counts.bySeverity.all || 0}
            active={filters.severity === 'all'}
            onClick={() => onFilterChange({ severity: 'all' })}
          />
          {QA_SEVERITY_ORDER.map(severity => (
            <NavItem
              key={severity}
              label={`${severity} ${QA_SEVERITY_LABELS[severity]}`}
              count={counts.bySeverity[severity] || 0}
              active={filters.severity === severity}
              onClick={() => onFilterChange({ severity })}
              dotColor={SEVERITY_DOTS[severity]}
            />
          ))}
        </NavSection>

        <NavSection title="处置状态">
          <NavItem
            label="全部"
            count={counts.byDisposition.all || 0}
            active={filters.disposition === 'all'}
            onClick={() => onFilterChange({ disposition: 'all' })}
          />
          {(['pending', 'confirmed', 'false_positive', 'ignored'] as const).map(key => (
            <NavItem
              key={key}
              label={QA_DISPOSITION_LABELS[key]}
              count={counts.byDisposition[key] || 0}
              active={filters.disposition === key}
              onClick={() => onFilterChange({ disposition: key })}
              dotColor={DISPOSITION_DOTS[key]}
            />
          ))}
        </NavSection>

        <NavSection title="确定性">
          <NavItem
            label="全部"
            count={counts.byCertainty.all || 0}
            active={filters.certainty === 'all'}
            onClick={() => onFilterChange({ certainty: 'all' })}
          />
          {(['certain', 'probable', 'possible', 'unknown'] as const).map(key => (
            <NavItem
              key={key}
              label={QA_CERTAINTY_LABELS[key]}
              count={counts.byCertainty[key] || 0}
              active={filters.certainty === key}
              onClick={() => onFilterChange({ certainty: key })}
            />
          ))}
        </NavSection>
      </div>
    </ScrollArea>
  )
}

export default DocumentQualityNav
