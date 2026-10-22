/**
 * 原文评分组构成（显示侧）。
 *
 * 职责: 把账本组行(原文组标签 + 已提取分值)转为横幅可用的构成明细。
 * 不做: 不做枚举卷映射、不判定通过与否、不改动账本数据、不承担文案拼装
 * (由 requirementReviewTransforms.buildScoringHeadlineText 负责)。
 *
 * 为何独立成模块: `requirementReviewTransforms.ts` 已达 783 行, 按复杂度护栏
 * 「存量文件禁止新增能力」不得再向其追加新函数。
 */
import type {
  ScoringLedgerReport,
  ScoringVolumeShare,
} from '../requirementReviewTransforms'

/** 组行合计与提取总分的允许偏差，与账本容差同量级。 */
const COVERAGE_TOLERANCE = 0.01

/**
 * 取原文组构成；仅在组行**完整覆盖**已提取分值时给出。
 *
 * 完整性门禁的意义: 若存在未归组分值(unlabeled)或组行合计对不上提取总分，
 * 展示这份构成会让用户以为已窥全貌，实则残缺。此时返回空数组，让调用方
 * 回退到既有枚举卷构成，宁可粗但不残。
 *
 * 保留文档顺序(不按分值降序): 组行来自分组列自上而下的识别顺序，与用户翻阅
 * 招标文件的顺序一致，便于逐条对照。
 */
export function ledgerGroupShares(
  ledger: ScoringLedgerReport | null | undefined,
): ScoringVolumeShare[] {
  if (!ledger || ledger.groupRows.length === 0) return []
  if (Math.abs(ledger.unlabeledSum) > COVERAGE_TOLERANCE) return []
  const covered = ledger.groupRows.reduce((sum, row) => sum + row.extractedSum, 0)
  if (Math.abs(covered - ledger.extractedTotal) > COVERAGE_TOLERANCE) return []
  return ledger.groupRows
    .filter((row) => row.label !== '' && row.extractedSum > 0)
    .map((row) => ({ label: row.label, score: row.extractedSum }))
}
