import React from 'react'
import { Badge } from '@/components/ui/badge'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { FactGroup, ReviewResult } from './types'
import {
  getCategoryLabel,
  complianceLevelLabels, riskLevelLabels,
  getComplianceBadgeVariant, getRiskColor,
  isNonScoredReference,
} from './types'
import {
  getComplianceDisplayLabel,
  isPendingJudgment,
  PENDING_JUDGMENT_BADGE_CLASS,
  PENDING_JUDGMENT_TOOLTIP,
} from './pendingJudgment'
import {
  isCategoryInferenceMiss, UNCATEGORIZED_CATEGORY_LABEL,
} from './scoringViewTransforms'
import { getCategoryColorClasses, bidPartLabel } from '@/lib/constants'

/**
 * 职责: 响应审查结果列表的事实组/单行渲染（自 Result.tsx 纯移动，行为零变化）
 * 不做: 不做筛选排序、不发请求、不承载文档质检视图
 */

// ==================== 事实组渲染 ====================

interface ResultGroupBlockProps {
  group: FactGroup
  isMulti: boolean
  collapsed: boolean
  childMembers: ReviewResult[]
  selectedResultId: number | null
  onSelect: (id: number) => void
  onToggleCollapse: () => void
}

export const ResultGroupBlock: React.FC<ResultGroupBlockProps> = ({
  group, isMulti, collapsed, childMembers, selectedResultId, onSelect, onToggleCollapse,
}) => {
  return (
    <div>
      <ResultRow
        result={group.primary}
        selected={selectedResultId === group.primary.id}
        onSelect={() => onSelect(group.primary.id)}
        groupBadges={isMulti ? {
          siblingCount: childMembers.length,
          collapsed,
          onToggleCollapse,
          hasConflict: group.hasConflict,
          needsReview: group.needsReview,
        } : null}
      />
      {isMulti && !collapsed && childMembers.map(child => (
        <ResultRow
          key={child.id}
          result={child}
          selected={selectedResultId === child.id}
          onSelect={() => onSelect(child.id)}
          isChild
        />
      ))}
    </div>
  )
}

interface ResultRowProps {
  result: ReviewResult
  selected: boolean
  onSelect: () => void
  isChild?: boolean
  groupBadges?: {
    siblingCount: number
    collapsed: boolean
    onToggleCollapse: () => void
    hasConflict: boolean
    needsReview: boolean
  } | null
}

const ResultRow: React.FC<ResultRowProps> = ({ result, selected, onSelect, isChild, groupBadges }) => {
  const padLeft = isChild ? 'pl-8' : 'pl-4'
  const isScoringRow = result.requirementKind === 'scoring_item' || Boolean(result.isKeyReview)
  const categoryLabel = getCategoryLabel(result.category)
  // 分类零命中时徽标直接显示"未分类"，不再显示未经关键词验证的推断分类名——
  // 左栏单轴化后已无"未分类"桶，该 fail-visible 由本徽标承担，须免悬停可见。
  const categoryMiss = isCategoryInferenceMiss(result)
  const categoryBadgeText = categoryMiss
    ? UNCATEGORIZED_CATEGORY_LABEL
    : categoryLabel.replace(/要求$/, '')
  const categoryTitle = result.categoryBasis
    ? `按"${result.categoryBasis}"归入${categoryLabel}`
    : categoryMiss
      ? '未命中分类关键词，分类待人工确认'
      : undefined
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect() } }}
      className={`w-full text-left ${padLeft} pr-4 py-3 transition-colors cursor-pointer hover:bg-gray-50 ${
        selected ? 'bg-primary/5 border-l-2 border-l-primary' : ''
      } ${isChild ? 'bg-gray-50/40' : ''}`}
    >
      <div className="flex items-start justify-between mb-1 gap-2">
        <div className="flex items-center gap-1.5 min-w-0 flex-wrap">
          {/* 核心徽标组: 符合度 + 类别 + 评分项 始终同一行(类别在列表中省去"要求"二字腾空间) */}
          <span className="flex items-center gap-1.5 shrink-0">
            {groupBadges && (
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); groupBadges.onToggleCollapse() }}
                className="text-gray-400 hover:text-gray-600 shrink-0"
                title={groupBadges.collapsed ? '展开同源条目' : '折叠同源条目'}
                aria-label={groupBadges.collapsed ? '展开同源条目' : '折叠同源条目'}
              >
                {groupBadges.collapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
              </button>
            )}
            <Badge
              variant={isPendingJudgment(result) ? 'outline' : getComplianceBadgeVariant(result.complianceLevel)}
              className={`text-[10px] px-1.5 py-0 whitespace-nowrap ${
                isPendingJudgment(result) ? PENDING_JUDGMENT_BADGE_CLASS : ''
              }`}
              title={isPendingJudgment(result) ? PENDING_JUDGMENT_TOOLTIP : undefined}
            >
              {getComplianceDisplayLabel(result, complianceLevelLabels)}
            </Badge>
            {/* 卷徽标已移除: 左栏卷导航与列表卷序已表达分组归属, 卡片再标一遍属层间
             重复（去重铁律）。卷信息缺失时的可见性由左栏"其他"桶承担。 */}
            <Badge
              variant="outline"
              title={categoryTitle}
              className={`text-[10px] px-1.5 py-0 whitespace-nowrap ${
                categoryMiss
                  ? 'font-normal text-gray-500'
                  : isScoringRow ? getCategoryColorClasses(categoryLabel).badge : ''
              }`}
            >
              {categoryBadgeText}
            </Badge>
            {(result.requirementKind === 'scoring_item' || result.isKeyReview) && (
              <Badge
                variant="secondary"
                className="text-[10px] px-1.5 py-0 bg-amber-100 text-amber-800 hover:bg-amber-100 whitespace-nowrap"
                title="评分项·重点：AI 预估得分/满分，仅供参考，不等于正式评标得分"
              >
              {result.maxScore != null && result.maxScore > 0
                ? `评分项 ${result.estimatedScore != null ? result.estimatedScore : '—'}/${result.maxScore}`
                : '评分项·重点'}
              </Badge>
            )}
            {isNonScoredReference(result) && (
              <Badge
                variant="secondary"
                className="text-[10px] px-1.5 py-0 whitespace-nowrap"
                title="该要求已被降为参考条款，此明细保留展示但不计入汇总评分"
              >
                参考条款·不计分
              </Badge>
            )}
            {/* 投标信封分卷: 证据来源卷徽标(商务标/技术标; 整本不显示) */}
            {(result.sourceParts || []).map((part) => (
              <Badge
                key={part}
                variant="outline"
                className="text-[10px] px-1.5 py-0 whitespace-nowrap text-gray-600"
                title={`投标响应证据来自${bidPartLabel(part)}`}
              >
                {bidPartLabel(part)}
              </Badge>
            ))}
          </span>
          {result.consistencyFlag === 'cross_conflict' && (
            <Badge variant="destructive" className="text-[10px] px-1.5 py-0">冲突</Badge>
          )}
          {groupBadges && groupBadges.siblingCount > 0 && (
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0" title="同事实组的其他条目数量">
              +{groupBadges.siblingCount} 同源
            </Badge>
          )}
          {groupBadges && groupBadges.hasConflict && (
            <Badge variant="destructive" className="text-[10px] px-1.5 py-0" title="组内存在判定冲突">组内冲突</Badge>
          )}
          {groupBadges && !groupBadges.hasConflict && groupBadges.needsReview && (
            <Badge variant="warning" className="text-[10px] px-1.5 py-0" title="组内存在待复核条目">组内待复核</Badge>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-sm font-bold text-blue-500">{result.score}</span>
          <span className={`text-xs ${getRiskColor(result.riskLevel)}`}>
            {riskLevelLabels[result.riskLevel]}
          </span>
        </div>
      </div>
      <div className="text-sm text-gray-700 line-clamp-2">
        {result.requirementSummary}
      </div>
    </div>
  )
}
