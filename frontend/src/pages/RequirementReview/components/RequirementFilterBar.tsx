/**
 * 招标要求确认页的「计数即筛选」单行控制栏。
 *
 * 职责: 把统计数字直接渲染为可点击的筛选控件 (全部 / 否决项 / 各分类),
 *       以及分组视图下的全部展开/收起切换；
 *       消除原"统计行 + 筛选行"双行的类别名冗余。
 * 不做: 不持有筛选/折叠状态、不做派生计算 —— activeTab / showMandatoryOnly
 *       / expanded 相关标志及所有计数均由调用方通过 props 传入 (纯受控展示)。
 *
 * 筛选维度相互正交: activeTab (分类单选) 与 showMandatoryOnly / showScoringFocusOnly
 * (两个独立 toggle) 可叠加, 视觉上以分隔线 + 左侧白底方形标记开关与右侧彩色分类组区分,
 * 避免被误读为互斥。展开收起只动折叠态，不参与筛选。
 */
import React from 'react'
import { ChevronsDownUp, ChevronsUpDown } from 'lucide-react'
import {
  ALL_FILTER_CLASSES,
  MANDATORY_FILTER_CLASSES,
  SCORING_FOCUS_FILTER_CLASSES,
  getCategoryColorClasses,
} from '@/lib/constants'

export interface RequirementFilterBarProps {
  totalCount: number
  mandatoryCount: number
  /** 评分项 + 重点评审项总条数 (含无具体分值的重点项); 为 0 时不渲染该开关 */
  scoringFocusCount: number
  uniqueCategoryLabels: string[]
  /** label → 该分类下的要求条数 (含强制 + 非强制) */
  categoryCounts: Record<string, number>
  activeTab: string
  showMandatoryOnly: boolean
  /** 仅显示评分项/重点项 (与分类、否决项均正交可叠加) */
  showScoringFocusOnly: boolean
  onTabChange: (tab: string) => void
  onToggleMandatoryOnly: () => void
  onToggleScoringFocusOnly: () => void
  /** 是否至少有一个分类分组处于展开（半展开亦算，用于显示「全部收起」） */
  anyGroupExpanded: boolean
  /** 是否显示展开/收起按钮（仅全部分组视图且可见分组 ≥ 2） */
  expandCollapseVisible: boolean
  onToggleExpandCollapse: () => void
}

const CHIP_BASE =
  'inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-sm font-medium transition-colors'

export const RequirementFilterBar: React.FC<RequirementFilterBarProps> = ({
  totalCount,
  mandatoryCount,
  scoringFocusCount,
  uniqueCategoryLabels,
  categoryCounts,
  activeTab,
  showMandatoryOnly,
  showScoringFocusOnly,
  onTabChange,
  onToggleMandatoryOnly,
  onToggleScoringFocusOnly,
  anyGroupExpanded,
  expandCollapseVisible,
  onToggleExpandCollapse,
}) => {
  const allActive = activeTab === 'all'

  return (
    <div className="px-4 py-3 border-b bg-gray-50 flex flex-wrap items-center gap-2">
      {/* 全部（= 共 N 条要求, 分类单选维度的默认项） */}
      <button
        type="button"
        onClick={() => onTabChange('all')}
        aria-pressed={allActive}
        title="显示全部分类"
        className={`${CHIP_BASE} ${allActive ? ALL_FILTER_CLASSES.active : ALL_FILTER_CLASSES.inactive}`}
      >
        全部
        <span className="text-xs font-semibold opacity-80">{totalCount}</span>
      </button>

      {/* 仅显示否决项（toggle, 与分类正交可叠加; 红色独占否决语义） */}
      {mandatoryCount > 0 && (
        <button
          type="button"
          onClick={onToggleMandatoryOnly}
          aria-pressed={showMandatoryOnly}
          title="仅显示否决项（可与分类叠加）"
          className={`${CHIP_BASE} ${
            showMandatoryOnly ? MANDATORY_FILTER_CLASSES.active : MANDATORY_FILTER_CLASSES.inactive
          }`}
        >
          <span
            className={`inline-block w-3 h-3 rounded-sm border ${
              showMandatoryOnly ? 'bg-red-500 border-red-500' : 'border-red-300'
            }`}
          />
          否决项
          <span className="text-xs font-semibold">{mandatoryCount}</span>
        </button>
      )}

      {/* 仅显示评分项/重点项（toggle, 与分类、否决项均正交可叠加;
          amber 与列表行内评分项徽标同语义色, 便于用户把筛选结果与徽标对应） */}
      {scoringFocusCount > 0 && (
        <button
          type="button"
          onClick={onToggleScoringFocusOnly}
          aria-pressed={showScoringFocusOnly}
          title="仅显示评分项与重点评审项（可与分类叠加）"
          className={`${CHIP_BASE} ${
            showScoringFocusOnly
              ? SCORING_FOCUS_FILTER_CLASSES.active
              : SCORING_FOCUS_FILTER_CLASSES.inactive
          }`}
        >
          <span
            className={`inline-block w-3 h-3 rounded-sm border ${
              showScoringFocusOnly ? 'bg-amber-500 border-amber-500' : 'border-amber-300'
            }`}
          />
          评分项/重点
          <span className="text-xs font-semibold">{scoringFocusCount}</span>
        </button>
      )}

      {/* 分隔线: 左侧总览/特殊筛选 与 右侧分类单选组 */}
      {uniqueCategoryLabels.length > 0 && (
        <span aria-hidden className="mx-1 h-5 w-px bg-gray-300" />
      )}

      {/* 分类单选组（各自配色, 点击过滤到该类别） */}
      {uniqueCategoryLabels.map((label) => {
        const isActive = activeTab === label
        const colors = getCategoryColorClasses(label)
        return (
          <button
            key={label}
            type="button"
            onClick={() => onTabChange(label)}
            aria-pressed={isActive}
            title={`仅显示${label}`}
            className={`${CHIP_BASE} ${isActive ? colors.chipActive : colors.chipInactive}`}
          >
            {label}
            <span className="text-xs font-semibold opacity-80">
              {categoryCounts[label] ?? 0}
            </span>
          </button>
        )
      })}

      {/* 分组视图：全部展开 / 全部收起（半展开亦显示收起，一键回默认折叠） */}
      {expandCollapseVisible && (
        <button
          type="button"
          onClick={onToggleExpandCollapse}
          title={anyGroupExpanded ? '收起全部分类分组' : '展开全部分类分组'}
          className="ml-auto inline-flex items-center justify-center gap-1 rounded-md px-3 h-7 text-xs font-medium text-gray-600 hover:bg-gray-200/80 hover:text-gray-900 transition-colors"
        >
          {anyGroupExpanded ? (
            <ChevronsDownUp className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <ChevronsUpDown className="h-3.5 w-3.5" aria-hidden />
          )}
          {anyGroupExpanded ? '全部收起' : '全部展开'}
        </button>
      )}
    </div>
  )
}
