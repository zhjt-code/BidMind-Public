import React, { useMemo } from 'react'
import {
  PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer,
  RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar,
} from 'recharts'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ChevronDown, ChevronUp, AlertTriangle } from 'lucide-react'
import RichTextContent from '@/components/RichTextContent'
import type {
  CollusionResults,
  CollusionPairResult,
} from '@/services/collusionApi'
import {
  buildRiskChartTitle,
  buildSingleGroupRiskGaugeData,
  COLLUSION_STATS_SCROLL_BODY_CLASS,
  resolveRiskGaugeLevel,
  shouldUseRiskLevelGauge,
} from './collusionStatsLayout'
import { buildCollusionOverviewNotices } from './collusionOverviewNotices'
import { useOneShotOverviewCollapse } from '@/hooks/useOneShotOverviewCollapse'
import { OVERLAP_GUIDANCE_TEXT, hasNavigableOverlapDetail } from './overlapGuidance'
import {
  buildCollusionInterpretationSections,
  getCollusionDecisionSummaryText,
  type CollusionInterpretationTone,
} from './collusionInterpretationSections'
import {
  getDecisionConfig, getRiskConfig, RISK_COLORS, RISK_ORDER,
  DIMENSION_KEYS, DIMENSION_SHORT_LABELS, CERT_KEY_MAP,
  getHeatmapColor,
} from './types'
import { getConfidenceLabel } from '@/lib/constants'

const CHART_HEIGHT = 210

const RADAR_GRID_RINGS = [
  { score: 20, radius: 16, stroke: 'hsl(210, 90%, 80%)' },
  { score: 40, radius: 32, stroke: 'hsl(160, 70%, 76%)' },
  { score: 60, radius: 48, stroke: 'hsl(48, 95%, 76%)' },
  { score: 80, radius: 64, stroke: 'hsl(28, 95%, 76%)' },
  { score: 100, radius: 80, stroke: 'hsl(0, 85%, 78%)' },
] as const

const RADAR_SERIES_COLOR = '#9056D8'
const RADAR_SERIES_ACTIVE_COLOR = '#6C40A2'
const RADAR_RADIAL_LINE_COLOR = '#E3D5F5'

const RADAR_GRID_TICKS = RADAR_GRID_RINGS.map(ring => ring.score)

interface RiskLevelGaugeProps {
  riskLevel: string | undefined
}

const RiskLevelGauge: React.FC<RiskLevelGaugeProps> = ({ riskLevel }) => {
  const gaugeData = buildSingleGroupRiskGaugeData(riskLevel)
  const activeGaugeSegment = gaugeData.find(entry => entry.active)

  return (
    <div className="flex w-full flex-col items-center" style={{ height: CHART_HEIGHT }}>
      <div className="w-full flex-1">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={gaugeData}
              cx="50%" cy="50%"
              innerRadius={50} outerRadius={80}
              paddingAngle={2}
              dataKey="value"
              nameKey="name"
            >
              {gaugeData.map((entry) => (
                <Cell
                  key={entry.key}
                  fill={entry.color}
                  opacity={entry.active ? 1 : 0.28}
                  stroke={entry.active ? entry.color : '#ffffff'}
                  strokeWidth={entry.active ? 3 : 1}
                />
              ))}
            </Pie>
            <text
              x="50%"
              y="50%"
              textAnchor="middle"
              dominantBaseline="central"
              className="text-lg font-bold"
              fill={activeGaugeSegment?.color || '#475569'}
            >
              {activeGaugeSegment?.name || '待复核'}
            </text>
            <Tooltip
              formatter={(_, name) => [
                activeGaugeSegment?.name === name ? '当前判定' : '风险等级',
                name,
              ]}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-1 grid grid-cols-5 gap-x-2 gap-y-1 text-[11px] text-gray-600">
        {gaugeData.map(entry => (
          <span key={entry.key} className="inline-flex items-center justify-center gap-1 whitespace-nowrap">
            <span
              className="h-2.5 w-2.5 rounded-sm"
              style={{ backgroundColor: entry.color, opacity: entry.active ? 1 : 0.45 }}
            />
            {entry.name}
          </span>
        ))}
      </div>
    </div>
  )
}

const INTERPRETATION_STYLE: Record<CollusionInterpretationTone, {
  wrap: string
  title: string
  badge: string
}> = {
  neutral: {
    wrap: 'bg-white border-gray-200',
    title: 'text-gray-700',
    badge: 'bg-gray-100 text-gray-600 border-gray-200',
  },
  warning: {
    wrap: 'bg-amber-50 border-amber-200',
    title: 'text-amber-800',
    badge: 'bg-amber-100 text-amber-700 border-amber-200',
  },
  fact: {
    wrap: 'bg-gray-50 border-gray-200',
    title: 'text-gray-700',
    badge: 'bg-gray-100 text-gray-600 border-gray-200',
  },
  ai: {
    wrap: 'bg-blue-50/40 border-blue-100',
    title: 'text-blue-800',
    badge: 'bg-blue-50 text-blue-700 border-blue-200',
  },
}

interface CollusionStatsProps {
  results: CollusionResults
  sortedPairs: CollusionPairResult[]
  replaceRefs: (text: string | undefined) => string
  onCellClick?: (docAIdx: number, docBIdx: number) => void
}

const CollusionStats: React.FC<CollusionStatsProps> = ({
  results, sortedPairs, replaceRefs, onCellClick,
}) => {
  const { collapsed, toggleCollapsed, bodyRef, rootRef } = useOneShotOverviewCollapse()
  const overallRisk = getRiskConfig(results.projectRiskLevel || results.overallRisk)
  const decision = getDecisionConfig(results.projectDecision)

  const chartUsesGroups = results.riskDistributionBasis === 'groups'
  const riskUnit = chartUsesGroups ? '组' : '对'
  const overviewNotices = useMemo(() => buildCollusionOverviewNotices({
    dataQualityFlags: results.dataQualityFlags,
    profileQualityFlags: results.profileQualityFlags,
    industryProfileConfidence: results.industryProfileConfidence,
  }), [results.dataQualityFlags, results.profileQualityFlags, results.industryProfileConfidence])

  const showOverlapGuidance = useMemo(
    () => hasNavigableOverlapDetail(sortedPairs),
    [sortedPairs],
  )
  const interpretationSections = useMemo(() => buildCollusionInterpretationSections({
    hasTenderDoc: results.hasTenderDoc,
    overviewNotices,
    riskConsistencyNote: results.riskConsistencyStatus === 'diverged'
      ? results.riskConsistencyNote
      : undefined,
    decisionEngineVersion: results.decisionEngineVersion,
    narrativeSummary: results.narrativeSummary,
    factualExplanation: results.factualExplanation,
    factualExplanationItems: results.factualExplanationItems,
    synthesisSummary: results.synthesisSummary,
    investigationSuggestions: results.investigationSuggestions,
  }), [overviewNotices, results])
  const decisionSummaryText = getCollusionDecisionSummaryText({
    projectDecision: results.projectDecision,
    reviewRequired: results.reviewRequired,
    fallbackHint: decision.hint,
  })

  const riskData = useMemo(() => {
    const counts: Record<string, number> = {}
    if (chartUsesGroups) {
      for (const g of results.groups) {
        const key = g.riskLevel.toLowerCase()
        counts[key] = (counts[key] || 0) + 1
      }
    } else {
      for (const p of sortedPairs) {
        const key = (p.riskLevel || p.overallRisk).toLowerCase()
        counts[key] = (counts[key] || 0) + 1
      }
    }
    return Object.entries(counts)
      .sort(([a], [b]) => (RISK_ORDER[a] ?? 5) - (RISK_ORDER[b] ?? 5))
      .map(([key, value]) => ({
        key,
        name: getRiskConfig(key).label,
        value,
        color: RISK_COLORS[key] || RISK_COLORS['inconclusive'],
      }))
  }, [chartUsesGroups, results.groups, sortedPairs])

  const showRiskLevelGauge = shouldUseRiskLevelGauge({
    groupCount: results.groups.length,
    distinctRiskCount: riskData.length,
  })
  const riskChartTitle = buildRiskChartTitle({
    useGauge: showRiskLevelGauge,
    groupCount: results.groups.length,
    riskDistributionBasis: results.riskDistributionBasis,
  })
  const gaugeRiskLevel = resolveRiskGaugeLevel({
    groupCount: results.groups.length,
    groupRiskLevel: results.groups[0]?.riskLevel,
    singleDistributionRiskKey: riskData.length === 1 ? riskData[0].key : undefined,
    projectRiskLevel: results.projectRiskLevel || results.overallRisk,
  })

  const radarData = useMemo(() => {
    const sums: Record<string, { total: number; count: number }> = {}
    for (const p of sortedPairs) {
      const certs = p.dimensionCertainties || {}
      for (const key of DIMENSION_KEYS) {
        const score = p[key as keyof CollusionPairResult] as number | undefined | null
        if (score !== undefined && score !== null && score > 0) {
          if (!sums[key]) sums[key] = { total: 0, count: 0 }
          const certainty = certs[CERT_KEY_MAP[key]] ?? null
          const dampened = certainty !== null && certainty < 0.3
            ? score * 0.2
            : score
          sums[key].total += dampened
          sums[key].count += 1
        }
      }
    }
    return DIMENSION_KEYS.map(key => ({
      dimension: DIMENSION_SHORT_LABELS[key],
      score: sums[key] ? Math.round((sums[key].total / sums[key].count) * 100) : 0,
      fullMark: 100,
    }))
  }, [sortedPairs])

  const { similarityMatrix, documentLabels } = results

  return (
    <div ref={rootRef} className="bg-white rounded-lg shadow-sm mb-4 overflow-hidden">
      {/* 始终可见的概览头部 */}
      <div className="flex items-center justify-between px-4 py-3 border-b">
        <div className="flex items-center gap-4 flex-wrap">
          <h2 className="font-medium text-gray-700">分析概览</h2>
          <div className="flex items-center gap-3 text-sm">
            <span className="text-gray-500">
              <span className="font-semibold text-gray-800">{documentLabels.length}</span> 份投标
            </span>
            <span className="text-gray-500">
              <span className="font-semibold text-gray-800">{sortedPairs.length}</span> 对比对
            </span>
            <span className="text-gray-500">
              覆盖率 <span className="font-semibold text-gray-800">{Math.round((results.coverageStats?.coverageRatio || 0) * 100)}%</span>
            </span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {collapsed && !results.hasTenderDoc && (
            <span className="flex items-center gap-1 text-xs text-amber-600 font-semibold bg-amber-50 border border-amber-300 rounded-full px-2.5 py-0.5">
              <AlertTriangle className="h-3 w-3 shrink-0" />
              未上传招标文件, 请注意甄别
            </span>
          )}
          <Button variant="ghost" size="sm" onClick={toggleCollapsed} data-guide="collusion-collapse-chart">
            {collapsed ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
            <span className="ml-1 text-[14px]">{collapsed ? '展开图表' : '收起图表'}</span>
          </Button>
        </div>
      </div>

      {!collapsed && (
        <div ref={bodyRef} className={COLLUSION_STATS_SCROLL_BODY_CLASS}>
          {/* 三图表区 -- 统一高度 */}
          <div className="grid grid-cols-3 gap-6">
            {/* 可疑度矩阵热力图 */}
            <div className="flex flex-col items-center">
              <div className="text-xs text-gray-500 mb-2">串标可疑度矩阵</div>
              <div className="w-full flex items-center justify-center" style={{ height: CHART_HEIGHT }}>
                <HeatmapChart
                  matrix={similarityMatrix}
                  labels={documentLabels}
                  onCellClick={onCellClick}
                  maxHeight={CHART_HEIGHT}
                />
              </div>
            </div>

            {/* 风险等级分布 / 风险判定圆环 */}
            <div className="flex flex-col items-center">
              <div className="text-xs text-gray-500 mb-2">{riskChartTitle}</div>
              {showRiskLevelGauge ? (
                <RiskLevelGauge riskLevel={gaugeRiskLevel} />
              ) : (
                <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
                  <PieChart>
                    <Pie
                      data={riskData}
                      cx="50%" cy="45%"
                      innerRadius={50} outerRadius={80}
                      paddingAngle={2}
                      dataKey="value"
                    >
                      {riskData.map((entry) => (
                        <Cell key={entry.key} fill={entry.color} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(value, name) => [`${value} ${riskUnit}`, name]} />
                    <Legend
                      wrapperStyle={{ fontSize: '11px' }}
                      formatter={(value: string) => <span className="text-gray-600">{value}</span>}
                    />
                  </PieChart>
                </ResponsiveContainer>
              )}
            </div>

            {/* 维度综合雷达图 */}
            <div className="flex flex-col items-center">
              <div className="text-xs text-gray-500 mb-2">维度综合得分</div>
              <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
                <RadarChart data={radarData} outerRadius={80}>
                  <PolarGrid
                    radialLines={true}
                    polarRadius={[]}
                    stroke={RADAR_RADIAL_LINE_COLOR}
                    strokeDasharray="3 3"
                  />
                  {RADAR_GRID_RINGS.map(ring => (
                    <PolarGrid
                      key={ring.score}
                      radialLines={false}
                      polarRadius={[ring.radius]}
                      stroke={ring.stroke}
                      strokeDasharray="3 3"
                    />
                  ))}
                  <PolarAngleAxis dataKey="dimension" tick={{ fontSize: 10 }} />
                  <PolarRadiusAxis
                    angle={90}
                    domain={[0, 100]}
                    ticks={RADAR_GRID_TICKS}
                    axisLine={false}
                    tickLine={false}
                    tick={{ fontSize: 9, fill: '#9ca3af' }}
                  />
                  <Radar
                    dataKey="score" stroke={RADAR_SERIES_COLOR}
                    fill={RADAR_SERIES_COLOR} fillOpacity={0.25}
                    dot={{ r: 3, fill: RADAR_SERIES_COLOR, strokeWidth: 0 }}
                    activeDot={{ r: 5, fill: RADAR_SERIES_ACTIVE_COLOR, strokeWidth: 0 }}
                  />
                  <Tooltip formatter={(value) => [`${value}`, '综合得分']} />
                </RadarChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="border-t pt-4">
            <div className="rounded-lg border border-[#E3D5F5] bg-[#F6F1FC]/80 px-4 py-3">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <p className="text-xs font-semibold text-[#6C40A2] mb-1">项目最终结论</p>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-base font-semibold text-gray-900">{decision.label}</span>
                    {(results.projectDecision === 'suspicious_found' || results.projectDecision === 'clear_no_risk') && (
                      <Badge
                        variant={overallRisk.label === '高风险' ? 'destructive' : overallRisk.label === '无风险' ? 'success' : 'warning'}
                        className="text-xs"
                      >
                        {overallRisk.label}
                      </Badge>
                    )}
                    <Badge variant="outline" className="text-xs bg-white">
                      可信度 {getConfidenceLabel(results.decisionConfidence)}
                    </Badge>
                    {results.reviewRequired && (
                      <Badge variant="warning" className="text-xs">需人工复核</Badge>
                    )}
                  </div>
                  <p className="mt-1 text-sm text-gray-600">
                    {decisionSummaryText}
                  </p>
                </div>
                <div className="hidden lg:grid grid-cols-3 gap-3 text-center shrink-0">
                  <div className="rounded-md bg-white/80 border border-[#E3D5F5] px-3 py-2">
                    <div className="text-lg font-semibold text-gray-900">{documentLabels.length}</div>
                    <div className="text-[11px] text-gray-500">投标文件</div>
                  </div>
                  <div className="rounded-md bg-white/80 border border-[#E3D5F5] px-3 py-2">
                    <div className="text-lg font-semibold text-gray-900">{sortedPairs.length}</div>
                    <div className="text-[11px] text-gray-500">完成比对</div>
                  </div>
                  <div className="rounded-md bg-white/80 border border-[#E3D5F5] px-3 py-2">
                    <div className="text-lg font-semibold text-gray-900">
                      {Math.round((results.coverageStats?.coverageRatio || 0) * 100)}%
                    </div>
                    <div className="text-[11px] text-gray-500">覆盖率</div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {interpretationSections.length > 0 && (
            <div className="border-t pt-3 space-y-3">
              <p className="text-xs font-semibold text-gray-500">结果解读</p>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                {interpretationSections.map(section => {
                  const style = INTERPRETATION_STYLE[section.tone]
                  return (
                    <section key={section.title} className={`rounded-lg border px-3 py-2.5 ${style.wrap}`}>
                      <div className="flex items-center gap-2 mb-1.5">
                        <p className={`text-xs font-semibold ${style.title}`}>{section.title}</p>
                        {section.badge && (
                          <span className={`text-[10px] px-1.5 py-0.5 rounded border ${style.badge}`}>
                            {section.badge}
                          </span>
                        )}
                      </div>
                      {section.items.length > 1 ? (
                        <ol className="list-decimal pl-5 space-y-1">
                          {section.items.map(item => (
                            <li key={`${section.title}-${item}`} className="text-sm text-gray-700 leading-relaxed">
                              <RichTextContent text={replaceRefs(item)} className="inline" />
                            </li>
                          ))}
                        </ol>
                      ) : (
                        <RichTextContent
                          text={replaceRefs(section.items[0])}
                          className="text-sm text-gray-700 leading-relaxed whitespace-pre-line"
                        />
                      )}
                    </section>
                  )
                })}
              </div>
            </div>
          )}

          {/* 重合明细导航引导: 概览只给结论, 重合原文/高相似片段在右侧详情面板,
              此处显式指路, 弥补"看完概览不知去哪看证据"的体验断层。
              仅在确有可导航内容(findings/evidenceChunks)时展示, 避免空数据误导。 */}
          {showOverlapGuidance && (
            <div className="border-t pt-3" data-guide="collusion-overlap-hint">
              <p className="text-sm text-gray-600 bg-blue-50/60 border border-blue-100 rounded-md px-3 py-2 leading-relaxed">
                {OVERLAP_GUIDANCE_TEXT}
              </p>
            </div>
          )}

          {/* 提取失败警告 */}
          {results.extractionWarnings && results.extractionWarnings.length > 0 && (
            <div className="p-3 bg-red-50 rounded-lg border border-red-200">
              <p className="text-xs font-semibold text-red-700 mb-1">部分文件处理异常</p>
              {results.extractionWarnings.map((w, i) => (
                <p key={`ew-${i}`} className="text-xs text-red-600 leading-relaxed">{w}</p>
              ))}
              <p className="text-xs text-red-600 mt-1">
                建议返回重新发起分析，系统将自动跳过已成功的文件，仅重试失败部分。
              </p>
            </div>
          )}

        </div>
      )}
    </div>
  )
}

// ==================== 热力图子组件（宽高双约束） ====================

interface HeatmapChartProps {
  matrix: (number | null)[][]
  labels: string[]
  onCellClick?: (row: number, col: number) => void
  maxHeight: number
}

const HeatmapChart: React.FC<HeatmapChartProps> = ({ matrix, labels, onCellClick, maxHeight }) => {
  const containerRef = React.useRef<HTMLDivElement>(null)
  const [containerWidth, setContainerWidth] = React.useState(0)

  React.useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const observer = new ResizeObserver(entries => {
      for (const entry of entries) setContainerWidth(entry.contentRect.width)
    })
    observer.observe(el)
    setContainerWidth(el.clientWidth)
    return () => observer.disconnect()
  }, [])

  const n = labels.length
  if (n === 0) return <div className="text-xs text-gray-400">无矩阵数据</div>

  const FONT_SIZE = 10
  const LEGEND_H = 24
  const HEADER_H = 18
  const LABEL_W = 60
  const CJK_W = FONT_SIZE * 1.1
  const LATIN_W = FONT_SIZE * 0.6
  const ELLIPSIS_W = LATIN_W * 2

  const estimateWidth = (text: string) => {
    let w = 0
    for (const ch of text) w += ch.charCodeAt(0) > 0x7f ? CJK_W : LATIN_W
    return w
  }

  const truncate = (name: string, maxPx: number) => {
    if (maxPx < ELLIPSIS_W) return '..'
    let w = 0
    for (let i = 0; i < name.length; i++) {
      const cw = name.charCodeAt(i) > 0x7f ? CJK_W : LATIN_W
      if (w + cw + ELLIPSIS_W > maxPx && i < name.length - 1) {
        return name.slice(0, Math.max(1, i)) + '..'
      }
      w += cw
    }
    return name
  }

  const cleanNames = labels.map(l => l.replace(/\.pdf$/i, ''))

  // cellSize 使用固定预留值(LABEL_W=60, HEADER_H=18)，保持矩阵尺寸与其他图表协调
  const cellSize = Math.max(20, Math.min(
    Math.floor((containerWidth - LABEL_W - 8) / n),
    Math.floor((maxHeight - LEGEND_H - HEADER_H - 8) / n),
  ))
  const gridSize = cellSize * n

  // 行标签: 截断到固定 LABEL_W 宽度内
  const rowLabels = cleanNames.map(name => truncate(name, LABEL_W - 8))

  // 列标签: 先尝试水平截断
  const horizColLabels = cleanNames.map(name => truncate(name, cellSize - 4))
  const horizMaxPx = Math.max(...horizColLabels.map(estimateWidth))
  const shouldRotate = horizMaxPx > cellSize - 2

  // 旋转时允许更长的截断(斜排空间大)
  const colLabels = shouldRotate
    ? cleanNames.map(name => truncate(name, cellSize * 1.8))
    : horizColLabels

  const gridX = LABEL_W
  const gridY = HEADER_H
  const svgW = gridX + gridSize + 4
  const svgH = gridY + gridSize + 4
  const valFont = cellSize > 50 ? 13 : cellSize > 35 ? 11 : 9

  return (
    <div
      ref={containerRef}
      className="w-full flex flex-col items-center justify-end"
      style={{ height: maxHeight, overflow: 'visible' }}
    >
      {containerWidth > 0 && (
        <>
          <svg width={svgW} height={svgH} style={{ overflow: 'visible' }}>
            {/* 底层: 矩阵格子 + 行标签 */}
            <g>
              {matrix.map((row, ri) => (
                <g key={`row-${ri}`}>
                  <text
                    x={gridX - 6}
                    y={gridY + ri * cellSize + cellSize / 2 + 3}
                    textAnchor="end"
                    fontSize={FONT_SIZE}
                    className="fill-gray-500"
                  >
                    {rowLabels[ri]}
                  </text>
                  {row.map((val, ci) => {
                    const isSelf = ri === ci
                    const cx = gridX + ci * cellSize
                    const cy = gridY + ri * cellSize
                    return (
                      <g key={`cell-${ri}-${ci}`}>
                        <rect
                          x={cx} y={cy}
                          width={cellSize - 2} height={cellSize - 2} rx={3}
                          fill={isSelf ? '#e5e7eb' : getHeatmapColor(val)}
                          className={!isSelf && onCellClick ? 'cursor-pointer hover:opacity-80' : ''}
                          onClick={() => { if (!isSelf && onCellClick) onCellClick(ri, ci) }}
                        />
                        <text
                          x={cx + (cellSize - 2) / 2}
                          y={cy + (cellSize - 2) / 2 + 4}
                          textAnchor="middle"
                          fontSize={valFont}
                          className={`pointer-events-none ${isSelf ? 'fill-gray-400' : 'fill-gray-800 font-medium'}`}
                        >
                          {isSelf ? '--' : (val != null ? Math.round(val * 100) : '')}
                        </text>
                      </g>
                    )
                  })}
                </g>
              ))}
            </g>

            {/* 顶层: 列头标签(后渲染不被遮挡，旋转时向上溢出) */}
            <g>
              {colLabels.map((label, ci) => {
                const x = gridX + ci * cellSize + cellSize / 2
                if (shouldRotate) {
                  const y = gridY - 6
                  return (
                    <text
                      key={`col-${ci}`}
                      x={x} y={y}
                      textAnchor="end"
                      fontSize={FONT_SIZE}
                      className="fill-gray-500"
                      transform={`rotate(-45, ${x}, ${y})`}
                    >
                      {label}
                    </text>
                  )
                }
                return (
                  <text
                    key={`col-${ci}`}
                    x={x} y={gridY - 5}
                    textAnchor="middle"
                    fontSize={FONT_SIZE}
                    className="fill-gray-500"
                  >
                    {label}
                  </text>
                )
              })}
            </g>
          </svg>

          <div className="mt-2 flex" style={{ width: svgW }}>
            <div className="flex items-center justify-center gap-1.5" style={{ marginLeft: gridX, width: gridSize }}>
              <span className="text-[10px] text-gray-400">0</span>
              <div className="h-2 w-20 rounded-sm" style={{
                background: 'linear-gradient(to right, rgb(34,197,94), rgb(234,179,8), rgb(239,68,68))'
              }} />
              <span className="text-[10px] text-gray-400">100</span>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

export default CollusionStats
