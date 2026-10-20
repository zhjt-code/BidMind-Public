/**
 * 账本明细文案防御测试。
 *
 * 真实症状:
 * - 项目2476 同屏出现两句以"检测到多套评分方案"开头的话，后一句还把后端内部
 * 判据串 `推导组和中2个≈100且Σ=200>101, 疑似多方案账本并存` 直接抛给用户；
 * - 项目2475 显示"已提取 100 分（未在招标文件中找到总分要求）"紧接"另有 100 分
 * 未能归入任何评分卷"，两个 100 实为同一批分值，用户读成 200。
 *
 * 锁定不变量:
 * 1. 标题已以多方案为主因时，明细行让位（去重铁律，与 insufficiency 同模式）
 * 2. 任何情况下都不回显后端原始判据串
 * 3. 未归卷提示必须表达"子集"而非"增量"，且全量未归组时不并列两个相同数字
 * 4. 折算制总分行必须同时说明原始轴与折算轴，不得并列两个不同轴的数字说"一致"
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import {
  mergeUnlabeledIntoGroupGaps,
  multiSchemeDetail,
  unlabeledScoreNotice,
} from '../src/pages/RequirementReview/scoringLedgerCopy.ts'
import {
  ledgerTotalsLine,
  type ScoringLedgerGroupRow,
  type ScoringLedgerReport,
} from '../src/pages/requirementReviewTransforms.ts'

const RAW_SIGNAL = '推导组和中2个≈100且Σ=200>101, 疑似多方案账本并存'

function ledger(overrides: Partial<ScoringLedgerReport> = {}): ScoringLedgerReport {
  return {
    status: 'review_required',
    basisLevel: 'l1_explicit_group',
    basisLowConfidence: false,
    expectedTotal: 100,
    extractedTotal: 100,
    extractedTotalFolded: null,
    totalGap: 0,
    groupRows: [],
    unlabeledSum: 0,
    multiSchemeSignal: '',
    insufficiencyCount: 0,
    inferredGroupLabel: null,
    inferredGroupSum: 0,
    inferredGroupCount: 0,
    diagnostics: [],
    ...overrides,
  }
}

// ===== 1/2. 多方案明细去重与去术语 =====

test('标题已是多方案主因时，明细行让位不重复', () => {
  assert.equal(multiSchemeDetail(RAW_SIGNAL, 'multi_scheme'), null)
})

test('标题为其他主因时保留明细，但不得回显后端判据串', () => {
  const text = multiSchemeDetail(RAW_SIGNAL, 'total_gap')

  assert.ok(text)
  assert.doesNotMatch(text!, /Σ|≈|推导组/, '内部判据量不得面向用户')
  assert.match(text!, /多套评分方案/)
})

test('无信号时不产出明细', () => {
  assert.equal(multiSchemeDetail('', 'total_gap'), null)
  assert.equal(multiSchemeDetail('   ', null), null)
})

// ===== 3. 未归卷提示表达为展示构成 =====

test('部分未归组时展示为“其它提取”', () => {
  const text = unlabeledScoreNotice(30, 100)

  assert.equal(text, '其它提取 30 分')
  assert.doesNotMatch(text!, /另有/)
  assert.doesNotMatch(text!, /未能归入|尚未识别/)
})

test('全量未归组时展示为“已提分数项”', () => {
  const text = unlabeledScoreNotice(100, 100)

  assert.equal(text, '已提分数项 100 分')
})

test('无未归组分值时不产出提示', () => {
  assert.equal(unlabeledScoreNotice(0, 100), null)
})

// ===== 4. 折算制总分行两轴分述 =====

test('折算制总分行同时给出原始满分合计与折算后总分', () => {
  const line = ledgerTotalsLine(
    ledger({ extractedTotal: 300, extractedTotalFolded: 100, expectedTotal: 100, totalGap: 0 }),
  )

  assert.match(line, /各卷原始满分合计 300 分/)
  assert.match(line, /按权重折算后 100 分/)
  assert.doesNotMatch(line, /已提取 300 分/, '不得把原始轴数字直接与折算基准并列')
})

test('非折算制总分行维持原表述', () => {
  const line = ledgerTotalsLine(ledger({ extractedTotal: 100, expectedTotal: 100, totalGap: 0 }))

  assert.match(line, /已提取 100 分/)
  assert.match(line, /两者一致/)
})

// ===== 5. "组缺口=未归卷"合并叙事（, 项目2481 形态）=====

function gapRow(label: string, declared: number, extractedSum: number): ScoringLedgerGroupRow {
  return { label, declared, extractedSum, gap: extractedSum - declared }
}

test('唯一负缺口与未归卷分值一致时合并为单一叙事并点名该卷', () => {
  const merge = mergeUnlabeledIntoGroupGaps(
    [
      gapRow('资信业绩部分', 3, 3),
      gapRow('项目管理大纲部分', 30, 0),
      gapRow('投标报价', 60, 60),
      gapRow('其他评分因素', 7, 7),
    ],
    30,
  )

  assert.ok(merge)
  assert.equal(merge!.headline, '评分项分值构成需核实')
  assert.match(merge!.detail, /其它提取 30 分/)
  assert.match(merge!.detail, /项目管理大纲部分/)
  assert.doesNotMatch(merge!.detail, /不一致/, '合并叙事不得再断言分值不一致')
})

test('多个负缺口合计等于未归卷分值时逐卷列出', () => {
  const merge = mergeUnlabeledIntoGroupGaps(
    [gapRow('商务部分', 20, 0), gapRow('技术部分', 80, 0)],
    100,
  )

  assert.ok(merge)
  assert.match(merge!.detail, /商务部分 20 分/)
  assert.match(merge!.detail, /技术部分 80 分/)
})

test('存在正缺口（多提）时不合并——多提是另一类问题', () => {
  const merge = mergeUnlabeledIntoGroupGaps(
    [gapRow('技术部分', 53, 85), gapRow('商务部分', 32, 0)],
    0,
  )

  assert.equal(merge, null)
})

test('负缺口合计与未归卷分值不一致时不合并，保留双行各自陈述', () => {
  assert.equal(
    mergeUnlabeledIntoGroupGaps([gapRow('项目管理大纲部分', 30, 0)], 12),
    null,
  )
})

test('无未归卷分值或无缺口行时不合并', () => {
  assert.equal(mergeUnlabeledIntoGroupGaps([gapRow('商务部分', 20, 0)], 0), null)
  assert.equal(mergeUnlabeledIntoGroupGaps([gapRow('商务部分', 20, 20)], 30), null)
})
