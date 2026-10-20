/**
 * 评分横幅「新增评分项」按钮可见性（Task7）。
 *
 * 职责: 按「绝对总分硬门槛 + 相对期望总分 + 明确漏提/多提语义 + 语义不清偏离幅度」
 * 判定是否显示新增入口；多包模式下改为消费 scope 结构状态。
 * 不做: 不渲染 UI、不改筛选/列表、不推断期望总分以外的业务结论。
 *
 * 规则摘要（单包 / globalComparisonAllowed=true）:
 * - 分数完全闭合(isOk) → 不显示
 * - 绝对提取总分 < 100 → 必须显示（硬门槛，优先于相对多提隐藏）
 * - 同轴提取 < 期望 → 必须显示
 * - 同轴提取 > 期望且明确漏提 → 显示；明确多提 → 不显示
 * - 同轴提取 > 期望且语义不清：偏离 < 50 不显示，≥ 50 显示
 * - 期望缺失时相对比较退回 100 作为弱基准
 *
 * 多包规则（globalComparisonAllowed=false）:
 * - 当某 applicable（非低置信）scope 的 totalGap < 0（明确负 gap）时显示
 * - : 误降级 unresolved（对账质量本应 applicable）且负 gap 同样显示
 * - all-pass / 真质量不足 unresolved / 多提均不显示
 * - 不得使用默认 100 或偏离 50 阈值（见 任务要求）
 */
import {
  isMisclassifiedApplicable,
  SCOPE_SCORE_TOLERANCE,
  type ScopedReport,
} from './scoringScopeReportTransforms.ts'
import type {
  ScoringLedgerGroupRow,
  ScoringLedgerReport,
  ScoringTotalsReport,
  VolumeCheck,
} from '../requirementReviewTransforms'

const ABSOLUTE_LOW_SCORE_SHOW_MAX = 100
const FALLBACK_EXPECTED_TOTAL = 100
const UNCLEAR_OVERAGE_SHOW_MIN = 50

export interface ScoringAddItemVisibilityInput {
  /** 横幅整体已通过（绿色闭合）时一律不显示。 */
  isOk: boolean
  /** 权威期望总分；null 时退回弱基准 100（仅单包模式使用）。 */
  expectedTotal: number | null
  /** 与期望同轴的已提取总分（折算场景用折算后值；单包模式）。 */
  extractedTotal: number
/**
 * 用户可见的绝对提取总分（未折算原始合计）；用于「<100 硬显示」（单包模式）。
 * 缺省时回退 extractedTotal。
 */
  absoluteExtractedTotal?: number
  /** 账本总差额（提取 − 期望）；有则优先用作偏离方向（单包模式）。 */
  totalGap?: number | null
  missingScoreCount?: number
  volumeChecks?: Record<string, VolumeCheck>
  groupRows?: ScoringLedgerGroupRow[]
/**
 * Task7: false = 多包模式，改用 scopeReports 判断可见性，
 * 不使用默认 100 或偏离 50 阈值。true/undefined = 单包模式，沿用旧逻辑。
 */
  globalComparisonAllowed?: boolean
/**
 * Task7: 多包模式下的 scope 结构状态列表。
 * 仅当 globalComparisonAllowed=false 且某 applicable scope totalGap<0 时显示入口。
 */
  scopeReports?: ScopedReport[]
}

/** 从确认页已有报告装配可见性入参（折算场景优先用折算后提取做相对比较）。 */
export function buildScoringAddItemVisibilityInput(args: {
  isOk: boolean
  report: ScoringTotalsReport | null
  ledgerReport: ScoringLedgerReport | null
}): ScoringAddItemVisibilityInput {
  const { isOk, report, ledgerReport } = args
  const rawExtracted = ledgerReport?.extractedTotal ?? reportSum(report)
  const folded = ledgerReport?.extractedTotalFolded
  const extractedTotal =
    folded != null && Number.isFinite(folded) ? folded : rawExtracted
  const expectedFromVolumes = report ? sumVolumeExpected(report.volumeChecks) : null
  return {
    isOk,
    expectedTotal: ledgerReport?.expectedTotal ?? expectedFromVolumes,
    extractedTotal,
    absoluteExtractedTotal: rawExtracted,
    totalGap: ledgerReport?.totalGap ?? null,
    missingScoreCount: report?.missingScoreCount ?? 0,
    volumeChecks: report?.volumeChecks,
    groupRows: ledgerReport?.groupRows,
    globalComparisonAllowed: ledgerReport?.globalComparisonAllowed,
    scopeReports: ledgerReport?.scopeReports,
  }
}

function reportSum(report: ScoringTotalsReport | null): number {
  if (!report) return 0
  return Object.values(report.byVolume).reduce((acc, n) => acc + (Number.isFinite(n) ? n : 0), 0)
}

function sumVolumeExpected(checks: Record<string, VolumeCheck>): number | null {
  const values = Object.values(checks)
  if (values.length === 0) return null
  const total = values.reduce((acc, c) => acc + c.expected, 0)
  return Number.isFinite(total) ? total : null
}

function hasClearUnderExtraction(input: ScoringAddItemVisibilityInput): boolean {
  if ((input.missingScoreCount ?? 0) > 0) return true
  if (input.volumeChecks) {
    for (const check of Object.values(input.volumeChecks)) {
      if (check.expected - check.sum > SCOPE_SCORE_TOLERANCE) return true
    }
  }
  if (input.groupRows?.some((row) => row.gap < -SCOPE_SCORE_TOLERANCE)) return true
  return false
}

function hasClearOverExtraction(input: ScoringAddItemVisibilityInput): boolean {
  if (input.volumeChecks) {
    for (const check of Object.values(input.volumeChecks)) {
      if (check.sum - check.expected > SCOPE_SCORE_TOLERANCE) return true
    }
  }
  if (input.groupRows?.some((row) => row.gap > SCOPE_SCORE_TOLERANCE)) return true
  return false
}

/**
 * 是否显示「新增评分项」。
 *
 * 调用方仍需自行确认 onAddScoringItem 回调存在。
 */
export function shouldShowAddScoringItem(input: ScoringAddItemVisibilityInput): boolean {
  if (input.isOk) return false

  // Task7: 多包模式。不使用默认 100 或偏离 50 阈值，
  // 仅当某 applicable scope 有明确负 gap（extracted < expected）且基准置信度足够时显示。
  // 误降级 unresolved（对账质量本应 applicable）且负 gap 同样显示补录入口。
  // 真质量不足 unresolved / basisLowConfidence / all-pass / 多提均不显示。
  // 无基准(expected=null)且该 scope 绝对提取总分低于百分制下限时，人工兜底
  // 入口必须可达——这只放开"入口可见性"，不产生任何 gap 断言文案，不违反 798 禁令
  // （项目3414 实测: 标段三 55 分/L4 无基准/unresolved，补录按钮被压掉致人工无路可走）。
  if (input.globalComparisonAllowed === false) {
    return (input.scopeReports ?? []).some(
      (scope) =>
        (((scope.applicability === 'applicable' && !scope.basisLowConfidence) ||
          isMisclassifiedApplicable(scope)) &&
          scope.totalGap !== null &&
          scope.totalGap < -SCOPE_SCORE_TOLERANCE) ||
        (scope.expectedTotal === null &&
          scope.extractedTotal < ABSOLUTE_LOW_SCORE_SHOW_MAX - SCOPE_SCORE_TOLERANCE),
    )
  }

  const absolute =
    input.absoluteExtractedTotal != null && Number.isFinite(input.absoluteExtractedTotal)
      ? input.absoluteExtractedTotal
      : Number.isFinite(input.extractedTotal)
        ? input.extractedTotal
        : 0

  // 硬门槛：绝对提取总分低于 100 必须显示（优先于相对期望的「多提不显示」）
  if (absolute < ABSOLUTE_LOW_SCORE_SHOW_MAX - SCOPE_SCORE_TOLERANCE) return true

  const expected =
    input.expectedTotal != null && Number.isFinite(input.expectedTotal)
      ? input.expectedTotal
      : FALLBACK_EXPECTED_TOTAL

  const extracted = Number.isFinite(input.extractedTotal) ? input.extractedTotal : 0
  const delta =
    input.totalGap != null && Number.isFinite(input.totalGap)
      ? input.totalGap
      : extracted - expected

  // 同轴：总分低于期望 → 必须显示
  if (delta < -SCOPE_SCORE_TOLERANCE) return true

  // 同轴已闭合 → 不显示（缺规则等其它 amber 也不诱导新增）
  if (Math.abs(delta) <= SCOPE_SCORE_TOLERANCE) return false

  // 同轴高于期望：明确漏提 / 明确多提 / 语义不清
  if (hasClearUnderExtraction(input)) return true
  if (hasClearOverExtraction(input)) return false

  return delta >= UNCLEAR_OVERAGE_SHOW_MIN - SCOPE_SCORE_TOLERANCE
}
