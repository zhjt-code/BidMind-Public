import React, { useMemo } from 'react'
import { useOneShotOverviewCollapse } from '@/hooks/useOneShotOverviewCollapse'
import {
  PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer,
  RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar,
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
} from 'recharts'
import { Button } from '@/components/ui/button'
import { ChevronDown, ChevronUp, Info } from 'lucide-react'
import type { ReviewResult, ReviewSummary } from './types'
import {
  complianceLevelLabels, riskLevelLabels, getCategoryLabel,
  COMPLIANCE_COLORS, RISK_COLORS, getConclusionStyle,
} from './types'
import {
  buildReviewFocusSections,
  buildReviewHeaderMetrics,
  getReviewConclusionLabel,
  getReviewDecisionSummaryText,
  type ReviewFocusItem,
  type ReviewFocusTone,
} from './reviewStatsOverview'
import { REVIEW_STATS_SCROLL_BODY_CLASS } from './reviewStatsLayout'
import {
  complianceCountKey,
  PENDING_JUDGMENT_COLOR,
  PENDING_JUDGMENT_FILTER,
  PENDING_JUDGMENT_LABEL,
} from './pendingJudgment'

interface ReviewStatsProps {
  results: ReviewResult[]
  summary: ReviewSummary
  extractionWarnings?: string[]
}

const CATEGORY_RADAR_ORDER = ['QUAL', 'TECH', 'COMM', 'PERF', 'STAFF', 'FORMAT']
const REVIEW_RADAR_COLOR = 'hsl(25, 95%, 53%)'
const REVIEW_RADAR_ACTIVE_DOT_COLOR = 'hsl(25, 95%, 45%)'
const REVIEW_RADAR_RADIAL_LINE_COLOR = 'hsl(25, 95%, 86%)'
const REVIEW_RADAR_GRID_RINGS = [
  { score: 20, radius: 16, stroke: 'hsl(210, 90%, 80%)' },
  { score: 40, radius: 32, stroke: 'hsl(160, 70%, 76%)' },
  { score: 60, radius: 48, stroke: 'hsl(48, 95%, 76%)' },
  { score: 80, radius: 64, stroke: 'hsl(28, 95%, 76%)' },
  { score: 100, radius: 80, stroke: 'hsl(0, 85%, 78%)' },
] as const

const REVIEW_RADAR_GRID_TICKS = REVIEW_RADAR_GRID_RINGS.map(ring => ring.score)

const FOCUS_STYLE: Record<ReviewFocusTone, {
  wrap: string
  title: string
  note: string
}> = {
  danger: {
    wrap: 'bg-red-50 border-red-200',
    title: 'text-red-700',
    note: 'border-red-200/70 text-red-700',
  },
  warning: {
    wrap: 'bg-amber-50 border-amber-200',
    title: 'text-amber-800',
    note: 'border-amber-200/70 text-amber-800',
  },
  info: {
    wrap: 'bg-blue-50/50 border-blue-100',
    title: 'text-blue-800',
    note: 'border-blue-200/70 text-blue-700',
  },
  neutral: {
    wrap: 'bg-white border-gray-200',
    title: 'text-gray-700',
    note: 'border-gray-200 text-gray-600',
  },
}

function hasNestedFocusItems(items: ReviewFocusItem[]): boolean {
  return items.some(item => (item.children?.length ?? 0) > 0)
}

const ReviewStats: React.FC<ReviewStatsProps> = ({ results, summary, extractionWarnings }) => {
  const { collapsed, toggleCollapsed, bodyRef, rootRef } = useOneShotOverviewCollapse()
  const headerMetrics = useMemo(() => buildReviewHeaderMetrics(summary), [summary])
  const decisionSummaryText = getReviewDecisionSummaryText(summary)
  const focusSections = useMemo(() => buildReviewFocusSections({
    summary,
    results,
    extractionWarnings,
  }), [summary, results, extractionWarnings])

  const complianceData = useMemo(() => {
    const counts: Record<string, number> = { fully: 0, partially: 0, not: 0, missing: 0, pending: 0 }
    for (const r of results) {
      const key = complianceCountKey(r)
      counts[key] = (counts[key] || 0) + 1
    }
    return Object.entries(counts)
      .filter(([, v]) => v > 0)
      .map(([key, value]) => ({
        name: key === PENDING_JUDGMENT_FILTER ? PENDING_JUDGMENT_LABEL : (complianceLevelLabels[key] || key),
        value,
        color: key === PENDING_JUDGMENT_FILTER ? PENDING_JUDGMENT_COLOR : (COMPLIANCE_COLORS[key] || '#94a3b8'),
      }))
  }, [results])

  const radarData = useMemo(() => {
    const scoreMap: Record<string, { total: number; count: number }> = {}
    for (const r of results) {
      const label = getCategoryLabel(r.category)
      if (!scoreMap[label]) scoreMap[label] = { total: 0, count: 0 }
      scoreMap[label].total += r.score
      scoreMap[label].count += 1
    }
    const categoryLabelOrder = CATEGORY_RADAR_ORDER.map(k => getCategoryLabel(k))
    return categoryLabelOrder
      .filter(label => scoreMap[label])
      .map(label => ({
        category: label.replace('要求', ''),
        score: Math.round(scoreMap[label].total / scoreMap[label].count),
        fullMark: 100,
      }))
  }, [results])

  const riskData = useMemo(() => {
    const counts: Record<string, number> = { high: 0, medium: 0, low: 0, none: 0 }
    for (const r of results) counts[r.riskLevel] = (counts[r.riskLevel] || 0) + 1
    return (['high', 'medium', 'low', 'none'] as const).map(key => ({
      name: riskLevelLabels[key],
      value: counts[key],
      color: RISK_COLORS[key],
    }))
  }, [results])

  return (
    <div ref={rootRef} className="bg-white rounded-lg shadow mb-4 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b">
        <div className="flex items-center gap-4">
          <h2 className="font-medium text-gray-700">审查概览</h2>
          <div className="flex items-center gap-3 text-sm">
            {headerMetrics.map(metric => {
              const isHighRisk = metric.startsWith('高风险')
              return (
                <span key={metric} className={isHighRisk ? 'text-red-600 font-medium' : 'text-gray-500'}>
                  {metric}
                </span>
              )
            })}
          </div>
        </div>
        <Button variant="ghost" size="sm" onClick={toggleCollapsed} data-guide="result-collapse-chart">
          {collapsed ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
          <span className="ml-1 text-[14px]">{collapsed ? '展开图表' : '收起图表'}</span>
        </Button>
      </div>

      {!collapsed && (
        <div ref={bodyRef} className={REVIEW_STATS_SCROLL_BODY_CLASS}>
          <div className="grid grid-cols-3 gap-4">
            <div className="flex flex-col items-center">
              <div className="text-xs text-gray-500 mb-2">符合程度分布</div>
              <ResponsiveContainer width="100%" height={200}>
                <PieChart>
                  <Pie
                    data={complianceData}
                    cx="50%" cy="50%"
                    innerRadius={40} outerRadius={70}
                    paddingAngle={2}
                    dataKey="value"
                  >
                    {complianceData.map((entry) => (
                      <Cell key={entry.name} fill={entry.color} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(value, name) => [`${value} 项`, name]} />
                  <Legend
                    wrapperStyle={{ fontSize: '11px' }}
                    formatter={(value: string) => <span className="text-gray-600">{value}</span>}
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>

            <div className="flex flex-col items-center">
              <div className="text-xs text-gray-500 mb-2">分类符合度</div>
              <ResponsiveContainer width="100%" height={200}>
                <RadarChart data={radarData} outerRadius={80}>
                  <PolarGrid
                    radialLines={true}
                    polarRadius={[]}
                    stroke={REVIEW_RADAR_RADIAL_LINE_COLOR}
                    strokeDasharray="3 3"
                  />
                  {REVIEW_RADAR_GRID_RINGS.map(ring => (
                    <PolarGrid
                      key={ring.score}
                      radialLines={false}
                      polarRadius={[ring.radius]}
                      stroke={ring.stroke}
                      strokeDasharray="3 3"
                    />
                  ))}
                  <PolarAngleAxis dataKey="category" tick={{ fontSize: 10 }} />
                  <PolarRadiusAxis
                    angle={90}
                    domain={[0, 100]}
                    ticks={REVIEW_RADAR_GRID_TICKS}
                    axisLine={false}
                    tickLine={false}
                    tick={{ fontSize: 9, fill: '#9ca3af' }}
                  />
                  <Radar
                    dataKey="score" stroke={REVIEW_RADAR_COLOR}
                    fill={REVIEW_RADAR_COLOR} fillOpacity={0.25}
                    dot={{ r: 3, fill: REVIEW_RADAR_COLOR, strokeWidth: 0 }}
                    activeDot={{ r: 5, fill: REVIEW_RADAR_ACTIVE_DOT_COLOR, strokeWidth: 0 }}
                  />
                  <Tooltip formatter={(value) => [`${value} 分`, '平均得分']} />
                </RadarChart>
              </ResponsiveContainer>
            </div>

            <div className="flex flex-col items-center">
              <div className="text-xs text-gray-500 mb-2">风险等级分布</div>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={riskData} barSize={32}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                  <Tooltip formatter={(value) => [`${value} 项`, '数量']} />
                  <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                    {riskData.map((entry) => (
                      <Cell key={entry.name} fill={entry.color} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="border-t pt-4">
            <div className="rounded-lg border border-blue-200 bg-blue-50/60 px-4 py-3">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <p className="text-xs font-semibold text-blue-700 mb-1">审查最终结论</p>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span
                      className={`inline-flex items-center px-2 py-0.5 rounded text-white text-xs font-medium ${getConclusionStyle(summary.conclusion)}`}
                      title={`${summary.conclusionReason}（AI 建议，最终以人工审核为准）`}
                    >
                      {getReviewConclusionLabel(summary)}
                    </span>
                    <span className="text-sm text-gray-700">
                      综合符合度 <span className="text-base font-bold text-blue-600">{summary.weightedScore.toFixed(1)}</span>
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-gray-600">
                    {decisionSummaryText}
                  </p>
                </div>
                <div className="hidden lg:grid grid-cols-3 gap-3 text-center flex-shrink-0">
                  <div className="rounded-md bg-white/80 border border-blue-100 px-3 py-2">
                    <div className="text-lg font-semibold text-blue-700">{summary.weightedScore.toFixed(1)}</div>
                    <div className="text-[11px] text-gray-500">综合符合度</div>
                  </div>
                  <div className="rounded-md bg-white/80 border border-blue-100 px-3 py-2">
                    <div className="text-lg font-semibold text-gray-900">{summary.compliantCount}/{summary.totalCount}</div>
                    <div className="text-[11px] text-gray-500">符合要求</div>
                  </div>
                  <div className="rounded-md bg-white/80 border border-blue-100 px-3 py-2">
                    <div className="text-lg font-semibold text-red-600">{summary.highRiskCount}</div>
                    <div className="text-[11px] text-gray-500">高风险项</div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {focusSections.length > 0 && (
            <div className="border-t pt-3 space-y-3">
              <p className="text-xs font-semibold text-gray-500">审查重点</p>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                {focusSections.map(section => {
                  const style = FOCUS_STYLE[section.tone]
                  const nestedItems = hasNestedFocusItems(section.items)
                  return (
                    <section key={section.title} className={`rounded-lg border px-3 py-2.5 ${style.wrap}`}>
                      <p className={`text-xs font-semibold mb-1.5 ${style.title}`}>{section.title}</p>
                      {nestedItems ? (
                        <div className="space-y-2.5">
                          {section.items.map(item => (
                            <div key={`${section.title}-${item.text}`} className="text-sm text-gray-700 leading-relaxed">
                              <p className={item.children?.length ? 'font-semibold text-gray-800 tabular-nums' : ''}>
                                {item.text}
                              </p>
                              {item.children && item.children.length > 0 && (
                                <div className="mt-1.5 space-y-1 border-l border-blue-200/80 pl-3">
                                  <p className="text-xs font-medium text-blue-700">其中：</p>
                                  {item.children.map(child => (
                                    <div key={`${item.text}-${child}`} className="flex items-start gap-2 text-xs text-gray-700">
                                      <span className="mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-blue-400" aria-hidden="true" />
                                      <span>{child}</span>
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      ) : section.items.length > 1 ? (
                        <ol className="list-decimal pl-5 space-y-1">
                          {section.items.map(item => (
                            <li key={`${section.title}-${item.text}`} className="text-sm text-gray-700 leading-relaxed">
                              {item.text}
                            </li>
                          ))}
                        </ol>
                      ) : (
                        <p className="text-sm text-gray-700 leading-relaxed">{section.items[0]?.text}</p>
                      )}
                      {section.note && (
                        <p className={`mt-2 flex items-start gap-1.5 border-t pt-2 text-xs font-medium leading-relaxed ${style.note}`}>
                          <Info className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" aria-hidden="true" />
                          <span>{section.note}</span>
                        </p>
                      )}
                    </section>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default ReviewStats
