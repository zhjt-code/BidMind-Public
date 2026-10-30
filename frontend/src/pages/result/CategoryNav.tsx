import React, { useMemo, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { ChevronDown, ChevronRight, Trophy } from 'lucide-react'
import type { ReviewResult, FilterState, FilterCounts } from './types'
import {
  complianceLevelLabels, riskLevelLabels, getCategoryLabel,
} from './types'
import {
  COMPLIANCE_NAV_VALUES,
  PENDING_JUDGMENT_DOT,
  PENDING_JUDGMENT_FILTER,
  PENDING_JUDGMENT_LABEL,
  PENDING_JUDGMENT_TOOLTIP,
} from './pendingJudgment'
import type { ScoringVolumeNavItem } from './scoringViewTransforms'
import { formatScore } from '../requirementReviewTransforms'

interface CategoryNavProps {
  results: ReviewResult[]
  filters: FilterState
  filterCounts: FilterCounts
  onFilterChange: (filters: Partial<FilterState>) => void
  /** 可选底部插槽，渲染在 ScrollArea 末尾，跟随筛选项一起滚动。 */
  footerSlot?: React.ReactNode
  /** 当前列表可见条数（筛选/评分项视图后），由父级传入，避免此处重复过滤逻辑。 */
  shownCount: number
  /** 评分项全集数量（=summary.scoringItemCount）；为 0 时不渲染评分项视图开关。 */
  scoringItemCount: number
  /** 评分卷导航项（评分项视图唯一分组轴，原文组标签）：得分/满分取自后端权威卷桶。 */
  scoringVolumeNav: ScoringVolumeNavItem[]
  /** 评分项预估总分；仅 reliable 且 estimated 非空时展示统一总分。 */
  scoringTotal: { estimated: number | null; max: number; reliable: boolean } | null
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

const CategoryNav: React.FC<CategoryNavProps> = ({
  results, filters, filterCounts, onFilterChange, footerSlot,
  shownCount, scoringItemCount, scoringVolumeNav, scoringTotal,
}) => {
  const availableCategories = useMemo(() => {
    const labels = results.map(r => getCategoryLabel(r.category))
    return Array.from(new Set(labels))
  }, [results])

  const scoringMode = filters.scoringOnly
  const hasActiveFilter =
    filters.compliance !== 'all' ||
    filters.risk !== 'all' ||
    (scoringMode ? filters.scoringVolume !== null : filters.category !== 'all')

  const handleReset = () =>
    onFilterChange({
      compliance: 'all',
      risk: 'all',
      category: 'all',
      scoringVolume: null,
    })

  const handleToggleScoring = () =>
    onFilterChange(
      scoringMode
        ? { scoringOnly: false, scoringVolume: null }
        : { scoringOnly: true, category: 'all', scoringVolume: null },
    )

  // 符合程度 / 风险等级两组在两种视图下渲染一致（计数由父级按当前子集传入）。
  const renderComplianceSection = () => (
    <NavSection title="符合程度">
      <NavItem
        label="全部"
        count={filterCounts.byCompliance.all || 0}
        active={filters.compliance === 'all'}
        onClick={() => onFilterChange({ compliance: 'all' })}
      />
      {COMPLIANCE_NAV_VALUES.map(val => (
        <NavItem
          key={val}
          label={val === PENDING_JUDGMENT_FILTER ? PENDING_JUDGMENT_LABEL : complianceLevelLabels[val]}
          count={filterCounts.byCompliance[val] || 0}
          active={filters.compliance === val}
          disabled={(filterCounts.byCompliance[val] || 0) === 0}
          onClick={() => onFilterChange({ compliance: val })}
          title={val === PENDING_JUDGMENT_FILTER ? PENDING_JUDGMENT_TOOLTIP : undefined}
          dotColor={
            val === PENDING_JUDGMENT_FILTER ? PENDING_JUDGMENT_DOT
              : val === 'not' ? 'bg-red-500'
                : val === 'partially' ? 'bg-yellow-500'
                  : val === 'fully' ? 'bg-green-500'
                    : 'bg-gray-400'
          }
        />
      ))}
    </NavSection>
  )

  const renderRiskSection = () => (
    <NavSection title="风险等级">
      <NavItem
        label="全部"
        count={filterCounts.byRisk.all || 0}
        active={filters.risk === 'all'}
        onClick={() => onFilterChange({ risk: 'all' })}
      />
      {(['high', 'medium', 'low', 'none'] as const).map(val => (
        <NavItem
          key={val}
          label={riskLevelLabels[val]}
          count={filterCounts.byRisk[val] || 0}
          active={filters.risk === val}
          disabled={(filterCounts.byRisk[val] || 0) === 0}
          onClick={() => onFilterChange({ risk: val })}
          dotColor={
            val === 'high' ? 'bg-red-500'
              : val === 'medium' ? 'bg-yellow-500'
              : val === 'low' ? 'bg-blue-500'
              : 'bg-green-500'
          }
        />
      ))}
    </NavSection>
  )

  return (
    <ScrollArea className="h-full min-h-0">
      <div className="py-2">
        <div className="px-3 pb-2 mb-2 border-b">
          <div className="text-xs text-gray-500">
            显示 <span className="font-semibold text-gray-800">{shownCount}</span> / {results.length} 项
          </div>
          {hasActiveFilter && (
            <button
              className="text-xs text-primary hover:underline mt-1"
              onClick={handleReset}
            >
              重置全部筛选
            </button>
          )}
          {scoringItemCount > 0 && (
            <button
              type="button"
              onClick={handleToggleScoring}
              title="仅查看评分项，按评分卷分组浏览"
              className={`mt-2 flex items-center justify-between w-full px-2.5 py-1.5 rounded-md text-xs font-bold transition-colors border ${
                scoringMode
                  ? 'bg-blue-600 border-blue-600 text-white shadow-xs'
                  : 'bg-blue-50 border-blue-300 text-blue-700 hover:bg-blue-100 hover:border-blue-400'
              }`}
            >
              <span className="flex items-center gap-1.5">
                <Trophy className="h-3.5 w-3.5" />
                评分项视图
              </span>
              <span
                className={`px-1.5 rounded font-semibold tabular-nums ${
                  scoringMode ? 'bg-white/25 text-white' : 'bg-blue-600 text-white'
                }`}
              >
                {scoringItemCount}
              </span>
            </button>
          )}
        </div>

        {scoringMode ? (
          <>
            {scoringTotal && scoringTotal.reliable && scoringTotal.estimated != null && (
              <div className="mx-2 mb-2 px-3 py-2 rounded-md bg-blue-50/70 border border-blue-100">
                <div className="text-[11px] text-blue-700 mb-0.5">预估评标得分</div>
                <div className="text-sm font-bold text-blue-700 tabular-nums">
                  {formatScore(scoringTotal.estimated)}
                  <span className="text-gray-400 font-normal"> / {formatScore(scoringTotal.max)}</span>
                </div>
                <div className="mt-0.5 text-[10px] leading-snug text-blue-600/80">
                  预估仅供参考，不代表实际评标得分
                </div>
              </div>
            )}

            {/* 评分卷轴（原文组标签）是评分项视图唯一分组轴——评标场景的第一心智是
             "哪一卷丢了多少分"。分类轴经实测判定为交互过载已移除, 分类信息降级为
             条目徽标。得分/满分来自后端权威卷桶, 此处不做任何累加。 */}
            <NavSection title="评分卷">
              {/* 平铺兜底入口：即使某项卷归属有误，"全部评分项"仍可见全部。 */}
              <NavItem
                label="全部评分项"
                count={filterCounts.byVolume.all || 0}
                active={filters.scoringVolume === null}
                onClick={() => onFilterChange({ scoringVolume: null })}
              />
              {scoringVolumeNav.map(item => (
                <NavItem
                  key={item.key}
                  label={item.label}
                  count={filterCounts.byVolume[item.key] || 0}
                  active={filters.scoringVolume === item.key}
                  disabled={(filterCounts.byVolume[item.key] || 0) === 0}
                  onClick={() => onFilterChange({ scoringVolume: item.key })}
                  suffix={`${item.earned == null ? '—' : formatScore(item.earned)}/${formatScore(item.max)}`}
                />
              ))}
            </NavSection>

            {renderComplianceSection()}
            {renderRiskSection()}
          </>
        ) : (
          <>
            <NavSection title="分类">
              <NavItem
                label="全部"
                count={filterCounts.byCategory.all || 0}
                active={filters.category === 'all'}
                onClick={() => onFilterChange({ category: 'all' })}
              />
              {availableCategories.map(label => (
                <NavItem
                  key={label}
                  label={label}
                  count={filterCounts.byCategory[label] || 0}
                  active={filters.category === label}
                  disabled={(filterCounts.byCategory[label] || 0) === 0}
                  onClick={() => onFilterChange({ category: label })}
                />
              ))}
            </NavSection>

            {renderComplianceSection()}
            {renderRiskSection()}
          </>
        )}

        {footerSlot && (
          <>
            <div className="border-t mx-2 my-1" />
            <div className="px-2 pb-3">{footerSlot}</div>
          </>
        )}
      </div>
    </ScrollArea>
  )
}

interface NavItemProps {
  label: string
  count: number
  active: boolean
  disabled?: boolean
  onClick: () => void
  dotColor?: string
  /** 计数徽标左侧的附加信息（评分卷轴用于展示"得分/满分"）。 */
  suffix?: string
  title?: string
}

const NavItem: React.FC<NavItemProps> = ({
  label, count, active, disabled, onClick, dotColor, suffix, title,
}) => (
  <button
    className={`flex items-center justify-between w-full px-3 py-1.5 text-sm transition-colors ${
      disabled
        ? 'text-gray-300 cursor-not-allowed'
        : active
          ? 'bg-primary/10 text-primary font-medium'
          : 'text-gray-600 hover:bg-gray-50'
    }`}
    disabled={disabled}
    title={title}
    onClick={() => !disabled && onClick()}
  >
    <span className="flex items-center gap-2 truncate">
      {dotColor && <span className={`inline-block w-2 h-2 rounded-full ${dotColor}`} />}
      {label}
    </span>
    <span className="flex items-center shrink-0">
      {suffix && (
        <span className={`text-xs tabular-nums ${disabled ? 'text-gray-300' : 'text-gray-400'}`}>
          {suffix}
        </span>
      )}
      <Badge variant="secondary" className="ml-2 h-5 min-w-6 justify-center text-xs">
        {count}
      </Badge>
    </span>
  </button>
)

export default CategoryNav
