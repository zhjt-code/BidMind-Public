/**
 * 总分基准置信度提醒防御测试。
 *
 * 防御目标（映射真实风险）：
 * - 原单一文案称「未能从招标文件直接读到总分要求」是事实错误：doc1933(项目2447)
 * 的招标文件 page2 明写 `2.2.1 分值构成 (总分100分)`，解析期已捕获，
 * 只是账本输入当时未覆盖该行。断言「文件没写」会让用户去找一个本就存在的东西。
 * - 原文案用「推算」描述各卷分值的确定性求和(26+30+9+30+5=100)，平白削弱严谨性。
 * - 原单一文案覆盖四类性质不同的基准来源，无法指导下一步动作，须分述。
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import { basisConfidenceNotice } from '../src/pages/RequirementReview/scoringBasisNotice.ts'

function ledger(overrides: Record<string, unknown> = {}) {
  return {
    status: 'review_required',
    basisLevel: 'l1_explicit_group',
    basisLowConfidence: true,
    expectedTotal: 100,
    extractedTotal: 100,
    totalGap: 0,
    groupRows: [],
    unlabeledSum: 0,
    multiSchemeSignal: '',
    duplicateExclusions: [],
    insufficiencyCount: 0,
    diagnostics: [],
    ...overrides,
  } as never
}

// ===== 低置信来源用户口径 =====

test('L1 各卷分值齐全但无独立总分句 —— 统一为短核实提示', () => {
  const notice = basisConfidenceNotice(ledger())

  assert.equal(notice, '注意：请仔细对照招标文件核实')
  assert.doesNotMatch(
    notice!,
    /未能从招标文件直接读到总分要求|未在招标文件中找到总分/,
    '断言文件缺失是事实错误（doc1933 实际写了 分值构成 (总分100分)）',
  )
})

test('L2 由提取结果归集 —— 统一为短核实提示', () => {
  const notice = basisConfidenceNotice(ledger({ basisLevel: 'l2_derived_group' }))

  assert.equal(notice, '注意：请仔细对照招标文件核实')
})

test('L3 多个互斥总分候选 —— 须要求用户裁定适用哪一个', () => {
  const notice = basisConfidenceNotice(
    ledger({ basisLevel: 'l3_doc_total', expectedTotal: 100 }),
  )

  assert.ok(notice)
  assert.match(notice!, /出现多个总分数值/)
  assert.match(notice!, /已暂按 100 分核对/)
  assert.match(notice!, /请确认适用哪一个/)
})

// ===== 全体文案禁用「推算」 =====

test('三类提醒一律不得使用「推算」', () => {
  const notices = [
    basisConfidenceNotice(ledger()),
    basisConfidenceNotice(ledger({ basisLevel: 'l2_derived_group' })),
    basisConfidenceNotice(ledger({ basisLevel: 'l3_doc_total' })),
  ]

  for (const notice of notices) {
    assert.ok(notice)
    assert.doesNotMatch(notice!, /推算/, '确定性求和不得描述为"推算"')
  }
  assert.equal(notices[0], notices[1], 'L1/L2 收敛为统一短核实提示')
  assert.notEqual(notices[2], notices[0], 'L3 多总分候选仍须保留独立裁定提示')
})

// ===== 不适用路径 =====

test('高置信基准（已由原文交叉核对）不给提醒', () => {
  assert.equal(
    basisConfidenceNotice(ledger({ basisLowConfidence: false })),
    null,
    'L1 高置信意味着组声明和与独立总分声明双源一致，无需提醒',
  )
  assert.equal(
    basisConfidenceNotice(
      ledger({ basisLevel: 'l3_doc_total', basisLowConfidence: false }),
    ),
    null,
  )
})

test('L4 无基准与缺报告时不给提醒（走警告态标题）', () => {
  assert.equal(
    basisConfidenceNotice(ledger({ basisLevel: 'l4_none', expectedTotal: null })),
    null,
  )
  assert.equal(basisConfidenceNotice(null), null)
  assert.equal(basisConfidenceNotice(undefined), null)
})

test('L2 即便标记为高置信也仍给提醒（其低置信是设计恒定属性）', () => {
  const notice = basisConfidenceNotice(
    ledger({ basisLevel: 'l2_derived_group', basisLowConfidence: false }),
  )

  assert.ok(notice, 'L2 基准恒由提取结果归集，交叉核对缺失与置信度标记无关')
  assert.equal(notice, '注意：请仔细对照招标文件核实')
})
