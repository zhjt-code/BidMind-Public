/**
 * 叶节点完整性证据（leaf_integrity）前端防御测试。
 *
 * 覆盖：
 * ① transformScoringLeafIntegrity：合法 single_100/per_scope_100 原样解析
 * ② 缺失/非对象/非法 basis/非有限或负数 counts/total → 默认未 verified
 * ③ verified=true 但 counts 不满足条件 → 降级 false
 * ④ transformScoringLedgerReport 带 leaf_integrity → 透传 leafIntegrity 字段
 * ⑤ isLedgerSelfConsistentWithSoftBasis：leafIntegrity.verified=true → 返回 true
 * ⑥ basisConfidenceNotice：leaf verified → 返回固定完整性核验文案
 * ⑦ basisConfidenceNotice：无 leaf 证据 → 旧 soft-basis 行为不变
 * ⑧ 源码契约：Banner 未修改（无 leafIntegrity 直接消费）；现有 notice 调用链可见
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  transformScoringLeafIntegrity,
  type ScoringLeafIntegrityEvidence,
} from '../src/pages/RequirementReview/scoringLeafIntegrity.ts'
import {
  transformScoringLedgerReport,
  transformScoringTotalsReport,
  isLedgerSelfConsistentWithSoftBasis,
} from '../src/pages/requirementReviewTransforms.ts'
import { basisConfidenceNotice } from '../src/pages/RequirementReview/scoringBasisNotice.ts'

const bannerSource = readFileSync(
  new URL('../src/pages/RequirementReview/components/ScoringTotalsBanner.tsx', import.meta.url),
  'utf8',
)

// ===== ① transformScoringLeafIntegrity 合法路径 =====

test('transformScoringLeafIntegrity 合法 single_100：verified=true 且 counts 全满足', () => {
  const raw = {
    verified: true,
    basis: 'single_100',
    inventory_count: 20,
    matched_count: 20,
    total: 100,
  }
  const result: ScoringLeafIntegrityEvidence = transformScoringLeafIntegrity(raw)
  assert.equal(result.verified, true)
  assert.equal(result.basis, 'single_100')
  assert.equal(result.inventoryCount, 20)
  assert.equal(result.matchedCount, 20)
  assert.equal(result.total, 100)
})

test('transformScoringLeafIntegrity 合法 per_scope_100：verified=true', () => {
  const raw = {
    verified: true,
    basis: 'per_scope_100',
    inventory_count: 5,
    matched_count: 5,
    total: 300,
  }
  const result = transformScoringLeafIntegrity(raw)
  assert.equal(result.verified, true)
  assert.equal(result.basis, 'per_scope_100')
})

test('transformScoringLeafIntegrity 合法 basis="" verified=false：返回 verified=false', () => {
  const raw = {
    verified: false,
    basis: '',
    inventory_count: 0,
    matched_count: 0,
    total: 0,
  }
  const result = transformScoringLeafIntegrity(raw)
  assert.equal(result.verified, false)
  assert.equal(result.basis, '')
})

// ===== ② 缺失/非法输入安全回退 =====

test('transformScoringLeafIntegrity null/undefined/非对象 → 默认未 verified', () => {
  const DEFAULT = { verified: false, basis: '', inventoryCount: 0, matchedCount: 0, total: 0 }
  assert.deepEqual(transformScoringLeafIntegrity(null), DEFAULT)
  assert.deepEqual(transformScoringLeafIntegrity(undefined), DEFAULT)
  assert.deepEqual(transformScoringLeafIntegrity('single_100'), DEFAULT)
  assert.deepEqual(transformScoringLeafIntegrity(42), DEFAULT)
  assert.deepEqual(transformScoringLeafIntegrity([]), DEFAULT)
})

test('transformScoringLeafIntegrity 非法 basis 字符串 → 默认未 verified', () => {
  const DEFAULT = { verified: false, basis: '', inventoryCount: 0, matchedCount: 0, total: 0 }
  assert.deepEqual(
    transformScoringLeafIntegrity({
      verified: true,
      basis: 'all_100',
      inventory_count: 10,
      matched_count: 10,
      total: 100,
    }),
    DEFAULT,
    '非法 basis 必须整体回退为默认',
  )
  assert.deepEqual(
    transformScoringLeafIntegrity({
      verified: true,
      basis: 'SINGLE_100',
      inventory_count: 10,
      matched_count: 10,
      total: 100,
    }),
    DEFAULT,
    '大小写不匹配的 basis 必须回退',
  )
})

test('transformScoringLeafIntegrity 非有限数 counts/total → 默认未 verified', () => {
  const DEFAULT = { verified: false, basis: '', inventoryCount: 0, matchedCount: 0, total: 0 }
  assert.deepEqual(
    transformScoringLeafIntegrity({
      verified: true,
      basis: 'single_100',
      inventory_count: Infinity,
      matched_count: 10,
      total: 100,
    }),
    DEFAULT,
    'Infinity inventory_count 必须回退',
  )
  assert.deepEqual(
    transformScoringLeafIntegrity({
      verified: true,
      basis: 'single_100',
      inventory_count: 10,
      matched_count: NaN,
      total: 100,
    }),
    DEFAULT,
    'NaN matched_count 必须回退',
  )
  assert.deepEqual(
    transformScoringLeafIntegrity({
      verified: true,
      basis: 'single_100',
      inventory_count: 10,
      matched_count: 10,
      total: '100',
    }),
    DEFAULT,
    '字符串 total 必须回退',
  )
})

test('transformScoringLeafIntegrity 负数 counts → 默认未 verified', () => {
  const DEFAULT = { verified: false, basis: '', inventoryCount: 0, matchedCount: 0, total: 0 }
  assert.deepEqual(
    transformScoringLeafIntegrity({
      verified: true,
      basis: 'single_100',
      inventory_count: -1,
      matched_count: -1,
      total: 100,
    }),
    DEFAULT,
    '负数 counts 必须回退',
  )
})

// [ CHANGES_REQUIRED] inventory_count/matched_count 必须为整数，小数必须回退
test('transformScoringLeafIntegrity 小数 inventory_count → 默认未 verified（counts 必须为整数）', () => {
  const result = transformScoringLeafIntegrity({
    verified: true,
    basis: 'single_100',
    inventory_count: 10.5,
    matched_count: 10.5,
    total: 100,
  })
  assert.equal(result.verified, false, 'inventory_count 小数不得放行，verified 必须回退')
  assert.equal(result.inventoryCount, 0, '非整数 inventory_count 回退后 inventoryCount 为 0')
})

test('transformScoringLeafIntegrity 小数 matched_count → 默认未 verified（counts 必须为整数）', () => {
  const result = transformScoringLeafIntegrity({
    verified: true,
    basis: 'per_scope_100',
    inventory_count: 5,
    matched_count: 4.9,
    total: 100,
  })
  assert.equal(result.verified, false, 'matched_count 小数不得放行，verified 必须回退')
})

// total 允许有限非负小数：回归守卫（整数检查禁止误伤 total）
test('transformScoringLeafIntegrity total 有限非负小数 → verified 仍通过（total 允许小数）', () => {
  const result = transformScoringLeafIntegrity({
    verified: true,
    basis: 'single_100',
    inventory_count: 10,
    matched_count: 10,
    total: 99.5,
  })
  assert.equal(result.verified, true, 'total=99.5 是合法值，不得因整数检查被拒绝')
  assert.equal(result.total, 99.5)
})

// verified=false 时有效 counts 保留用于诊断：回归守卫
test('transformScoringLeafIntegrity verified 降级时有效 counts 仍保留（诊断值不归零）', () => {
  // mismatched counts → verified=false，但 inventoryCount/matchedCount 本身是有效整数，应保留
  const result = transformScoringLeafIntegrity({
    verified: true,
    basis: 'single_100',
    inventory_count: 8,
    matched_count: 6,
    total: 100,
  })
  assert.equal(result.verified, false, 'mismatched counts → verified 降级')
  assert.equal(result.inventoryCount, 8, 'inventoryCount 应保留用于诊断')
  assert.equal(result.matchedCount, 6, 'matchedCount 应保留用于诊断')
  assert.equal(result.total, 100, 'total 应保留用于诊断')
})

// ===== ③ 伪 verified 降级 =====

test('transformScoringLeafIntegrity verified=true 但 inventory_count=0 → 降级 verified=false', () => {
  const result = transformScoringLeafIntegrity({
    verified: true,
    basis: 'single_100',
    inventory_count: 0,
    matched_count: 0,
    total: 100,
  })
  assert.equal(result.verified, false, 'inventory_count=0 时 verified 必须降级')
})

test('transformScoringLeafIntegrity verified=true 但 matched_count != inventory_count → 降级', () => {
  const result = transformScoringLeafIntegrity({
    verified: true,
    basis: 'per_scope_100',
    inventory_count: 10,
    matched_count: 9,
    total: 100,
  })
  assert.equal(result.verified, false, 'matched_count 不等于 inventory_count 必须降级')
})

test('transformScoringLeafIntegrity verified=true 但 total=0 → 降级', () => {
  const result = transformScoringLeafIntegrity({
    verified: true,
    basis: 'single_100',
    inventory_count: 5,
    matched_count: 5,
    total: 0,
  })
  assert.equal(result.verified, false, 'total=0 时 verified 必须降级')
})

test('transformScoringLeafIntegrity verified=true 但 basis="" → 降级', () => {
  const result = transformScoringLeafIntegrity({
    verified: true,
    basis: '',
    inventory_count: 5,
    matched_count: 5,
    total: 100,
  })
  assert.equal(result.verified, false, 'basis="" 时 verified 必须降级')
})

// ===== ④ transformScoringLedgerReport 带 leaf_integrity =====

const BASE_LEDGER_PAYLOAD = {
  status: 'reconciled',
  basis_level: 'l1_explicit_group',
  basis_low_confidence: false,
  expected_total: 100,
  extracted_total: 100,
  total_gap: 0,
  group_rows: [],
  unlabeled_sum: 0,
  multi_scheme_signal: '',
  insufficiency_count: 0,
  diagnostics: [],
}

test('transformScoringLedgerReport 带合法 leaf_integrity → leafIntegrity.verified=true', () => {
  const report = transformScoringLedgerReport({
    ...BASE_LEDGER_PAYLOAD,
    leaf_integrity: {
      verified: true,
      basis: 'single_100',
      inventory_count: 10,
      matched_count: 10,
      total: 100,
    },
  })
  assert.notEqual(report, null)
  assert.equal(report!.leafIntegrity.verified, true)
  assert.equal(report!.leafIntegrity.basis, 'single_100')
  assert.equal(report!.leafIntegrity.inventoryCount, 10)
})

test('transformScoringLedgerReport 缺失 leaf_integrity → leafIntegrity.verified=false（默认）', () => {
  const report = transformScoringLedgerReport(BASE_LEDGER_PAYLOAD)
  assert.notEqual(report, null)
  assert.equal(report!.leafIntegrity.verified, false)
  assert.equal(report!.leafIntegrity.basis, '')
})

// ===== ⑤ isLedgerSelfConsistentWithSoftBasis：leaf verified → true =====

function makeLedger(overrides: Record<string, unknown> = {}) {
  return transformScoringLedgerReport({
    ...BASE_LEDGER_PAYLOAD,
    ...overrides,
  })!
}

function makeTotalsReport(overrides: Record<string, unknown> = {}) {
  return transformScoringTotalsReport({
    needs_review: false,
    item_count: 15,
    missing_score_count: 0,
    missing_rule_count: 0,
    ...overrides,
  })!
}

test('isLedgerSelfConsistentWithSoftBasis：leafIntegrity.verified=true 且无矛盾信号 → 返回 true', () => {
  // leaf verified + multiSchemeSignal='' + insufficiencyCount=0（来自 BASE_LEDGER_PAYLOAD）
  const ledger = makeLedger({
    status: 'review_required',
    basis_level: 'l4_none',
    expected_total: null,
    total_gap: null,
    leaf_integrity: {
      verified: true,
      basis: 'single_100',
      inventory_count: 15,
      matched_count: 15,
      total: 100,
    },
  })
  const report = makeTotalsReport()
  assert.equal(
    isLedgerSelfConsistentWithSoftBasis(ledger, report),
    true,
    'leaf verified 且无矛盾信号时必须返回 true',
  )
  assert.equal(basisConfidenceNotice(ledger), LEAF_VERIFIED_NOTICE)
})

test('isLedgerSelfConsistentWithSoftBasis：leaf verified 但 missingScoreCount>0 → false', () => {
  const ledger = makeLedger({
    status: 'review_required',
    basis_level: 'l4_none',
    expected_total: null,
    total_gap: null,
    leaf_integrity: {
      verified: true,
      basis: 'single_100',
      inventory_count: 15,
      matched_count: 15,
      total: 100,
    },
  })

  assert.equal(
    isLedgerSelfConsistentWithSoftBasis(
      ledger,
      makeTotalsReport({ needs_review: true, missing_score_count: 1 }),
    ),
    false,
  )
})

test('isLedgerSelfConsistentWithSoftBasis：leaf verified 但 missingRuleCount>0 → false', () => {
  const ledger = makeLedger({
    status: 'review_required',
    basis_level: 'l4_none',
    expected_total: null,
    total_gap: null,
    leaf_integrity: {
      verified: true,
      basis: 'single_100',
      inventory_count: 15,
      matched_count: 15,
      total: 100,
    },
  })

  assert.equal(
    isLedgerSelfConsistentWithSoftBasis(
      ledger,
      makeTotalsReport({ needs_review: true, missing_rule_count: 1 }),
    ),
    false,
  )
})

test('isLedgerSelfConsistentWithSoftBasis：leaf verified 但 structureReviewRequired → false', () => {
  const ledger = makeLedger({
    status: 'review_required',
    basis_level: 'l4_none',
    expected_total: null,
    total_gap: null,
    leaf_integrity: {
      verified: true,
      basis: 'single_100',
      inventory_count: 15,
      matched_count: 15,
      total: 100,
    },
  })

  assert.equal(
    isLedgerSelfConsistentWithSoftBasis(
      ledger,
      makeTotalsReport({ needs_review: true, structure_review_required: true }),
    ),
    false,
  )
})

test('isLedgerSelfConsistentWithSoftBasis：单包不得接受 per_scope_100 口径', () => {
  const ledger = makeLedger({
    status: 'review_required',
    basis_level: 'l4_none',
    expected_total: null,
    total_gap: null,
    global_comparison_allowed: true,
    leaf_integrity: {
      verified: true,
      basis: 'per_scope_100',
      inventory_count: 15,
      matched_count: 15,
      total: 100,
    },
  })

  assert.equal(isLedgerSelfConsistentWithSoftBasis(ledger), false)
})

test('isLedgerSelfConsistentWithSoftBasis：多包不得接受 single_100 口径', () => {
  const ledger = makeLedger({
    status: 'review_required',
    basis_level: 'l4_none',
    expected_total: null,
    total_gap: null,
    global_comparison_allowed: false,
    leaf_integrity: {
      verified: true,
      basis: 'single_100',
      inventory_count: 15,
      matched_count: 15,
      total: 100,
    },
  })

  assert.equal(isLedgerSelfConsistentWithSoftBasis(ledger), false)
})

// [ CHANGES_REQUIRED] leaf verified 短路必须同时要求 multiSchemeSignal=='' 且 insufficiencyCount==0
test('isLedgerSelfConsistentWithSoftBasis：leaf verified 但 multiSchemeSignal≠"" → false（防多方案amber被压）', () => {
  const ledger = makeLedger({
    status: 'review_required',
    basis_level: 'l4_none',
    expected_total: null,
    total_gap: null,
    multi_scheme_signal: '存在多套评分方案',
    leaf_integrity: {
      verified: true,
      basis: 'single_100',
      inventory_count: 10,
      matched_count: 10,
      total: 100,
    },
  })
  assert.equal(
    isLedgerSelfConsistentWithSoftBasis(ledger),
    false,
    'multiSchemeSignal≠"" 时 leaf verified 不得将多方案 amber 压成蓝色提醒',
  )
})

test('isLedgerSelfConsistentWithSoftBasis：leaf verified 但 insufficiencyCount>0 → false（防充分性amber被压）', () => {
  const ledger = makeLedger({
    status: 'review_required',
    basis_level: 'l4_none',
    expected_total: null,
    total_gap: null,
    insufficiency_count: 3,
    leaf_integrity: {
      verified: true,
      basis: 'per_scope_100',
      inventory_count: 7,
      matched_count: 7,
      total: 100,
    },
  })
  assert.equal(
    isLedgerSelfConsistentWithSoftBasis(ledger),
    false,
    'insufficiencyCount>0 时 leaf verified 不得将充分性 amber 压成蓝色提醒',
  )
})

test('isLedgerSelfConsistentWithSoftBasis：无 leaf 证据时旧逻辑不变（low_confidence + gap≈0 → true）', () => {
  const ledger = makeLedger({
    status: 'review_required',
    basis_level: 'l1_explicit_group',
    basis_low_confidence: true,
    expected_total: 100,
    total_gap: 0,
  })
  assert.equal(
    isLedgerSelfConsistentWithSoftBasis(ledger),
    true,
    '旧 soft-basis 路径（low_confidence + gap≈0）必须仍然返回 true',
  )
})

test('isLedgerSelfConsistentWithSoftBasis：无 leaf 证据且有真实 gap → false（旧逻辑守卫）', () => {
  const ledger = makeLedger({
    status: 'review_required',
    basis_level: 'l1_explicit_group',
    basis_low_confidence: true,
    expected_total: 100,
    total_gap: -4,
  })
  assert.equal(
    isLedgerSelfConsistentWithSoftBasis(ledger),
    false,
    '真实 gap 存在时旧逻辑不得被 leaf 覆盖',
  )
})

// ===== ⑥ basisConfidenceNotice：leaf verified → 固定完整性核验文案 =====

const LEAF_VERIFIED_NOTICE =
  '评分细项已通过逐项完整性核验；归卷或总分基准尚需对照原文确认，不影响细项完整性结论'

test('basisConfidenceNotice：leaf verified → 精确固定文案（优先于其他判断）', () => {
  const ledger = makeLedger({
    status: 'review_required',
    basis_level: 'l2_derived_group',
    basis_low_confidence: true,
    expected_total: 100,
    total_gap: 0,
    leaf_integrity: {
      verified: true,
      basis: 'per_scope_100',
      inventory_count: 8,
      matched_count: 8,
      total: 100,
    },
  })
  const notice = basisConfidenceNotice(ledger)
  assert.equal(notice, LEAF_VERIFIED_NOTICE, '完整性核验文案必须精确匹配')
})

test('basisConfidenceNotice：leaf verified 且 expectedTotal=null → 仍返回完整性核验文案（优先）', () => {
  const ledger = makeLedger({
    status: 'review_required',
    basis_level: 'l4_none',
    basis_low_confidence: false,
    expected_total: null,
    total_gap: null,
    leaf_integrity: {
      verified: true,
      basis: 'single_100',
      inventory_count: 3,
      matched_count: 3,
      total: 100,
    },
  })
  const notice = basisConfidenceNotice(ledger)
  assert.equal(
    notice,
    LEAF_VERIFIED_NOTICE,
    'leaf verified 必须优先于 expectedTotal==null 的 null 短路',
  )
})

// ===== ⑦ basisConfidenceNotice：无 leaf 证据时旧 soft-basis 行为不变 =====

test('basisConfidenceNotice：无 leaf 证据 L2 → 旧提示文案（推算通道保留）', () => {
  const ledger = makeLedger({
    status: 'review_required',
    basis_level: 'l2_derived_group',
    basis_low_confidence: true,
    expected_total: 100,
    total_gap: 0,
    // 无 leaf_integrity
  })
  const notice = basisConfidenceNotice(ledger)
  assert.equal(notice, '注意：请仔细对照招标文件核实', 'L2 无 leaf 时旧文案必须不变')
  assert.notEqual(notice, LEAF_VERIFIED_NOTICE)
})

test('basisConfidenceNotice：无 leaf 证据且高置信 → null（旧逻辑不变）', () => {
  const ledger = makeLedger({
    basis_level: 'l1_explicit_group',
    basis_low_confidence: false,
    expected_total: 100,
    total_gap: 0,
  })
  assert.equal(basisConfidenceNotice(ledger), null)
})

// ===== ⑧ 源码契约：Banner 未改；现有 notice 调用链可见 =====

test('源码契约：ScoringTotalsBanner.tsx 未直接消费 leafIntegrity（完整性通过现有蓝色 notice 通道）', () => {
  assert.doesNotMatch(
    bannerSource,
    /leafIntegrity/,
    'Banner 不应直接读取 leafIntegrity；该字段通过 basisConfidenceNotice → notices 通道渲染',
  )
})

test('源码契约：Banner 包含 basisConfidenceNotice 调用链（蓝色 notice 通道存在）', () => {
  assert.match(
    bannerSource,
    /basisConfidenceNotice\(/,
    'Banner 必须通过 basisConfidenceNotice 消费 leaf verified 产生的 notice',
  )
})

test('源码契约：Banner 判定 leaf 提醒时必须传入 totals report', () => {
  assert.match(
    bannerSource,
    /isLedgerSelfConsistentWithSoftBasis\(ledgerReport \?\? null,\s*report\)/,
  )
})
