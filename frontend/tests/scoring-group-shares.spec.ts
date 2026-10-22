/**
 * 原文评分组构成防御测试（显示侧）。
 *
 * 防御目标（映射真实风险 doc1933/项目2447）：
 * - volume 六类枚举把原文 5 个大类压成「技术标 70 分、价格 30 分」，用户无法按
 * 招标文件的评审维度核对。横幅须优先展示原文组构成。
 * - 组行残缺（存在未归组分值 / 合计对不上）时展示这份构成会让用户误以为已窥全貌，
 * 必须回退到枚举卷构成——宁可粗但不残。
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import { ledgerGroupShares } from '../src/pages/RequirementReview/scoringGroupShares.ts'
import {
  buildScoringHeadlineText,
  buildScoringTotalsSummary,
  transformScoringTotalsReport,
} from '../src/pages/requirementReviewTransforms.ts'

/** doc1933 真实形态：5 个原文大类全部闭合，零未归组。 */
function ledgerWithGroups(overrides: Record<string, unknown> = {}) {
  return {
    status: 'review_required',
    basisLevel: 'l1_explicit_group',
    basisLowConfidence: true,
    expectedTotal: 100,
    extractedTotal: 100,
    totalGap: 0,
    groupRows: [
      { label: '设计方案', declared: 26, extractedSum: 26, gap: 0 },
      { label: '施工方案', declared: 30, extractedSum: 30, gap: 0 },
      { label: '项目管理机构评分标准', declared: 9, extractedSum: 9, gap: 0 },
      { label: '投标报价评分标准', declared: 30, extractedSum: 30, gap: 0 },
      { label: '其他因素评分标准', declared: 5, extractedSum: 5, gap: 0 },
    ],
    unlabeledSum: 0,
    multiSchemeSignal: '',
    duplicateExclusions: [],
    insufficiencyCount: 0,
    diagnostics: [],
    ...overrides,
  } as never
}

/** legacy 报告：同一文档被六类枚举压成 technical 70 / price 30。 */
function legacySummary() {
  const report = transformScoringTotalsReport({
    needs_review: false,
    by_volume: { technical: 70, price: 30 },
    volume_checks: {},
    item_count: 19,
    missing_score_count: 0,
    missing_rule_count: 0,
    diagnostics: [],
  })
  assert.notEqual(report, null)
  return buildScoringTotalsSummary(report!)
}

test('组行完整时给出原文 5 个大类构成，保留文档顺序', () => {
  const shares = ledgerGroupShares(ledgerWithGroups())

  assert.deepEqual(
    shares.map((s) => s.label),
    ['设计方案', '施工方案', '项目管理机构评分标准', '投标报价评分标准', '其他因素评分标准'],
    '须保留分组列自上而下的文档顺序，便于逐条对照原文',
  )
  assert.deepEqual(
    shares.map((s) => s.score),
    [26, 30, 9, 30, 5],
  )
})

test('横幅优先展示原文大类，不再显示被压缩的枚举卷', () => {
  const text = buildScoringHeadlineText(legacySummary(), {
    verified: true,
    groupShares: ledgerGroupShares(ledgerWithGroups()),
  })

  assert.match(text, /设计方案 26 分/)
  assert.match(text, /施工方案 30 分/)
  assert.match(text, /其他因素评分标准 5 分/)
  assert.doesNotMatch(text, /技术标 70 分/, '枚举卷压缩结果不得再出现')
})

test('存在未归组分值时回退枚举卷构成（宁可粗但不残）', () => {
  const shares = ledgerGroupShares(ledgerWithGroups({ unlabeledSum: 12 }))
  assert.deepEqual(shares, [])

  const text = buildScoringHeadlineText(legacySummary(), {
    verified: true,
    groupShares: shares,
  })
  assert.match(text, /技术标 70 分/, '残缺构成时须回退到既有枚举卷展示')
})

test('组行合计对不上提取总分时同样回退', () => {
  const shares = ledgerGroupShares(
    ledgerWithGroups({ extractedTotal: 130, totalGap: 30 }),
  )
  assert.deepEqual(shares, [])
})

test('无组行 / 无账本报告时不干预既有文案', () => {
  assert.deepEqual(ledgerGroupShares(ledgerWithGroups({ groupRows: [] })), [])
  assert.deepEqual(ledgerGroupShares(null), [])
  assert.deepEqual(ledgerGroupShares(undefined), [])

  const text = buildScoringHeadlineText(legacySummary(), { verified: true })
  assert.match(text, /技术标 70 分/)
})

test('零分组与空标签行不进入构成明细', () => {
  const shares = ledgerGroupShares(
    ledgerWithGroups({
      extractedTotal: 56,
      groupRows: [
        { label: '设计方案', declared: 26, extractedSum: 26, gap: 0 },
        { label: '施工方案', declared: 30, extractedSum: 30, gap: 0 },
        { label: '', declared: 0, extractedSum: 0, gap: 0 },
        { label: '空组', declared: 0, extractedSum: 0, gap: 0 },
      ],
    }),
  )

  assert.deepEqual(
    shares.map((s) => s.label),
    ['设计方案', '施工方案'],
  )
})
