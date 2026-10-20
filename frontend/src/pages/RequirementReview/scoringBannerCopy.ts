/**
 * 评分核对提示条文案构造。
 *
 * 职责: 把评分项提取结果转成用户可理解的横幅事实文案。
 * 不做: 不判定评分是否通过、不修改评分项数据、不承担后端账本计算。
 */
import {
  formatScore,
  type ScoringLedgerReport,
  type ScoringTotalsSummary,
  type ScoringVolumeShare,
} from '../requirementReviewTransforms.ts'

const SCORE_TOLERANCE = 0.01

export const SCORING_MANUAL_CHECK_NOTICE = '注意：请仔细对照招标文件核实'

export const SUSPECTED_ASSESSMENT_LABEL = '疑似履约考核，建议排除'

export function buildSuspectedAssessmentNotice(count: number, score: number): string {
  if (count <= 0) return ''
  return `另有 ${count} 项${SUSPECTED_ASSESSMENT_LABEL.split('，')[0]}（合计 ${formatScore(score)} 分），建议排除`
}

function formatShares(shares: ScoringVolumeShare[]): string {
  return shares
    .filter((share) => share.label !== '' && share.score > SCORE_TOLERANCE)
    .map((share) => `${share.label} ${formatScore(share.score)} 分`)
    .join('、')
}

export function buildScoringBannerHeadlineText(
  summary: ScoringTotalsSummary | null,
  options: {
    verified: boolean
    groupShares?: ScoringVolumeShare[]
    suppressShares?: boolean
  },
): string {
  if (!summary) return ''
  if (summary.itemCount <= 0 || summary.totalScore <= SCORE_TOLERANCE) {
    return `未提取到有效评分项，请仔细对照招标文件核实`
  }

  const lead = options.verified
    ? `已成功提取 ${summary.itemCount} 个评分项并完成分值校验`
    : `已成功提取 ${summary.itemCount} 个评分项`

  if (summary.lotShares.length > 0) {
    const lots = summary.lotShares
      .map((share) => `${share.label} 合计 ${formatScore(share.score)} 分`)
      .join('、')
    return `${lead}，${lots}。`
  }

  if (summary.foldedText) {
    return `${lead}，${summary.foldedText}。`
  }

  const shares = options.suppressShares
    ? []
    : options.groupShares && options.groupShares.length > 0
      ? options.groupShares
      : summary.shares
  const shareText = shares.length > 0 ? `，其中 ${formatShares(shares)}` : ''
  return `${lead}，总分 ${formatScore(summary.totalScore)} 分${shareText}。`
}

export function buildScoringCompositionText(
  ledger: ScoringLedgerReport | null | undefined,
  fallbackShares: ScoringVolumeShare[] = [],
): string | null {
  if (ledger && ledger.extractedTotal > SCORE_TOLERANCE) {
    if (ledger.unlabeledSum >= ledger.extractedTotal - SCORE_TOLERANCE) {
      return `已提分数项 ${formatScore(ledger.extractedTotal)} 分`
    }

    const shares = ledger.groupRows
      .filter((row) => row.label !== '' && row.extractedSum > SCORE_TOLERANCE)
      .map((row) => ({ label: row.label, score: row.extractedSum }))

    if (ledger.unlabeledSum > SCORE_TOLERANCE) {
      shares.push({ label: '其它提取', score: ledger.unlabeledSum })
    }

    const text = formatShares(shares)
    if (text !== '') return text
  }

  const fallbackText = formatShares(fallbackShares)
  return fallbackText === '' ? null : fallbackText
}
