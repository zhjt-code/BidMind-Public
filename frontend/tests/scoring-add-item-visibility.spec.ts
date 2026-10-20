/**
 * 防御测试：评分横幅「新增评分项」精确可见性（相对期望总分 + 漏提/多提语义）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import {
  shouldShowAddScoringItem,
  type ScoringAddItemVisibilityInput,
} from '../src/pages/RequirementReview/scoringAddItemVisibility.ts'

const base = (
  overrides: Partial<ScoringAddItemVisibilityInput> = {},
): ScoringAddItemVisibilityInput => ({
  isOk: false,
  expectedTotal: 100,
  extractedTotal: 100,
  missingScoreCount: 0,
  volumeChecks: {},
  groupRows: [],
  ...overrides,
})

test('分数完全闭合(isOk)时不显示', () => {
  assert.equal(shouldShowAddScoringItem(base({ isOk: true, extractedTotal: 80 })), false)
})

test('提取低于期望时必须显示', () => {
  assert.equal(
    shouldShowAddScoringItem(base({ extractedTotal: 28, expectedTotal: 100 })),
    true,
  )
  assert.equal(
    shouldShowAddScoringItem(base({ extractedTotal: 250, expectedTotal: 300 })),
    true,
    '多卷期望 300 时不得用写死 100 误判',
  )
})

test('期望缺失时以 100 为弱基准：低于 100 显示', () => {
  assert.equal(
    shouldShowAddScoringItem(base({ expectedTotal: null, extractedTotal: 78 })),
    true,
  )
})

test('相对期望已闭合时即使非 isOk 也不显示', () => {
  assert.equal(
    shouldShowAddScoringItem(
      base({ isOk: false, extractedTotal: 100, expectedTotal: 100, missingScoreCount: 0 }),
    ),
    false,
  )
})

test('高于期望且明确漏提（缺分值/少分缺口）时显示', () => {
  assert.equal(
    shouldShowAddScoringItem(
      base({
        extractedTotal: 120,
        expectedTotal: 100,
        missingScoreCount: 2,
      }),
    ),
    true,
  )
  assert.equal(
    shouldShowAddScoringItem(
      base({
        extractedTotal: 120,
        expectedTotal: 100,
        groupRows: [{ label: '技术', declared: 40, extractedSum: 20, gap: -20 }],
      }),
    ),
    true,
  )
})

test('高于期望且明确多提时不显示', () => {
  assert.equal(
    shouldShowAddScoringItem(
      base({
        extractedTotal: 105,
        expectedTotal: 100,
        groupRows: [{ label: '技术', declared: 40, extractedSum: 45, gap: 5 }],
      }),
    ),
    false,
  )
  assert.equal(
    shouldShowAddScoringItem(
      base({
        extractedTotal: 105,
        expectedTotal: 100,
        volumeChecks: {
          technical: { sum: 55, expected: 50, weight: 1, within: false },
        },
      }),
    ),
    false,
  )
})

test('高于期望且语义不清时以 50 为界', () => {
  assert.equal(
    shouldShowAddScoringItem(base({ extractedTotal: 105, expectedTotal: 100 })),
    false,
    '偏离 5 < 50 不显示',
  )
  assert.equal(
    shouldShowAddScoringItem(base({ extractedTotal: 295, expectedTotal: 100 })),
    true,
    '偏离 195 >= 50 显示',
  )
})

test('优先使用 totalGap 作为偏离（与账本同轴）', () => {
  assert.equal(
    shouldShowAddScoringItem(
      base({
        extractedTotal: 300,
        absoluteExtractedTotal: 300,
        expectedTotal: 100,
        totalGap: 5,
      }),
    ),
    false,
    'totalGap=5 视为轻度超出，不因 raw extracted=300 误判',
  )
})

test('绝对提取总分低于 100 时硬显示（即使相对期望为多提）', () => {
  // 现场：要求 8、已提 12（多 4）——相对多提，但总分 12<100，必须给新增入口
  assert.equal(
    shouldShowAddScoringItem(
      base({
        extractedTotal: 12,
        absoluteExtractedTotal: 12,
        expectedTotal: 8,
        totalGap: 4,
        groupRows: [{ label: '某卷', declared: 8, extractedSum: 12, gap: 4 }],
      }),
    ),
    true,
    '绝对总分 < 100 优先于「明确多提不显示」',
  )
})

// =====: 多包无基准 scope 的人工兜底入口可达性（项目3414实测） =====

const unresolvedScope = (
  overrides: Record<string, unknown> = {},
): import('../src/pages/RequirementReview/scoringScopeReportTransforms.ts').ScopedReport => ({
  scopeId: 'scheme_1·标段三',
  scheme: 'scheme_1',
  lot: '标段三',
  status: 'review_required',
  basis: 'l4_none',
  expectedTotal: null,
  extractedTotal: 55,
  totalGap: null,
  applicability: 'unresolved',
  basisLowConfidence: true,
  diagnostics: [],
  unlabeledSum: 55,
  insufficiencyCount: 0,
  multiSchemeSignal: '',
  ...overrides,
})

test(' 多包: 无基准(L4) scope 绝对提取 55<100 时必须显示补录入口', () => {
  assert.equal(
    shouldShowAddScoringItem(
      base({
        globalComparisonAllowed: false,
        scopeReports: [unresolvedScope(), unresolvedScope({
          scopeId: 'scheme_1·标段四', lot: '标段四', extractedTotal: 100, unlabeledSum: 0,
        })],
      }),
    ),
    true,
    '标段三 55 分且无基准时人工兜底入口不得被 unresolved 语义压掉',
  )
})

test(' 多包: 无基准但各 scope 均已达 100 时不显示（避免诱导多提）', () => {
  assert.equal(
    shouldShowAddScoringItem(
      base({
        globalComparisonAllowed: false,
        scopeReports: [
          unresolvedScope({ extractedTotal: 100, unlabeledSum: 0 }),
          unresolvedScope({
            scopeId: 'scheme_1·标段四', lot: '标段四', extractedTotal: 100, unlabeledSum: 0,
          }),
        ],
      }),
    ),
    false,
  )
})
