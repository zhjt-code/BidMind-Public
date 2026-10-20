import { formatExtractionWarning } from '../RequirementReview/extractionWarningCopy.ts'
import { getCategoryLabel, getConclusionText } from './types.ts'
import { isSubstantiveGap } from './pendingJudgment.ts'

type ReviewConclusion = 'pass' | 'fail' | 'pending'
type ComplianceLevel = 'fully' | 'partially' | 'not' | 'missing'
type RiskLevel = 'none' | 'low' | 'medium' | 'high'

interface ReviewStatsVolumeScore {
  estimated: number
  max: number
  count: number
  weight?: number | null
}

interface ReviewStatsLotScore {
  estimated: number
  max: number
  count: number
  estimatedCount: number
  reliable: boolean
  unassigned: boolean
  byVolume: Record<string, ReviewStatsVolumeScore>
}

interface ReviewStatsSummary {
  weightedScore: number
  conclusion: ReviewConclusion
  totalCount: number
  compliantCount: number
  highRiskCount: number
  scoringItemCount: number
  estimatedItemCount: number
  estimatedTotalScore: number
  maxTotalScore: number
  estimatedScoreByVolume: Record<string, ReviewStatsVolumeScore>
  scoringTotalReliable: boolean
  /** 按采购包核计：仅多包文档非空；非空时按包播报，禁止跨包汇总。 */
  estimatedScoreByLot: Record<string, ReviewStatsLotScore>
}

interface ReviewStatsResult {
  category: string
  score: number
  complianceLevel: ComplianceLevel
  riskLevel: RiskLevel
  pendingJudgment?: boolean
}

export type ReviewFocusTone = 'danger' | 'warning' | 'info' | 'neutral'

export interface ReviewFocusSection {
  title: string
  tone: ReviewFocusTone
  items: ReviewFocusItem[]
  /** 板块级通用注释：不参与条目编号，渲染层以突出样式展示 */
  note?: string
}

export interface ReviewFocusItem {
  text: string
  children?: string[]
}

interface BuildReviewFocusSectionsOptions {
  summary: ReviewStatsSummary
  results: ReviewStatsResult[]
  extractionWarnings?: string[]
}

const cleanItems = (items: Array<string | undefined | null>): string[] =>
  items.map(item => (typeof item === 'string' ? item.trim() : '')).filter(Boolean)

const formatPercent = (value: number | null | undefined): string =>
  value == null ? '' : `${Math.round(value * 100)}%`

const toFocusItems = (items: string[]): ReviewFocusItem[] =>
  items.map(text => ({ text }))

const REVIEW_USAGE_LIMITATION_TEXT =
  '本页结果由 AI 辅助生成，综合符合度用于衡量响应质量，预估评标得分仅按评分表估算，最终结论仍需结合招标原文和人工审核确认。'

export const SCORING_ESTIMATE_NOTE =
  '按招标评分表估算，仅供参考，不等同于响应质量分或正式评委打分。'

export function buildReviewHeaderMetrics(summary: ReviewStatsSummary): string[] {
  const metrics = [
    `${summary.totalCount} 项要求`,
    `符合 ${summary.compliantCount} 项`,
  ]
  if (summary.highRiskCount > 0) {
    metrics.push(`高风险 ${summary.highRiskCount} 项`)
  }
  if (summary.scoringItemCount > 0) {
    metrics.push(`评分项 ${summary.scoringItemCount} 项`)
  }
  return metrics
}

export function getReviewDecisionSummaryText(summary: Pick<ReviewStatsSummary, 'conclusion' | 'highRiskCount'>): string {
  if (summary.conclusion === 'fail' || summary.highRiskCount > 0) {
    return '当前项目存在高风险不符合项，建议优先核查高风险与不符合要求。'
  }
  if (summary.conclusion === 'pass') {
    return '当前项目未发现高风险不符合项，建议抽查关键要求与评分项依据。'
  }
  return '当前项目仍有待澄清内容，建议优先核查部分符合、未响应和中高风险要求。'
}

function buildWeakCategoryItems(results: ReviewStatsResult[]): string[] {
  const scoreMap: Record<string, { total: number; count: number; nonCompliantCount: number }> = {}
  for (const result of results) {
    const label = getCategoryLabel(result.category).replace('要求', '')
    if (!scoreMap[label]) scoreMap[label] = { total: 0, count: 0, nonCompliantCount: 0 }
    scoreMap[label].total += result.score
    scoreMap[label].count += 1
    if (isSubstantiveGap(result)) {
      scoreMap[label].nonCompliantCount += 1
    }
  }
  return Object.entries(scoreMap)
    .map(([label, stat]) => ({
      label,
      score: Math.round(stat.total / stat.count),
      count: stat.count,
      nonCompliantCount: stat.nonCompliantCount,
    }))
    .sort((a, b) => a.score - b.score)
    .slice(0, 2)
    .map(item => `${item.label}类响应偏弱：共 ${item.count} 项，其中 ${item.nonCompliantCount} 项未响应或不符合，建议优先查看该分类下的不符合、未响应和低分条目。`)
}

function buildVolumeDetailItems(
  byVolume: Record<string, ReviewStatsVolumeScore>,
  hasEstimate = true,
): string[] {
  return Object.entries(byVolume).map(([volume, value]) => {
    const scoreText = `${hasEstimate ? value.estimated : '—'}/${value.max}`
    const weightText = value.weight != null ? ` · 权重${formatPercent(value.weight)}` : ''
    return `${volume} ${scoreText}（${value.count}项${weightText}）`
  })
}

/** 多采购包播报：各包独立核计，与确认页横幅口径一致，禁止跨包相加。 */
function buildLotScoringItems(byLot: Record<string, ReviewStatsLotScore>): ReviewFocusItem[] {
  const items: ReviewFocusItem[] = [{ text: '本项目按采购包分别评审，各包独立核计，不汇总跨包总分。' }]
  for (const [lot, value] of Object.entries(byLot)) {
    const children = buildVolumeDetailItems(value.byVolume, value.estimatedCount > 0)
    if (value.unassigned) {
      items.push({
        text: `另有 ${value.count} 项评分项（满分合计 ${value.max} 分）未识别采购包归属，未计入任何采购包核计，请人工核对其所属包。`,
        children,
      })
      continue
    }
    const hasEstimate = value.estimatedCount > 0
    if (!hasEstimate) {
      items.push({
        text: `${lot}：满分合计 ${value.max} 分（${value.count}项），预估得分待重新发起审查后生成。`,
        children,
      })
    } else if (value.reliable) {
      items.push({
        text: `${lot}：预估评标得分 ${value.estimated} / ${value.max}（${value.count}项）`,
        children,
      })
    } else {
      items.push({
        text: `${lot}：评分结构较复杂，暂不汇总该包总分（${value.count}项），下列按卷参考。`,
        children,
      })
    }
  }
  return items
}

function buildScoringItems(summary: ReviewStatsSummary): ReviewFocusItem[] {
  if (summary.scoringItemCount <= 0) return []
  // 多包文档优先按包播报——两包各自独立百分制时相加成"200分制"
  // 会被单一100制可信判定误判为"结构复杂"，与确认页按包核计结论矛盾。
  const byLot = summary.estimatedScoreByLot ?? {}
  if (Object.keys(byLot).length > 0) {
    return buildLotScoringItems(byLot)
  }
  const volumeItems = buildVolumeDetailItems(summary.estimatedScoreByVolume)
  if (summary.estimatedItemCount <= 0) {
    return [
      { text: `评分项满分合计 ${summary.maxTotalScore} 分（${summary.scoringItemCount} 项），预估得分待重新发起审查后生成。` },
    ]
  }
  if (!summary.scoringTotalReliable) {
    return [
      {
        text: '评分结构较复杂（含多卷/加权/非100分制），暂不汇总统一总分，下列按卷分项仅供参考。',
        children: volumeItems,
      },
    ]
  }
  return [
    {
      text: `${summary.estimatedTotalScore} / ${summary.maxTotalScore}`,
      children: volumeItems,
    },
  ]
}

export function buildReviewFocusSections({
  summary,
  results,
  extractionWarnings = [],
}: BuildReviewFocusSectionsOptions): ReviewFocusSection[] {
  const nonCompliantCount = results.filter(r => isSubstantiveGap(r)).length
  const partialCount = results.filter(r => r.complianceLevel === 'partially').length
  const sections: ReviewFocusSection[] = []

  const riskItems = cleanItems([
    summary.highRiskCount > 0 ? `当前存在 ${summary.highRiskCount} 项高风险要求，建议优先逐项核查原文与响应证据。` : '当前未发现高风险要求。',
    nonCompliantCount > 0 ? `不符合或未响应 ${nonCompliantCount} 项，是影响审查结论的主要入口。` : '',
    partialCount > 0 ? `部分符合 ${partialCount} 项，建议核查缺失要素是否可补充说明。` : '',
  ])
  if (riskItems.length > 0) {
    sections.push({ title: '主要风险', tone: summary.highRiskCount > 0 ? 'danger' : 'neutral', items: toFocusItems(riskItems) })
  }

  const weakItems = buildWeakCategoryItems(results)
  if (weakItems.length > 0) {
    sections.push({ title: '响应薄弱分类', tone: 'warning', items: toFocusItems(weakItems) })
  }

  const scoringItems = buildScoringItems(summary)
  if (scoringItems.length > 0) {
    sections.push({ title: '预估评标得分', tone: 'info', items: scoringItems, note: SCORING_ESTIMATE_NOTE })
  }

  const limitationItems = cleanItems([
    REVIEW_USAGE_LIMITATION_TEXT,
    ...extractionWarnings.map((item) => formatExtractionWarning(item).summary),
  ])
  sections.push({ title: '使用限制', tone: 'warning', items: toFocusItems(limitationItems) })

  return sections
}

export function getReviewConclusionLabel(summary: Pick<ReviewStatsSummary, 'conclusion'>): string {
  return `审查结论：${getConclusionText(summary.conclusion)}`
}
