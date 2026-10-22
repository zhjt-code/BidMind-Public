/**
 * 防御测试：采购包/标段维度评分报告（Task7）。
 *
 * 覆盖：
 * ① transformScopedReportItem 解析合法 scope payload；
 * 非法 status/applicability/scope_id → null（丢弃）
 * ② transformScopedReportList 过滤非法项，空/null 输入 → []
 * ③ buildScopeReportView 聚合：anyApplicableGap / anyUnresolved / allApplicableReconciled
 * ④ formatScopeGapLine 文案：采购包X：要求N分，已提取M分（少/多K分）；
 * gap≈0+insufficiency/unlabeled 退化文案
 * ⑤ MULTI_SCHEME_UNRESOLVED_NOTICE 2502 精确文案
 * ⑥ ScoringTotalsBanner: globalComparisonAllowed 变量存在并用于门控
 * ⑦ ScoringTotalsBanner: 引入并渲染 ScopedScoringReportPanel
 * ⑧ shouldShowAddScoringItem: globalComparisonAllowed=false 时仅 applicable 负 gap 显示，
 * all-pass / unresolved / 多提均不显示
 * ⑨ isOk 包含 scope 级别状态感知（scopeAmber / effectiveLedgerNeedsReview）
 * ⑩ 2499场景：两scope均100/100，不显示新增评分项
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  transformScopedReportItem,
  transformScopedReportList,
  buildScopeReportView,
  formatScopeGapLine,
  isMisclassifiedApplicable,
  MULTI_SCHEME_UNRESOLVED_NOTICE,
  SCOPE_SCORE_TOLERANCE,
  type ScopedReport,
} from '../src/pages/RequirementReview/scoringScopeReportTransforms.ts'
import {
  shouldShowAddScoringItem,
  type ScoringAddItemVisibilityInput,
} from '../src/pages/RequirementReview/scoringAddItemVisibility.ts'

const bannerSource = readFileSync(
  new URL('../src/pages/RequirementReview/components/ScoringTotalsBanner.tsx', import.meta.url),
  'utf8',
)

// ===== 测试 fixture =====

const validScopePayload = {
  scope_id: 'schemeA_lot1',
  scheme: 'schemeA',
  lot: '采购包1',
  status: 'reconciled',
  basis: 'l1_explicit_group',
  expected_total: 100,
  extracted_total: 100,
  total_gap: 0,
  expected_by_volume: [
    { volume: 'technical', expected: 60 },
    { volume: 'price', expected: 40 },
  ],
  applicability: 'applicable',
  basis_low_confidence: false,
  diagnostics: [],
  unlabeled_sum: 0,
  insufficiency_count: 0,
  multi_scheme_signal: '',
}

// 方便构造 ScopedReport 对象用于 shouldShowAddScoringItem 测试
function makeScope(overrides: Partial<ScopedReport> = {}): ScopedReport {
  return {
    scopeId: 's1',
    scheme: 'schemeA',
    lot: '采购包1',
    status: 'reconciled',
    basis: 'l1',
    expectedTotal: 100,
    extractedTotal: 100,
    totalGap: 0,
    applicability: 'applicable',
    basisLowConfidence: false,
    diagnostics: [],
    unlabeledSum: 0,
    insufficiencyCount: 0,
    multiSchemeSignal: '',
    ...overrides,
  }
}

// ===== ① transformScopedReportItem =====

test('transformScopedReportItem 解析合法 reconciled/applicable scope', () => {
  const scope = transformScopedReportItem(validScopePayload)
  assert.notEqual(scope, null, '合法 payload 不应返回 null')
  assert.equal(scope!.scopeId, 'schemeA_lot1')
  assert.equal(scope!.scheme, 'schemeA')
  assert.equal(scope!.lot, '采购包1')
  assert.equal(scope!.status, 'reconciled')
  assert.equal(scope!.applicability, 'applicable')
  assert.equal(scope!.expectedTotal, 100)
  assert.equal(scope!.extractedTotal, 100)
  assert.equal(scope!.totalGap, 0)
  assert.equal(scope!.basisLowConfidence, false)
  assert.equal(scope!.insufficiencyCount, 0)
  assert.equal(scope!.unlabeledSum, 0)
  assert.equal(scope!.multiSchemeSignal, '')
})

test('transformScopedReportItem 解析 review_required/unresolved scope', () => {
  const scope = transformScopedReportItem({
    ...validScopePayload,
    scope_id: 'schemeB_lot2',
    lot: '采购包2',
    status: 'review_required',
    applicability: 'unresolved',
    expected_total: 100,
    extracted_total: 90,
    total_gap: -10,
    basis_low_confidence: true,
    multi_scheme_signal: 'two_schemes_detected',
  })
  assert.notEqual(scope, null)
  assert.equal(scope!.status, 'review_required')
  assert.equal(scope!.applicability, 'unresolved')
  assert.equal(scope!.totalGap, -10)
  assert.equal(scope!.basisLowConfidence, true)
  assert.equal(scope!.multiSchemeSignal, 'two_schemes_detected')
})

test('transformScopedReportItem: 非法 status 返回 null', () => {
  assert.equal(
    transformScopedReportItem({ ...validScopePayload, status: 'bogus_status' }),
    null,
    '未知 status 必须返回 null',
  )
  assert.equal(
    transformScopedReportItem({ ...validScopePayload, status: undefined }),
    null,
    'status 缺失必须返回 null',
  )
})

test('transformScopedReportItem: 非法 applicability 返回 null', () => {
  assert.equal(
    transformScopedReportItem({ ...validScopePayload, applicability: 'unknown_applicability' }),
    null,
    '未知 applicability 必须返回 null',
  )
  assert.equal(
    transformScopedReportItem({ ...validScopePayload, applicability: '' }),
    null,
    '空 applicability 必须返回 null',
  )
})

test('transformScopedReportItem: null/非对象 返回 null', () => {
  assert.equal(transformScopedReportItem(null), null)
  assert.equal(transformScopedReportItem(undefined), null)
  assert.equal(transformScopedReportItem('reconciled'), null, '字符串不是对象')
  assert.equal(transformScopedReportItem(42), null, '数字不是对象')
})

test('transformScopedReportItem: scope_id 非 string/空字符串返回 null（key 稳定性保障）', () => {
  assert.equal(
    transformScopedReportItem({ ...validScopePayload, scope_id: '' }),
    null,
    '空 scope_id 返回 null',
  )
  assert.equal(
    transformScopedReportItem({ ...validScopePayload, scope_id: '   ' }),
    null,
    '纯空白 scope_id 返回 null',
  )
  assert.equal(
    transformScopedReportItem({ ...validScopePayload, scope_id: 123 }),
    null,
    '数字 scope_id 返回 null',
  )
})

test('transformScopedReportItem: expected_total/total_gap 为 null 时保持 null', () => {
  const scope = transformScopedReportItem({
    ...validScopePayload,
    expected_total: null,
    total_gap: null,
  })
  assert.notEqual(scope, null)
  assert.equal(scope!.expectedTotal, null, 'expected_total=null 必须保持 null')
  assert.equal(scope!.totalGap, null, 'total_gap=null 必须保持 null')
})

// ===== ② transformScopedReportList =====

test('transformScopedReportList 空数组 → []', () => {
  assert.deepEqual(transformScopedReportList([]), [])
})

test('transformScopedReportList null/undefined/非数组 → []', () => {
  assert.deepEqual(transformScopedReportList(null), [])
  assert.deepEqual(transformScopedReportList(undefined), [])
  assert.deepEqual(transformScopedReportList('bad_string'), [])
  assert.deepEqual(transformScopedReportList(42), [])
})

test('transformScopedReportList 过滤非法项，合法项顺序保留', () => {
  const result = transformScopedReportList([
    validScopePayload,
    { ...validScopePayload, scope_id: 'scope_bad', status: 'not_a_status' },
    null,
    'string_item',
    { ...validScopePayload, scope_id: 'scope_C', lot: '采购包2' },
  ])
  assert.equal(result.length, 2, '非法项必须被过滤')
  assert.equal(result[0].scopeId, 'schemeA_lot1', '合法项顺序必须保留')
  assert.equal(result[1].lot, '采购包2')
})

// ===== ③ buildScopeReportView =====

test('buildScopeReportView: 全 applicable+reconciled+gap=0 → allApplicableReconciled=true, anyApplicableGap=false', () => {
  const scopes = [makeScope()]
  const view = buildScopeReportView(scopes)
  assert.equal(view.allApplicableReconciled, true)
  assert.equal(view.anyApplicableGap, false)
  assert.equal(view.anyUnresolved, false)
})

test('buildScopeReportView: scope totalGap=-10 → anyApplicableGap=true, allApplicableReconciled=false', () => {
  const scopes = [
    makeScope({ status: 'review_required', totalGap: -10, extractedTotal: 90 }),
  ]
  const view = buildScopeReportView(scopes)
  assert.equal(view.anyApplicableGap, true)
  assert.equal(view.allApplicableReconciled, false)
})

test('buildScopeReportView: scope insufficiencyCount>0 → anyApplicableGap=true', () => {
  const scopes = [makeScope({ status: 'review_required', insufficiencyCount: 2, totalGap: 0 })]
  const view = buildScopeReportView(scopes)
  assert.equal(view.anyApplicableGap, true, 'insufficiency_count>0 计入 anyApplicableGap')
})

test('buildScopeReportView: scope unlabeledSum>0 → anyApplicableGap=true', () => {
  const scopes = [makeScope({ status: 'review_required', unlabeledSum: 5, totalGap: 0 })]
  const view = buildScopeReportView(scopes)
  assert.equal(view.anyApplicableGap, true, 'unlabeled_sum>0 计入 anyApplicableGap')
})

test('buildScopeReportView: scope applicability=unresolved → anyUnresolved=true, unresolved 不计入 anyApplicableGap', () => {
  const scopes = [makeScope({ applicability: 'unresolved', totalGap: -5 })]
  const view = buildScopeReportView(scopes)
  assert.equal(view.anyUnresolved, true)
  assert.equal(
    view.anyApplicableGap,
    false,
    'unresolved scope 有负 gap 也不计入 anyApplicableGap（适用性未确定不等于错误）',
  )
  assert.equal(
    view.anyMisclassifiedGap,
    true,
    ': 误降级 unresolved 且有真实 gap 计入 anyMisclassifiedGap',
  )
})

test('buildScopeReportView: 2758形态 — 误降级负缺口触发 anyMisclassifiedGap，仍 anyUnresolved', () => {
  const scopes = [
    makeScope({
      scopeId: 'scheme_A',
      scheme: 'scheme_A',
      lot: '',
      applicability: 'unresolved',
      status: 'review_required',
      expectedTotal: 100,
      extractedTotal: 60,
      totalGap: -40,
      basisLowConfidence: false,
    }),
    makeScope({
      scopeId: 'scheme_B',
      scheme: 'scheme_B',
      lot: '',
      applicability: 'unresolved',
      status: 'review_required',
      expectedTotal: 100,
      extractedTotal: 80,
      totalGap: -20,
      basisLowConfidence: false,
    }),
  ]
  const view = buildScopeReportView(scopes)
  assert.equal(view.anyMisclassifiedGap, true, '2758: 方案级负缺口应触发 anyMisclassifiedGap')
  assert.equal(view.anyApplicableGap, false, '2758: 仍非 applicable，不计入 anyApplicableGap')
  assert.equal(view.anyUnresolved, true, '2758: 适用关系仍 unresolved，蓝色提示保留')
  assert.equal(view.allApplicableReconciled, false)
})

test('buildScopeReportView: 2502形态 — 各方案闭合不触发 anyMisclassifiedGap', () => {
  const scopes = [
    makeScope({
      scopeId: 's1',
      applicability: 'unresolved',
      multiSchemeSignal: 'two_schemes',
      totalGap: 0,
      expectedTotal: 100,
      extractedTotal: 100,
    }),
    makeScope({
      scopeId: 's2',
      applicability: 'unresolved',
      multiSchemeSignal: 'two_schemes',
      totalGap: 0,
      expectedTotal: 100,
      extractedTotal: 100,
    }),
  ]
  const view = buildScopeReportView(scopes)
  assert.equal(view.anyMisclassifiedGap, false, '2502: gap≈0 不触发 amber')
  assert.equal(view.anyApplicableGap, false)
  assert.equal(view.anyUnresolved, true, '2502: 仍显示适用关系待确认蓝色提醒')
})

test('isMisclassifiedApplicable: 低置信 / 无基准 / 有不足项 → false', () => {
  assert.equal(
    isMisclassifiedApplicable(
      makeScope({
        applicability: 'unresolved',
        expectedTotal: 100,
        totalGap: -10,
        basisLowConfidence: true,
      }),
    ),
    false,
    '低置信不得视为误降级 applicable',
  )
  assert.equal(
    isMisclassifiedApplicable(
      makeScope({
        applicability: 'unresolved',
        expectedTotal: null,
        totalGap: null,
      }),
    ),
    false,
    '无基准不得视为误降级 applicable',
  )
  assert.equal(
    isMisclassifiedApplicable(
      makeScope({
        applicability: 'unresolved',
        expectedTotal: 100,
        totalGap: -10,
        insufficiencyCount: 2,
      }),
    ),
    false,
    '有不足项属质量不足 unresolved，非误降级',
  )
})
test('buildScopeReportView: 两 scope，一 applicable+reconciled，一 applicable+gap → anyApplicableGap=true', () => {
  const scopes = [
    makeScope({ scopeId: 's1' }),
    makeScope({ scopeId: 's2', status: 'review_required', totalGap: -20, extractedTotal: 80 }),
  ]
  const view = buildScopeReportView(scopes)
  assert.equal(view.anyApplicableGap, true)
  assert.equal(view.allApplicableReconciled, false)
  assert.equal(view.anyUnresolved, false)
})

test('buildScopeReportView: 2499场景，两scope均100/100 → allApplicableReconciled=true, anyApplicableGap=false', () => {
  const scopes = [
    makeScope({ scopeId: 'lot1', lot: '采购包1', status: 'reconciled', expectedTotal: 100, extractedTotal: 100, totalGap: 0 }),
    makeScope({ scopeId: 'lot2', lot: '采购包2', status: 'reconciled', expectedTotal: 100, extractedTotal: 100, totalGap: 0 }),
  ]
  const view = buildScopeReportView(scopes)
  assert.equal(view.allApplicableReconciled, true, '2499: 两scope均通过')
  assert.equal(view.anyApplicableGap, false, '2499: 无缺口')
  assert.equal(view.anyUnresolved, false, '2499: 无未确定')
})

test('buildScopeReportView: 无 applicable scope → allApplicableReconciled=false（not_applicable 不计入）', () => {
  const scopes = [makeScope({ applicability: 'not_applicable' })]
  const view = buildScopeReportView(scopes)
  assert.equal(
    view.allApplicableReconciled,
    false,
    '无任何 applicable scope 不等于"全部通过"（避免空集为真）',
  )
})

// ===== ④ formatScopeGapLine 文案 =====

test('formatScopeGapLine: 负 gap → 采购包N：要求 X 分，已提取 Y 分（少 Z 分）', () => {
  const scope = makeScope({
    lot: '采购包1',
    expectedTotal: 100,
    extractedTotal: 90,
    totalGap: -10,
  })
  assert.equal(
    formatScopeGapLine(scope),
    '采购包1：要求 100 分，已提取 90 分（少 10 分）',
  )
})

test('formatScopeGapLine: 正 gap → 采购包N：要求 X 分，已提取 Y 分（多 Z 分）', () => {
  const scope = makeScope({
    lot: '采购包2',
    expectedTotal: 100,
    extractedTotal: 110,
    totalGap: 10,
  })
  assert.equal(
    formatScopeGapLine(scope),
    '采购包2：要求 100 分，已提取 110 分（多 10 分）',
  )
})

test('formatScopeGapLine: gap≈0 + insufficiencyCount>0 → 标注评审标准缺失', () => {
  const scope = makeScope({
    lot: '采购包3',
    totalGap: 0,
    insufficiencyCount: 3,
    status: 'review_required',
  })
  const line = formatScopeGapLine(scope)
  assert.match(line, /采购包3/, '必须含采购包标签')
  assert.match(line, /评审标准/, '必须提及评审标准缺失')
})

test('formatScopeGapLine: expectedTotal=null → 报已提取量+未找到总分', () => {
  const scope = makeScope({ lot: '采购包4', expectedTotal: null, extractedTotal: 85, totalGap: null })
  const line = formatScopeGapLine(scope)
  assert.match(line, /采购包4/)
  assert.match(line, /未找到要求总分|85 分/)
})

test('formatScopeGapLine: lot 为空时回退到 scheme', () => {
  const scope = makeScope({ lot: '', scheme: '评分模板A', expectedTotal: 100, extractedTotal: 90, totalGap: -10 })
  const line = formatScopeGapLine(scope)
  assert.match(line, /评分模板A/, '当 lot 为空时应回退到 scheme 作为标签')
})

// ===== ⑤ MULTI_SCHEME_UNRESOLVED_NOTICE 精确文案（2502场景）=====

test('MULTI_SCHEME_UNRESOLVED_NOTICE 精确等于 2502 规定文案', () => {
  assert.equal(
    MULTI_SCHEME_UNRESOLVED_NOTICE,
    '已识别多套评分模板，结构已分别核对；适用关系需结合招标公告附件确认。',
    '2502 文案必须精确匹配，不得有多余空格或标点',
  )
})

test('MULTI_SCHEME_UNRESOLVED_NOTICE 不含内部术语', () => {
  assert.doesNotMatch(
    MULTI_SCHEME_UNRESOLVED_NOTICE,
    /scope|ledger|basis|scheme_signal/i,
    '用户文案不得裸出内部术语',
  )
})

// ===== ⑥ ScoringTotalsBanner: globalComparisonAllowed 门控 =====

test('ScoringTotalsBanner: globalComparisonAllowed 变量存在（multi-scope 门控）', () => {
  assert.match(
    bannerSource,
    /globalComparisonAllowed/,
    'Banner 必须存在 globalComparisonAllowed 变量以门控全局对比块',
  )
})

test('ScoringTotalsBanner: effectiveLedgerNeedsReview 由 globalComparisonAllowed 驱动', () => {
  assert.match(
    bannerSource,
    /effectiveLedgerNeedsReview/,
    '必须引入 effectiveLedgerNeedsReview 以在 multi-scope 模式下抑制全局账本 amber',
  )
  // effectiveLedgerNeedsReview 的定义须含 globalComparisonAllowed
  const defMatch = bannerSource.match(/const\s+effectiveLedgerNeedsReview\s*=\s*[^\n]+/)
  assert.ok(defMatch, 'effectiveLedgerNeedsReview 定义必须存在')
  assert.match(
    defMatch![0],
    /globalComparisonAllowed/,
    'effectiveLedgerNeedsReview 必须由 globalComparisonAllowed 驱动',
  )
})

test('ScoringTotalsBanner: isOk 含 !effectiveLedgerNeedsReview（不再直接用 !ledgerNeedsReview）', () => {
  const isOkMatch = bannerSource.match(/const\s+isOk\s*=\s*[^\n]+/)
  assert.ok(isOkMatch, 'isOk 定义必须存在')
  assert.match(
    isOkMatch![0],
    /!effectiveLedgerNeedsReview/,
    'isOk 必须含 !effectiveLedgerNeedsReview，确保 global=false 时不被 L4 拉 amber',
  )
})

test('ScoringTotalsBanner: isOk 含 scope 级别 amber 状态', () => {
  const isOkMatch = bannerSource.match(/const\s+isOk\s*=\s*[^\n]+/)
  assert.ok(isOkMatch, 'isOk 定义必须存在')
  assert.match(
    isOkMatch![0],
    /scopeAmber/,
    'isOk 必须含 scopeAmber，确保 scope 有真实缺口时不显示绿色',
  )
})

test('ScoringTotalsBanner: 全局账本 review 渲染块由 effectiveLedgerNeedsReview 门控', () => {
  // ledgerNeedsReview 的渲染块由 effectiveLedgerNeedsReview 门控（不得仍用 ledgerNeedsReview）
  // 检查含 ledgerTotalsLine 的区域前的 if 条件
  const ledgerBlockIdx = bannerSource.indexOf('ledgerTotalsLine(')
  assert.ok(ledgerBlockIdx > -1, 'ledgerTotalsLine 渲染点必须存在')
  // JSX 分支含组合提示子块，向前覆盖完整门禁条件而非依赖紧邻字符数。
  const vicinity = bannerSource.slice(Math.max(0, ledgerBlockIdx - 1200), ledgerBlockIdx)
  assert.match(
    vicinity,
    /effectiveLedgerNeedsReview/,
    'ledgerTotalsLine 的渲染条件必须含 effectiveLedgerNeedsReview（global=false 时不渲染）',
  )
})

// ===== ⑦ ScoringTotalsBanner 引入 ScopedScoringReportPanel =====

test('ScoringTotalsBanner 引入 ScopedScoringReportPanel', () => {
  assert.match(
    bannerSource,
    /ScopedScoringReportPanel/,
    'Banner 必须引入并使用 ScopedScoringReportPanel 以渲染 scope 级别报告',
  )
})

// ===== ⑧ shouldShowAddScoringItem: globalComparisonAllowed=false 时的精确行为 =====

test('shouldShowAddScoringItem: global=false, 无 scopeReports → 不显示', () => {
  const input: ScoringAddItemVisibilityInput = {
    isOk: false,
    expectedTotal: null,
    extractedTotal: 50,
    globalComparisonAllowed: false,
    scopeReports: [],
  }
  assert.equal(shouldShowAddScoringItem(input), false)
})

test('shouldShowAddScoringItem: global=false, 2499场景 all scope 100/100 → 不显示', () => {
  const input: ScoringAddItemVisibilityInput = {
    isOk: false,
    expectedTotal: null,
    extractedTotal: 200,
    globalComparisonAllowed: false,
    scopeReports: [
      makeScope({ scopeId: 's1', lot: '采购包1', totalGap: 0, extractedTotal: 100, expectedTotal: 100 }),
      makeScope({ scopeId: 's2', lot: '采购包2', totalGap: 0, extractedTotal: 100, expectedTotal: 100 }),
    ],
  }
  assert.equal(shouldShowAddScoringItem(input), false, '2499: 两scope均通过，不显示新增入口')
})

test('shouldShowAddScoringItem: global=false, scope 有明确负gap → 显示', () => {
  const input: ScoringAddItemVisibilityInput = {
    isOk: false,
    expectedTotal: null,
    extractedTotal: 180,
    globalComparisonAllowed: false,
    scopeReports: [
      makeScope({ scopeId: 's1', lot: '采购包1', totalGap: 0 }),
      makeScope({
        scopeId: 's2',
        lot: '采购包2',
        status: 'review_required',
        totalGap: -20,
        extractedTotal: 80,
        expectedTotal: 100,
      }),
    ],
  }
  assert.equal(shouldShowAddScoringItem(input), true, 'scope 有负 gap → 显示新增入口')
})

test('shouldShowAddScoringItem: global=false, 误降级负 gap → 显示', () => {
  const input: ScoringAddItemVisibilityInput = {
    isOk: false,
    expectedTotal: null,
    extractedTotal: 140,
    globalComparisonAllowed: false,
    scopeReports: [
      makeScope({
        scopeId: 'scheme_A',
        scheme: 'scheme_A',
        lot: '',
        applicability: 'unresolved',
        status: 'review_required',
        expectedTotal: 100,
        extractedTotal: 60,
        totalGap: -40,
        basisLowConfidence: false,
      }),
      makeScope({
        scopeId: 'scheme_B',
        scheme: 'scheme_B',
        lot: '',
        applicability: 'unresolved',
        status: 'review_required',
        expectedTotal: 100,
        extractedTotal: 80,
        totalGap: -20,
        basisLowConfidence: false,
      }),
    ],
  }
  assert.equal(
    shouldShowAddScoringItem(input),
    true,
    ' 2758: 误降级 unresolved 且负 gap 应显示补录入口',
  )
})

test('shouldShowAddScoringItem: global=false, 2502 各方案闭合 unresolved → 不显示', () => {
  const input: ScoringAddItemVisibilityInput = {
    isOk: false,
    expectedTotal: null,
    extractedTotal: 200,
    globalComparisonAllowed: false,
    scopeReports: [
      makeScope({
        scopeId: 's1',
        applicability: 'unresolved',
        totalGap: 0,
        extractedTotal: 100,
        expectedTotal: 100,
        multiSchemeSignal: 'two_schemes',
      }),
      makeScope({
        scopeId: 's2',
        applicability: 'unresolved',
        totalGap: 0,
        extractedTotal: 100,
        expectedTotal: 100,
        multiSchemeSignal: 'two_schemes',
      }),
    ],
  }
  assert.equal(
    shouldShowAddScoringItem(input),
    false,
    '2502: 各方案闭合，仅适用关系待确认，不诱导新增',
  )
})

test('shouldShowAddScoringItem: global=false, 误降级但正 gap（超分）→ 不显示', () => {
  const input: ScoringAddItemVisibilityInput = {
    isOk: false,
    expectedTotal: null,
    extractedTotal: 118,
    globalComparisonAllowed: false,
    scopeReports: [
      makeScope({
        scopeId: 's1',
        applicability: 'unresolved',
        status: 'review_required',
        totalGap: 18,
        extractedTotal: 118,
        expectedTotal: 100,
      }),
    ],
  }
  assert.equal(
    shouldShowAddScoringItem(input),
    false,
    '超分升 amber 但不给补录入口',
  )
})

test('shouldShowAddScoringItem: global=false, 质量不足 unresolved（低置信）负 gap → 不显示', () => {
  const input: ScoringAddItemVisibilityInput = {
    isOk: false,
    expectedTotal: null,
    extractedTotal: 90,
    globalComparisonAllowed: false,
    scopeReports: [
      makeScope({
        scopeId: 's1',
        applicability: 'unresolved',
        basisLowConfidence: true,
        totalGap: -10,
        extractedTotal: 90,
        expectedTotal: 100,
      }),
    ],
  }
  assert.equal(
    shouldShowAddScoringItem(input),
    false,
    '低置信 unresolved 属质量不足，不诱导新增',
  )
})

test('shouldShowAddScoringItem: global=false, unresolved 无基准且绝对分数不足 → 必须显示', () => {
  // 裁决(项目3414): 无基准 scope 绝对提取 50<100 时人工兜底入口必须可达,
  // 覆盖此前"无基准不显示"契约; 各 scope 均达 100 的不显示保护见 add-item spec。
  const input: ScoringAddItemVisibilityInput = {
    isOk: false,
    expectedTotal: null,
    extractedTotal: 50,
    globalComparisonAllowed: false,
    scopeReports: [
      makeScope({
        scopeId: 's1',
        applicability: 'unresolved',
        expectedTotal: null,
        totalGap: null,
        extractedTotal: 50,
      }),
    ],
  }
  assert.equal(shouldShowAddScoringItem(input), true, '无基准且分数不足时人工兜底入口必须可达')
})

test('shouldShowAddScoringItem: global=false, scope 多提（extractedTotal > expectedTotal）→ 不显示', () => {
  const input: ScoringAddItemVisibilityInput = {
    isOk: false,
    expectedTotal: null,
    extractedTotal: 220,
    globalComparisonAllowed: false,
    scopeReports: [
      makeScope({ scopeId: 's1', status: 'review_required', totalGap: 20, extractedTotal: 120, expectedTotal: 100 }),
      makeScope({ scopeId: 's2', totalGap: 0 }),
    ],
  }
  assert.equal(shouldShowAddScoringItem(input), false, 'scope 多提不显示新增入口')
})

test('shouldShowAddScoringItem: global=true 时沿用旧逻辑（向下兼容）', () => {
  // global=true / undefined 时走原有 totalGap/extractedTotal 路径
  const input: ScoringAddItemVisibilityInput = {
    isOk: false,
    expectedTotal: 100,
    extractedTotal: 80,
    globalComparisonAllowed: true,
  }
  assert.equal(shouldShowAddScoringItem(input), true, 'global=true, 提取<期望 → 仍显示（旧逻辑）')
})

// ===== R2 修订测试：basisLowConfidence 计入 anyUnresolved =====

test('buildScopeReportView: basisLowConfidence=true 且软自洽 → 豁免为基准提醒', () => {
  // 裁决(项目3414): 分值完全自洽、唯一疑点是基准低置信的 scope 不再蓝色阻断,
  // 与单包 isLedgerSelfConsistentWithSoftBasis 豁免同构; 改经 anyScopeSoftBasisOnly 提醒。
  const scopes = [makeScope({ basisLowConfidence: true, applicability: 'applicable', status: 'reconciled', totalGap: 0 })]
  const view = buildScopeReportView(scopes)
  assert.equal(view.anyUnresolved, false, '软自洽低置信 scope 不驱动蓝色阻断')
  assert.equal(view.anyScopeSoftBasisOnly, true, '须经基准提醒通道呈现')
})

test('buildScopeReportView: basisLowConfidence=true 且非自洽(有未归组) → 仍计入 anyUnresolved', () => {
  const scopes = [makeScope({
    basisLowConfidence: true, applicability: 'applicable', status: 'review_required',
    totalGap: 0, unlabeledSum: 30,
  })]
  const view = buildScopeReportView(scopes)
  assert.equal(view.anyUnresolved, true, '未归组分值存在时豁免不成立( 缺一不可)')
  assert.equal(view.anyScopeSoftBasisOnly, false)
})

test('buildScopeReportView: basisLowConfidence=true 使 allApplicableReconciled=false', () => {
  const scopes = [makeScope({ basisLowConfidence: true, applicability: 'applicable', status: 'reconciled', totalGap: 0 })]
  const view = buildScopeReportView(scopes)
  assert.equal(
    view.allApplicableReconciled,
    false,
    'basisLowConfidence scope 不得纳入 allApplicableReconciled（置信度不足，不标业务通过）',
  )
})

test('buildScopeReportView: 2502场景 unresolved + low-confidence → allApplicableReconciled=false, anyUnresolved=true', () => {
  const scopes = [
    makeScope({ scopeId: 's1', applicability: 'unresolved', multiSchemeSignal: 'two_schemes', totalGap: 0 }),
    makeScope({ scopeId: 's2', basisLowConfidence: true, applicability: 'applicable', status: 'reconciled', totalGap: 0 }),
  ]
  const view = buildScopeReportView(scopes)
  assert.equal(view.anyUnresolved, true, '2502: unresolved 或 basisLowConfidence 均应触发 anyUnresolved')
  assert.equal(view.allApplicableReconciled, false, '2502: 存在未确认项，不得宣告全部通过')
  assert.equal(view.anyApplicableGap, false, '2502: gap=0 时不产生 amber')
  assert.equal(view.anyMisclassifiedGap, false, '2502: gap=0 时不触发 anyMisclassifiedGap')
})

test('buildScopeReportView: allApplicableReconciled 要求无 unlabeledSum', () => {
  const scopes = [makeScope({ status: 'reconciled', totalGap: 0, unlabeledSum: 5 })]
  const view = buildScopeReportView(scopes)
  assert.equal(
    view.allApplicableReconciled,
    false,
    'unlabeledSum>0 时即使 status=reconciled 也不得视为全部通过',
  )
})

test('buildScopeReportView: allApplicableReconciled 要求无 insufficiencyCount', () => {
  const scopes = [makeScope({ status: 'reconciled', totalGap: 0, insufficiencyCount: 1 })]
  const view = buildScopeReportView(scopes)
  assert.equal(
    view.allApplicableReconciled,
    false,
    'insufficiencyCount>0 时即使 status=reconciled 也不得视为全部通过',
  )
})

// ===== R2 修订测试：transformScopedReportList dedup + sort =====

test('transformScopedReportList: 重复 scopeId 保留首个，丢弃后续（稳定去重）', () => {
  const result = transformScopedReportList([
    { ...validScopePayload, scope_id: 'dup', lot: '采购包1（首次）' },
    { ...validScopePayload, scope_id: 'dup', lot: '采购包1（重复）' },
    { ...validScopePayload, scope_id: 'unique', lot: '采购包2' },
  ])
  assert.equal(result.length, 2, '相同 scopeId 应去重，保留首个')
  assert.equal(result[0].lot, '采购包1（首次）', '首次出现的 scope 必须保留')
  assert.equal(result[1].scopeId, 'unique', '不重复的 scope 保留')
})

test('transformScopedReportList: 稳定排序——lot 优先，scheme 次之，scopeId 最后', () => {
  const result = transformScopedReportList([
    { ...validScopePayload, scope_id: 'c_3', lot: '采购包3', scheme: 'X' },
    { ...validScopePayload, scope_id: 'a_1', lot: '采购包1', scheme: 'A' },
    { ...validScopePayload, scope_id: 'b_2', lot: '采购包2', scheme: 'B' },
  ])
  assert.equal(result[0].scopeId, 'a_1', 'lot "采购包1" 应排最前')
  assert.equal(result[1].scopeId, 'b_2')
  assert.equal(result[2].scopeId, 'c_3', 'lot "采购包3" 应排最后')
})

test('transformScopedReportList: 同 lot 时按 scheme 排序', () => {
  const result = transformScopedReportList([
    { ...validScopePayload, scope_id: 'Z_lot1', lot: '采购包1', scheme: 'Z方案' },
    { ...validScopePayload, scope_id: 'A_lot1', lot: '采购包1', scheme: 'A方案' },
  ])
  assert.equal(result[0].scheme, 'A方案', '同 lot 下应按 scheme 字母序排列')
})

// ===== R2 修订测试：SCOPE_SCORE_TOLERANCE 单源导出 =====

test('SCOPE_SCORE_TOLERANCE 可从 scoringScopeReportTransforms 导出且值为 0.01', () => {
  assert.equal(
    typeof SCOPE_SCORE_TOLERANCE,
    'number',
    'SCOPE_SCORE_TOLERANCE 必须是导出的数字常量',
  )
  assert.equal(SCOPE_SCORE_TOLERANCE, 0.01, 'SCOPE_SCORE_TOLERANCE 值应为 0.01')
})

// ===== R2 修订测试：Banner 三态 (green/amber/blue) =====

const panelSource = readFileSync(
  new URL('../src/pages/RequirementReview/components/ScopedScoringReportPanel.tsx', import.meta.url),
  'utf8',
)

test('ScoringTotalsBanner: isScopeInfoOnly 变量存在（蓝色中性态驱动源）', () => {
  assert.match(
    bannerSource,
    /isScopeInfoOnly/,
    'Banner 必须引入 isScopeInfoOnly 以驱动蓝色中性态（区别于 amber 错误态和 green 通过态）',
  )
})

test('ScoringTotalsBanner: isOk 排除 isScopeInfoOnly（unresolved 不算绿色通过）', () => {
  const isOkMatch = bannerSource.match(/const\s+isOk\s*=\s*[^\n]+/)
  assert.ok(isOkMatch, 'isOk 定义必须存在')
  assert.match(
    isOkMatch![0],
    /isScopeInfoOnly/,
    'isOk 必须排除 isScopeInfoOnly：unresolved 态不应显示绿色通过',
  )
})

test('ScoringTotalsBanner: 三态 CSS 支持——存在蓝色容器/标题/正文/hint 样式', () => {
  assert.match(
    bannerSource,
    /bg-blue-50/,
    'Banner 必须含蓝色背景类支持 isScopeInfoOnly 三态，当前只有 bg-green/bg-amber',
  )
  assert.match(
    bannerSource,
    /text-blue-/,
    'Banner 必须含蓝色文字类以供蓝色中性态标题/正文使用',
  )
  assert.match(
    bannerSource,
    /border-blue-/,
    'Banner 的 actionAreaBorderClass 或容器 border 必须支持蓝色态',
  )
})

test('ScoringTotalsBanner: legacyVolumeGapsVisible 含 globalComparisonAllowed 门控', () => {
  const match = bannerSource.match(/const\s+legacyVolumeGapsVisible\s*=[\s\S]{0,400}/)
  assert.ok(match, 'legacyVolumeGapsVisible 定义必须存在')
  assert.match(
    match![0],
    /globalComparisonAllowed/,
    'legacyVolumeGapsVisible 必须含 globalComparisonAllowed 门控；global=false 时旧卷差额不得渲染',
  )
})

test('ScoringTotalsBanner: legacyReviewSuperseded 对 active scoped reports 成立（不被 ledgerNeedsReview 阻断）', () => {
  const match = bannerSource.match(/const\s+legacyReviewSuperseded\s*=[\s\S]{0,400}/)
  assert.ok(match, 'legacyReviewSuperseded 定义必须存在')
  assert.match(
    match![0],
    /scopeView/,
    'legacyReviewSuperseded 应在 scopeView 存在时成立，不能被 top-level ledgerNeedsReview 独自阻断',
  )
})

test('ScoringTotalsBanner: legacyDiagnosticsVisible 在 global=false+scoped reports active 时被抑制', () => {
  const match = bannerSource.match(/const\s+legacyDiagnosticsVisible\s*=[\s\S]{0,300}/)
  assert.ok(match, 'legacyDiagnosticsVisible 定义必须存在')
  assert.match(
    match![0],
    /globalComparisonAllowed|scopeView|legacyReviewSuperseded/,
    'legacyDiagnosticsVisible 必须在 scoped reports 接管时被抑制',
  )
})

// ===== R2 修订测试：shouldShowAddScoringItem - basisLowConfidence =====

test('shouldShowAddScoringItem: global=false, basisLowConfidence scope 有负 gap → 不显示（置信度不足）', () => {
  const input: ScoringAddItemVisibilityInput = {
    isOk: false,
    expectedTotal: null,
    extractedTotal: 90,
    globalComparisonAllowed: false,
    scopeReports: [
      makeScope({
        scopeId: 's1',
        applicability: 'applicable',
        basisLowConfidence: true,
        totalGap: -10,
        extractedTotal: 90,
        expectedTotal: 100,
      }),
    ],
  }
  assert.equal(
    shouldShowAddScoringItem(input),
    false,
    '基准置信度低的 scope 不应触发新增入口（gap 可能是假阳性）',
  )
})

// ===== R2 修订测试：ScopedScoringReportPanel basisLowConfidence 蓝色提醒 =====

test('ScopedScoringReportPanel: 使用 SCOPE_SCORE_TOLERANCE 而非重复定义本地 SCORE_TOLERANCE', () => {
  assert.doesNotMatch(
    panelSource,
    /const\s+SCORE_TOLERANCE\s*=/,
    'Panel 不得重复定义本地 SCORE_TOLERANCE，应从 scoringScopeReportTransforms 导入',
  )
  assert.match(
    panelSource,
    /SCOPE_SCORE_TOLERANCE/,
    'Panel 必须使用从 transforms 导入的 SCOPE_SCORE_TOLERANCE 常量',
  )
})

test('ScoringTotalsBanner: scopeAmber 消费 anyMisclassifiedGap', () => {
  assert.match(
    bannerSource,
    /anyMisclassifiedGap/,
    'Banner scopeAmber 必须消费 anyMisclassifiedGap，使误降级方案缺口升 amber',
  )
})

test('ScopedScoringReportPanel: amber 区纳入 anyMisclassifiedGap', () => {
  assert.match(
    panelSource,
    /anyMisclassifiedGap/,
    'Panel amber 渲染条件必须纳入 anyMisclassifiedGap',
  )
  assert.match(
    panelSource,
    /isMisclassifiedApplicable/,
    'Panel hasApplicableIssue 必须识别误降级 scope',
  )
})

// ===== R3 复审修订测试：C-NEW-1 / I-NEW-1 / I-NEW-2 及 Minor =====

const addItemSource = readFileSync(
  new URL('../src/pages/RequirementReview/scoringAddItemVisibility.ts', import.meta.url),
  'utf8',
)
const transformsSource = readFileSync(
  new URL('../src/pages/RequirementReview/scoringScopeReportTransforms.ts', import.meta.url),
  'utf8',
)

// I-NEW-1: anyUnresolved not_applicable 永不触发 -----

test('buildScopeReportView: not_applicable + basisLowConfidence=true → anyUnresolved=false', () => {
  const scopes = [
    makeScope({ applicability: 'not_applicable', basisLowConfidence: true }),
  ]
  const view = buildScopeReportView(scopes)
  assert.equal(
    view.anyUnresolved,
    false,
    'not_applicable scope 即便 basisLowConfidence=true 也不应触发 anyUnresolved（适用关系不适用不等于待确认）',
  )
})

test('buildScopeReportView 待确认 scope 集合仅统计 unresolved 或 applicable+basisLowConfidence', () => {
  // 后 anyUnresolved 由 uncertainScopes(候选集) + 软自洽豁免两段构成;
  // not_applicable 不得触发的保护约束由候选集 filter 承担, 契约随实现同步。
  const match = transformsSource.match(/const\s+uncertainScopes\s*=[\s\S]{0,250}/)
  assert.ok(match, 'uncertainScopes 候选集定义必须存在')
  assert.match(
    match![0],
    /applicability\s*===\s*['"]applicable['"]/,
    'uncertainScopes 的 basisLowConfidence 分支必须限定 applicability === "applicable"，防止 not_applicable 误触发',
  )
})

test('buildScopeReportView: not_applicable 唯一 scope → allApplicableReconciled=false、anyUnresolved=false、anyApplicableGap=false', () => {
  const scopes = [makeScope({ applicability: 'not_applicable', basisLowConfidence: false, totalGap: 0 })]
  const view = buildScopeReportView(scopes)
  assert.equal(view.anyUnresolved, false, 'not_applicable 不计入 anyUnresolved')
  assert.equal(view.allApplicableReconciled, false, '无 applicable scope 不得为 true（空集保护）')
  assert.equal(view.anyApplicableGap, false, 'not_applicable 不计入 anyApplicableGap')
})

// I-NEW-2: Panel hasBasisLowConfidence 仅统计 applicable scope -----

test('ScopedScoringReportPanel: hasBasisLowConfidence 源码限定 applicable scope', () => {
  assert.match(
    panelSource,
    /hasBasisLowConfidence[\s\S]{0,80}applicability\s*===\s*['"]applicable['"]/,
    'hasBasisLowConfidence 必须限定 applicability === "applicable"，not_applicable 不得触发蓝色提醒',
  )
})

// C-NEW-1: hasOtherRealError 驱动 visualState=blue 严格条件 -----

test('ScoringTotalsBanner: hasOtherRealError 变量存在', () => {
  assert.match(
    bannerSource,
    /hasOtherRealError/,
    'Banner 必须引入 hasOtherRealError 变量，仅在无任何其他真实错误时允许 visualState=blue',
  )
})

test('ScoringTotalsBanner: hasOtherRealError 包含 effectiveReportNeedsReview', () => {
  const match = bannerSource.match(/const\s+hasOtherRealError\s*=[\s\S]{0,400}/)
  assert.ok(match, 'hasOtherRealError 定义必须存在')
  assert.match(
    match![0],
    /effectiveReportNeedsReview/,
    'hasOtherRealError 必须含 effectiveReportNeedsReview（legacy 复核属真实错误，不得降级为 blue）',
  )
})

test('ScoringTotalsBanner: hasOtherRealError 包含 scopeAmber、effectiveLedgerNeedsReview', () => {
  const match = bannerSource.match(/const\s+hasOtherRealError\s*=[\s\S]{0,500}/)
  assert.ok(match, 'hasOtherRealError 定义必须存在')
  assert.match(match![0], /scopeAmber/, 'hasOtherRealError 须含 scopeAmber')
  assert.match(match![0], /effectiveLedgerNeedsReview/, 'hasOtherRealError 须含 effectiveLedgerNeedsReview')
})

test('ScoringTotalsBanner: visualState=blue 仅当 isScopeInfoOnly && !hasOtherRealError', () => {
  const match = bannerSource.match(/const\s+visualState[\s\S]{0,200}/)
  assert.ok(match, 'visualState 定义必须存在')
  assert.match(
    match![0],
    /hasOtherRealError/,
    'visualState 必须结合 hasOtherRealError 判断；isScopeInfoOnly + 候选失败 → amber 而非 blue',
  )
})

// Minor: legacyReviewSuperseded 显式括号 -----

test('ScoringTotalsBanner: legacyReviewSuperseded 两个 OR 分支均有显式括号', () => {
  const match = bannerSource.match(/const\s+legacyReviewSuperseded\s*=[\s\S]{0,400}/)
  assert.ok(match, 'legacyReviewSuperseded 定义必须存在')
  const body = match![0]
  // 第一分支：以 (reportNeedsReview && !ledgerNeedsReview 开头
  assert.match(
    body,
    /\(\s*reportNeedsReview\s*&&\s*!ledgerNeedsReview/,
    '第一个 OR 分支必须以 (reportNeedsReview && !ledgerNeedsReview 开头（显式括号）',
  )
  // 第二分支：(reportNeedsReview && !!scopeView) 形式
  assert.match(
    body,
    /\(\s*reportNeedsReview\s*&&\s*!!scopeView\s*\)/,
    '第二个 OR 分支必须形如 (reportNeedsReview && !!scopeView)（显式括号）',
  )
})

// Minor: scoringAddItemVisibility 使用 SCOPE_SCORE_TOLERANCE -----

test('scoringAddItemVisibility: 不得定义本地 SCORE_TOLERANCE', () => {
  assert.doesNotMatch(
    addItemSource,
    /const\s+SCORE_TOLERANCE\s*=/,
    'scoringAddItemVisibility 必须删除本地 SCORE_TOLERANCE，复用 SCOPE_SCORE_TOLERANCE',
  )
})

test('scoringAddItemVisibility: 从 scoringScopeReportTransforms 导入 SCOPE_SCORE_TOLERANCE', () => {
  assert.match(
    addItemSource,
    /SCOPE_SCORE_TOLERANCE/,
    'scoringAddItemVisibility 必须使用从 transforms 导入的 SCOPE_SCORE_TOLERANCE',
  )
})

// Minor: blue icon 使用 info-circle 路径 -----

test('ScoringTotalsBanner: 蓝色态主 icon 使用 info-circle（M13 16h...）而非 alert-circle', () => {
  assert.match(
    bannerSource,
    /M13 16h-1v-4h-1m1-4h\.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z/,
    'Banner 蓝色中性态主 icon 必须使用 info-circle 路径（M13 16h...），而非 alert-circle（M12 9v2...）',
  )
})

// Minor: localeCompare 使用 zh-Hans-CN -----

test('transformScopedReportList: localeCompare 使用 zh-Hans-CN locale', () => {
  assert.match(
    transformsSource,
    /localeCompare\([^,)]+,\s*['"]zh-Hans-CN['"]/,
    'localeCompare 必须指定 zh-Hans-CN locale，保证中文名称排序稳定正确',
  )
})
