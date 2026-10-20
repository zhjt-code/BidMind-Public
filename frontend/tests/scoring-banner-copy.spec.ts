/**
 * 评分核对提示条用户文案防御测试。
 *
 * 目标: 只调整确认页提示条的用户理解口径，不改评分账本或后端数据。
 * 锁定四态：全部归卷、部分归卷、完全未归卷、未提取有效评分项。
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import {
  SCORING_MANUAL_CHECK_NOTICE,
  buildScoringBannerHeadlineText,
  buildScoringCompositionText,
  buildSuspectedAssessmentNotice,
} from '../src/pages/RequirementReview/scoringBannerCopy.ts'
import type {
  ScoringLedgerReport,
  ScoringTotalsSummary,
  ScoringVolumeShare,
} from '../src/pages/requirementReviewTransforms.ts'

function summary(overrides: Partial<ScoringTotalsSummary> = {}): ScoringTotalsSummary {
  return {
    itemCount: 7,
    totalScore: 100,
    foldedText: null,
    folded: null,
    shares: [],
    lotShares: [],
    ...overrides,
  }
}

function ledger(overrides: Partial<ScoringLedgerReport> = {}): ScoringLedgerReport {
  return {
    status: 'review_required',
    basisLevel: 'l4_none',
    basisLowConfidence: true,
    expectedTotal: null,
    extractedTotal: 100,
    extractedTotalFolded: null,
    totalGap: null,
    groupRows: [],
    unlabeledSum: 0,
    multiSchemeSignal: '',
    insufficiencyCount: 0,
    inferredGroupLabel: null,
    inferredGroupSum: 0,
    inferredGroupCount: 0,
    diagnostics: [],
    scopeReports: [],
    globalComparisonAllowed: true,
    ...overrides,
  }
}

test('需核对态主结论仍用“已成功提取”，但不宣称完成分值校验', () => {
  assert.equal(
    buildScoringBannerHeadlineText(summary(), { verified: false }),
    '已成功提取 7 个评分项，总分 100 分。',
  )
})

test('存在未归组分值时禁止标题回退展示未经验证的分组构成', () => {
  const unverifiedShares: ScoringVolumeShare[] = [
    { label: '其他', score: 35 },
    { label: '错误分组', score: 25 },
  ]

  const text = buildScoringBannerHeadlineText(
    summary({ itemCount: 8, shares: unverifiedShares }),
    { verified: false, suppressShares: true },
  )

  assert.equal(text, '已成功提取 8 个评分项，总分 100 分。')
  assert.doesNotMatch(text, /其中|其他|错误分组/)
})

test('全部归卷成功时按卷展示构成', () => {
  const shares: ScoringVolumeShare[] = [
    { label: '商务部分', score: 40 },
    { label: '技术部分', score: 60 },
  ]

  assert.equal(
    buildScoringBannerHeadlineText(summary(), { verified: true, groupShares: shares }),
    '已成功提取 7 个评分项并完成分值校验，总分 100 分，其中 商务部分 40 分、技术部分 60 分。',
  )
})

test('部分归卷时把未归卷分值合成“其它提取”展示', () => {
  const text = buildScoringCompositionText(
    ledger({
      groupRows: [
        { label: '商务部分', declared: 40, extractedSum: 40, gap: 0 },
        { label: '技术部分', declared: 60, extractedSum: 0, gap: -60 },
      ],
      unlabeledSum: 60,
    }),
  )

  assert.equal(text, '商务部分 40 分、其它提取 60 分')
  assert.doesNotMatch(text!, /未能归入|未找到总分/)
})

test('完全未归卷时展示为“已提分数项”', () => {
  assert.equal(
    buildScoringCompositionText(ledger({ unlabeledSum: 100 })),
    '已提分数项 100 分',
  )
})

test('未提取到有效评分项时直接给出核实提示', () => {
  assert.equal(
    buildScoringBannerHeadlineText(summary({ itemCount: 0, totalScore: 0 }), {
      verified: false,
    }),
    '未提取到有效评分项，请仔细对照招标文件核实',
  )
})

test('统一人工核实短提示', () => {
  assert.equal(SCORING_MANUAL_CHECK_NOTICE, '注意：请仔细对照招标文件核实')
})

test('疑似履约考核提示含条数与分值', () => {
  assert.equal(buildSuspectedAssessmentNotice(0, 0), '')
  assert.equal(
    buildSuspectedAssessmentNotice(26, 161),
    '另有 26 项疑似履约考核（合计 161 分），建议排除',
  )
})
