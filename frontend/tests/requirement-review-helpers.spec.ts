/**
 * Phase 1 防御测试基线（R2-10）：
 * 锁定 RequirementReview/helpers.ts 的 4 个纯函数契约，
 * 在后续 hook / component 拆分 Step 中作为重构防御网，
 * 任一断言失败即视为拆分破坏既有行为。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  getCategoryOrder,
  mapExtractionToSteps,
  mapStandardExtractionSteps,
  sortRequirements,
} from '../src/pages/RequirementReview/helpers.ts'
import { EXTRACTION_STEPS } from '../src/pages/RequirementReview/constants.ts'
import {
  buildScoringHeadlineText,
  buildScoringTotalsSummary,
  isLedgerGapFoldingArtifact,
  isLedgerSelfConsistentWithSoftBasis,
  ledgerReviewHeadline,
  ledgerTotalsLine,
  resolveLedgerReviewReason,
  formatScore,
  formatScoringRules,
  transformRequirement,
  transformScoringTotalsReport,
  volumeCheckHint,
  type Requirement,
} from '../src/pages/requirementReviewTransforms.ts'

const scoringTotalsBannerSource = readFileSync(
  new URL('../src/pages/RequirementReview/components/ScoringTotalsBanner.tsx', import.meta.url),
  'utf8',
)

const makeReq = (overrides: Partial<Requirement> = {}): Requirement => ({
  id: 1,
  category: 'TECH',
  description: 'desc',
  isMandatory: false,
  confirmed: false,
  documentId: null,
  sourcePage: null,
  sourcePages: [],
  mergedCount: null,
  subRequirements: [],
  memberIds: [1],
  ...overrides,
})

// ===== sortRequirements =====

test('sortRequirements 否决项优先于非否决项（不论分类）', () => {
  const items: Requirement[] = [
    makeReq({ id: 1, category: 'QUAL', isMandatory: false }),
    makeReq({ id: 2, category: 'FORMAT', isMandatory: true }),
    makeReq({ id: 3, category: 'TECH', isMandatory: false }),
  ]
  const sorted = sortRequirements(items)
  assert.equal(sorted[0].id, 2, '否决项 FORMAT 应排到第一位，先于非否决项的 QUAL')
  assert.equal(sorted[0].isMandatory, true)
  assert.equal(sorted[1].isMandatory, false, '其余项保持非否决项顺序')
})

test('sortRequirements 同强制性下按分类优先级 QUAL > TECH > COMM > PERF > STAFF > FORMAT', () => {
  const items: Requirement[] = [
    makeReq({ id: 1, category: 'FORMAT', isMandatory: false }),
    makeReq({ id: 2, category: 'qualification', isMandatory: false }),
    makeReq({ id: 3, category: 'TECH', isMandatory: false }),
    makeReq({ id: 4, category: 'staffing', isMandatory: false }),
  ]
  const sorted = sortRequirements(items)
  assert.deepEqual(
    sorted.map((r) => r.category),
    ['qualification', 'TECH', 'staffing', 'FORMAT'],
    '混合 name 与 value 形式时仍按权重排序',
  )
})

test('sortRequirements 不修改原数组（浅拷贝契约）', () => {
  const items: Requirement[] = [
    makeReq({ id: 1, category: 'FORMAT' }),
    makeReq({ id: 2, category: 'QUAL' }),
  ]
  const snapshot = items.map((r) => r.id)
  sortRequirements(items)
  assert.deepEqual(items.map((r) => r.id), snapshot, '原数组顺序必须保留')
})

// ===== getCategoryOrder =====

test('getCategoryOrder 同时支持枚举 name 与 value 两种形式', () => {
  assert.equal(getCategoryOrder('QUAL'), 1)
  assert.equal(getCategoryOrder('qualification'), 1)
  assert.equal(getCategoryOrder('TECH'), 2)
  assert.equal(getCategoryOrder('technical'), 2)
  assert.equal(getCategoryOrder('format'), 6)
})

test('getCategoryOrder 未知分类用 99 兜底排到最后', () => {
  assert.equal(getCategoryOrder('UNKNOWN'), 99)
  assert.equal(getCategoryOrder('xyz'), 99, '小写未知会先 upper-case 再查也不命中')
})

// ===== mapExtractionToSteps =====

test('mapExtractionToSteps 各 stage 推进且完成态全部标 completed', () => {
  const extracting = mapExtractionToSteps('extracting', 35, '正在解析第 3 页')
  assert.equal(extracting.steps.length, 5)
  assert.equal(extracting.steps[0].status, 'processing')
  assert.equal(extracting.steps[1].status, 'pending')
  assert.equal(extracting.steps[2].status, 'pending')
  assert.equal(extracting.steps[3].status, 'pending')
  assert.equal(extracting.steps[4].status, 'pending')
  assert.equal(extracting.steps[0].detail, '正在解析第 3 页')
  assert.equal(extracting.overall, 0.35)

  const merging = mapExtractionToSteps('merging', 70, '合并去重中')
  assert.equal(merging.steps[0].status, 'completed')
  assert.equal(merging.steps[1].status, 'completed', '高风险核验在合并前已完成')
  assert.equal(merging.steps[2].status, 'processing')
  assert.equal(merging.steps[3].status, 'pending')
  assert.equal(merging.steps[4].status, 'pending')

  const saving = mapExtractionToSteps('saving', 80, '保存到数据库')
  assert.equal(saving.steps[2].status, 'processing', 'saving 与 merging 同属"合并与保存"步')

  const done = mapExtractionToSteps('completed', 100, '完成')
  assert.equal(done.steps.every((s) => s.status === 'completed'), true)
  assert.equal(done.overall, 1)
})

test('mapExtractionToSteps 高风险条款核验是合并前的独立第二步', () => {
  const coverage = mapExtractionToSteps('mandatory_coverage', 52, '核验否决与资格条款 1/4')
  assert.deepEqual(
    coverage.steps.map((s) => s.id),
    ['extract', 'mandatory_coverage', 'consolidate', 'scoring', 'finalize'],
  )
  assert.deepEqual(
    coverage.steps.map((s) => s.status),
    ['completed', 'processing', 'pending', 'pending', 'pending'],
  )
  assert.equal(coverage.steps[1].detail, '核验否决与资格条款 1/4')
  // (52-50)/(55-50) = 0.4
  assert.equal(Math.round(coverage.steps[1].targetProgress * 100) / 100, 0.4)
})

test('mapExtractionToSteps 评分项提取是独立第四步，不再折叠进保存结果', () => {
  const scoring = mapExtractionToSteps('scoring_extract', 93, '评分项提取中')
  assert.deepEqual(
    scoring.steps.map((s) => s.id),
    ['extract', 'mandatory_coverage', 'consolidate', 'scoring', 'finalize'],
    '步骤 id 必须与后端 EXTRACT_PROGRESS_STEPS 的 key 同名',
  )
  assert.deepEqual(
    scoring.steps.map((s) => s.status),
    ['completed', 'completed', 'completed', 'processing', 'pending'],
  )
  assert.equal(scoring.steps[3].detail, '评分项提取中')
  // (93-90)/(96-90) = 0.5
  assert.equal(Math.round(scoring.steps[3].targetProgress * 100) / 100, 0.5)
})

test('mapExtractionToSteps 归并核验为第五步且评分步已完成', () => {
  const finalizing = mapExtractionToSteps('finalizing', 97, '整理提取结果')
  assert.deepEqual(
    finalizing.steps.map((s) => s.status),
    ['completed', 'completed', 'completed', 'completed', 'processing'],
  )
})

test('mapExtractionToSteps 未知 stage 全部回退到 pending（idx -1）', () => {
  const unknown = mapExtractionToSteps('garbage', 50, '')
  assert.equal(unknown.steps.every((s) => s.status === 'pending'), true)
  assert.equal(unknown.overall, 0.5, 'overall 仍按 progress / 100 推算')
})

// ===== mapStandardExtractionSteps =====

test('mapStandardExtractionSteps null 兜底：rawSteps 解析后为空时返回 null', () => {
  assert.equal(mapStandardExtractionSteps(null, 0, ''), null)
  assert.equal(mapStandardExtractionSteps([], 50, '提取中'), null)
  assert.equal(mapStandardExtractionSteps('not-an-array', 30, ''), null)
})

test('mapStandardExtractionSteps 步骤映射保持索引顺序与 EXTRACTION_STEPS 一致', () => {
  const raw = [
    { key: 'extract', label: '要求提取', status: 'completed', progress: 100 },
    { key: 'mandatory_coverage', label: '高风险条款核验', status: 'completed', progress: 100 },
    { key: 'consolidate', label: '合并与保存', status: 'processing', progress: 50, detail: '正在合并' },
    { key: 'scoring', label: '评分项提取', status: 'pending', progress: 0 },
    { key: 'finalize', label: '归并核验', status: 'pending', progress: 0 },
  ]
  const result = mapStandardExtractionSteps(raw, 65, '')
  assert.notEqual(result, null)
  if (!result) return

  assert.equal(result.steps.length, 5)
  assert.deepEqual(
    result.steps.map((s) => s.id),
    EXTRACTION_STEPS.map((s) => s.id),
  )

  assert.equal(result.steps[0].status, 'completed')
  assert.equal(result.steps[0].targetProgress, 1)
  assert.equal(result.steps[1].status, 'completed')
  assert.equal(result.steps[2].status, 'processing')
  assert.equal(result.steps[2].targetProgress, 0.5)
  assert.equal(result.steps[2].detail, '正在合并')
  assert.equal(result.steps[3].status, 'pending')
  assert.equal(result.steps[4].status, 'pending')

  assert.equal(result.overall, 0.65)
  assert.equal(
    result.stageText,
    '步骤3 合并与保存 · 正在合并',
    'fallbackText 为空且有 processing 步骤时，stageText 由 processing 步骤推断',
  )
})

test('mapStandardExtractionSteps 步骤 id 优先取后端 key，不受数组下标影响', () => {
  // 后端若在中间插入步骤而前端模板尚未同步，按下标对位会静默错位；按 key 绑定则不会。
  const raw = [
    { key: 'scoring', label: '评分项提取', status: 'processing', progress: 30, detail: '评分项提取中' },
  ]
  const result = mapStandardExtractionSteps(raw, 88, '')
  assert.notEqual(result, null)
  if (!result) return
  assert.equal(result.steps[0].id, 'scoring', '下标 0 对应模板 extract，但 key 必须优先')
})

test('mapStandardExtractionSteps 后端未下发 key 时回退按下标绑定', () => {
  const raw = [
    { label: '要求提取', status: 'processing', progress: 30 },
    { label: '合并与保存', status: 'pending', progress: 0 },
  ]
  const result = mapStandardExtractionSteps(raw, 25, '')
  assert.notEqual(result, null)
  if (!result) return
  assert.equal(result.steps[0].id, EXTRACTION_STEPS[0].id)
  assert.equal(result.steps[1].id, EXTRACTION_STEPS[1].id)
})

test('mapStandardExtractionSteps fallbackText 非空时优先使用 fallbackText 作为 stageText', () => {
  const raw = [
    { key: 'extract', label: '要求提取', status: 'processing', progress: 30 },
    { key: 'consolidate', label: '合并与保存', status: 'pending', progress: 0 },
    { key: 'scoring', label: '评分项提取', status: 'pending', progress: 0 },
    { key: 'finalize', label: '归并核验', status: 'pending', progress: 0 },
  ]
  const result = mapStandardExtractionSteps(raw, 25, '后端推送：提取进度 30%')
  assert.notEqual(result, null)
  if (!result) return
  assert.equal(result.stageText, '后端推送：提取进度 30%')
})

// ===== scoring totals report =====

test('transformScoringTotalsReport 保留评分项复核 diagnostics', () => {
  const report = transformScoringTotalsReport({
    needs_review: true,
    note: '按卷满分校验未通过',
    by_volume: { technical: 90 },
    volume_checks: {
      technical: { sum: 90, expected: 100, weight: 1, within: false },
    },
    weight_sum_ok: true,
    item_count: 1,
    missing_score_count: 0,
    diagnostics: ['technical卷满分合计90≠基准100'],
  })

  assert.notEqual(report, null)
  assert.deepEqual(report?.diagnostics, ['technical卷满分合计90≠基准100'])
})

test('transformScoringTotalsReport 保留评审标准缺失计数字段', () => {
  const report = transformScoringTotalsReport({
    needs_review: true,
    note: '评审标准疑似缺失',
    by_volume: { technical: 100 },
    volume_checks: {},
    weight_sum_ok: true,
    item_count: 1,
    missing_score_count: 0,
    missing_rule_count: 1,
    diagnostics: ['有 1 项评分项评审标准疑似缺失，请人工补齐'],
  })

  assert.notEqual(report, null)
  assert.equal(report?.missingRuleCount, 1)
  assert.match(report?.diagnostics.join('\n') ?? '', /评审标准疑似缺失/)
})

test('transformScoringTotalsReport 解析结构复核标记且旧 payload 默认 false', () => {
  const legacy = transformScoringTotalsReport({
    needs_review: false,
    item_count: 1,
    missing_score_count: 0,
  })
  const structural = transformScoringTotalsReport({
    needs_review: true,
    structure_review_required: true,
    item_count: 1,
    missing_score_count: 0,
  })

  assert.equal(legacy?.structureReviewRequired, false)
  assert.equal(structural?.structureReviewRequired, true)
})

test('ScoringTotalsBanner 异常态展示评审标准缺失提示', () => {
  assert.match(scoringTotalsBannerSource, /missingRuleCount/)
  assert.match(scoringTotalsBannerSource, /评审标准疑似缺失/)
})

test('ScoringTotalsBanner 规则缺失不应误写为分值不一致', () => {
  assert.match(scoringTotalsBannerSource, /scoreGapNeedsReview/)
  assert.match(scoringTotalsBannerSource, /评分项规则信息待完善/)
  assert.match(scoringTotalsBannerSource, /评分项分值与评分表不一致/)
})

test('诊断仍全量保留在折叠区且不过滤（含页码定位，不得因精简UI丢失）', () => {
  // 原标签"查看技术详情"按用户反馈移除（内含逻辑表编号/错误码/基准分级等零信息量内容），
  // 但 report.diagnostics 含"已识别构成：价格 30.0 分（第34页）"这类可核对原文的定位信息，
  // 必须保留且不得过滤——这是本断言防御的原始风险，与标签改名无关。
  assert.match(scoringTotalsBannerSource, /分值核对明细/)
  assert.match(scoringTotalsBannerSource, /report\.diagnostics\.map/)
  assert.doesNotMatch(scoringTotalsBannerSource, /diagnostics\.filter/)
  // 技术噪音必须已从展示层清除
  assert.doesNotMatch(scoringTotalsBannerSource, /查看技术详情/)
  assert.doesNotMatch(
    scoringTotalsBannerSource,
    /logicalTableKey}（/,
    '逻辑表编号与错误码属实现细节，不得展示给用户',
  )
})

test('ScoringTotalsBanner 默认视图用缺/多分值的用户语言描述卷差异', () => {
  assert.match(scoringTotalsBannerSource, /volumeGapText/)
  assert.match(scoringTotalsBannerSource, /缺 \$\{formatScore\(delta\)\} 分/)
  assert.match(scoringTotalsBannerSource, /多 \$\{formatScore\(-delta\)\} 分/)
})

test('transformScoringTotalsReport 对 diagnostics 保序去重（重复告警兜底）', () => {
  const report = transformScoringTotalsReport({
    needs_review: true,
    note: '按采购包评分项满分校验未通过',
    by_volume: { technical: 64.5, price: 30 },
    by_lot: { 采购包1: 94.5, 采购包2: 100 },
    volume_checks: {},
    item_count: 32,
    missing_score_count: 0,
    diagnostics: [
      '采购包1: 满分合计 94.5 与期望 100.0 偏差超容差，请人工复核',
      '采购包1: 满分合计 94.5 与期望 100.0 偏差超容差，请人工复核',
      '采购包1: 已识别构成：价格 30.0 分（第34页）',
    ],
  })

  assert.notEqual(report, null)
  assert.deepEqual(report?.diagnostics, [
    '采购包1: 满分合计 94.5 与期望 100.0 偏差超容差，请人工复核',
    '采购包1: 已识别构成：价格 30.0 分（第34页）',
  ])
  assert.deepEqual(report?.byLot, { 采购包1: 94.5, 采购包2: 100 })
})

test('transformScoringTotalsReport 缺 by_lot 字段时回退空对象（旧接口兼容）', () => {
  const report = transformScoringTotalsReport({
    needs_review: false,
    by_volume: { technical: 100 },
    volume_checks: {},
    item_count: 3,
    missing_score_count: 0,
    diagnostics: [],
  })
  assert.notEqual(report, null)
  assert.deepEqual(report?.byLot, {})
})

test('transformScoringTotalsReport 保留评分项来源统计字段', () => {
  const report = transformScoringTotalsReport({
    needs_review: false,
    note: 'ok',
    by_volume: { technical: 100 },
    volume_checks: {},
    item_count: 2,
    missing_score_count: 0,
    authority_count: 0,
    llm_count: 2,
  })

  assert.notEqual(report, null)
  assert.equal(report?.authorityCount, 0)
  assert.equal(report?.llmCount, 2)
  assert.equal(report?.suspectedAssessmentCount, 0)
  assert.equal(report?.suspectedAssessmentScore, 0)
})

test('transformScoringTotalsReport 保留疑似履约考核计数字段', () => {
  const report = transformScoringTotalsReport({
    needs_review: false,
    note: 'ok',
    by_volume: { technical: 100 },
    volume_checks: {},
    item_count: 10,
    missing_score_count: 0,
    suspected_assessment_count: 26,
    suspected_assessment_score: 161,
  })

  assert.notEqual(report, null)
  assert.equal(report?.needsReview, false)
  assert.equal(report?.suspectedAssessmentCount, 26)
  assert.equal(report?.suspectedAssessmentScore, 161)
})

// ===== buildScoringTotalsSummary / formatScore =====

test('buildScoringTotalsSummary 多卷时给出总分与降序分卷明细', () => {
  const report = transformScoringTotalsReport({
    needs_review: false,
    note: '按卷评分项满分校验通过',
    by_volume: { commercial: 40, technical: 60 },
    volume_checks: {
      technical: { sum: 60, expected: 60, weight: 0.6, within: true },
      commercial: { sum: 40, expected: 40, weight: 0.4, within: true },
    },
    weight_sum_ok: true,
    item_count: 5,
    missing_score_count: 0,
    diagnostics: [],
  })
  assert.notEqual(report, null)
  if (!report) return
  const summary = buildScoringTotalsSummary(report)
  assert.equal(summary.itemCount, 5)
  assert.equal(summary.totalScore, 100)
  assert.deepEqual(
    summary.shares,
    [
      { label: '技术标', score: 60 },
      { label: '商务标', score: 40 },
    ],
    '分卷明细按分值降序，技术标(60)先于商务标(40)',
  )
})

test('buildScoringTotalsSummary 折算制三卷各100时生成权重摘要而非总分300文案', () => {
  const report = transformScoringTotalsReport({
    needs_review: false,
    note: '按卷满分与权重校验通过',
    by_volume: { qualification: 100, technical: 100, commercial: 100 },
    volume_checks: {
      qualification: { sum: 100, expected: 100, weight: 0.1, within: true },
      technical: { sum: 100, expected: 100, weight: 0.5, within: true },
      commercial: { sum: 100, expected: 100, weight: 0.4, within: true },
    },
    weight_sum_ok: true,
    item_count: 3,
    missing_score_count: 0,
    diagnostics: [],
  })
  assert.notEqual(report, null)
  if (!report) return
  const summary = buildScoringTotalsSummary(report)
  assert.equal(summary.totalScore, 300)
  assert.equal(
    summary.foldedText,
    '资格 100分×10%、技术标 100分×50%、商务标 100分×40%，折算基准100分',
  )
})

test('平铺制卷满分合计为100时不生成折算摘要', () => {
  const report = transformScoringTotalsReport({
    needs_review: false,
    note: '按卷满分与权重校验通过',
    by_volume: { commercial: 34, technical: 66 },
    volume_checks: {
      commercial: { sum: 34, expected: 34, weight: 0.34, within: true },
      technical: { sum: 66, expected: 66, weight: 0.66, within: true },
    },
    weight_sum_ok: true,
    item_count: 7,
    missing_score_count: 0,
    diagnostics: [],
  })
  assert.notEqual(report, null)
  if (!report) return
  const summary = buildScoringTotalsSummary(report)
  assert.equal(summary.totalScore, 100)
  assert.equal(summary.foldedText, null)
  assert.deepEqual(summary.shares, [
    { label: '技术标', score: 66 },
    { label: '商务标', score: 34 },
  ])
})

test('buildScoringTotalsSummary 单卷(全局100分制)不产生冗余明细', () => {
  const report = transformScoringTotalsReport({
    needs_review: false,
    by_volume: { unspecified: 100 },
    volume_checks: {},
    item_count: 8,
    missing_score_count: 0,
    diagnostics: [],
  })
  if (!report) return
  const summary = buildScoringTotalsSummary(report)
  assert.equal(summary.totalScore, 100)
  assert.deepEqual(summary.shares, [], '仅一个卷时明细为空，避免出现“其中 其他 100 分”')
})

test('buildScoringTotalsSummary 过滤分值<=0 的卷后再求和与排序', () => {
  const report = transformScoringTotalsReport({
    needs_review: false,
    by_volume: { technical: 70, other: 0, price: 30 },
    volume_checks: {},
    item_count: 4,
    missing_score_count: 0,
    diagnostics: [],
  })
  if (!report) return
  const summary = buildScoringTotalsSummary(report)
  assert.equal(summary.totalScore, 100)
  assert.deepEqual(
    summary.shares.map((s) => s.label),
    ['技术标', '价格'],
    '分值为 0 的卷不计入明细',
  )
})

test('buildScoringTotalsSummary 浮点分值求和经 toFixed 收敛，无 99.9999 噪声', () => {
  const report = transformScoringTotalsReport({
    needs_review: false,
    by_volume: { technical: 33.33, commercial: 33.33, price: 33.34 },
    volume_checks: {},
    item_count: 3,
    missing_score_count: 0,
    diagnostics: [],
  })
  if (!report) return
  const summary = buildScoringTotalsSummary(report)
  assert.equal(summary.totalScore, 100, '三卷小数求和收敛为 100，而非 99.99999999')
})

test('buildScoringTotalsSummary 多包时给出按包合计明细（防 200/100 误导）', () => {
  const report = transformScoringTotalsReport({
    needs_review: false,
    note: '按采购包评分项满分校验通过（采购包1 100.0/100.0；采购包2 100.0/100.0）',
    by_volume: { technical: 160, price: 40 },
    by_lot: { 采购包2: 100, 采购包1: 100 },
    volume_checks: {},
    item_count: 32,
    missing_score_count: 0,
    diagnostics: [],
  })
  assert.notEqual(report, null)
  if (!report) return
  const summary = buildScoringTotalsSummary(report)
  assert.deepEqual(
    summary.lotShares,
    [
      { label: '采购包1', score: 100 },
      { label: '采购包2', score: 100 },
    ],
    '按包名升序展示各包合计，横幅须用它替代"总分 200 分"',
  )
})

test('buildScoringTotalsSummary 单包/无包信息时 lotShares 为空不干扰既有文案', () => {
  const report = transformScoringTotalsReport({
    needs_review: false,
    by_volume: { technical: 100 },
    by_lot: {},
    volume_checks: {},
    item_count: 8,
    missing_score_count: 0,
    diagnostics: [],
  })
  if (!report) return
  const summary = buildScoringTotalsSummary(report)
  assert.deepEqual(summary.lotShares, [])
})

// ===== buildScoringHeadlineText / foldedLedgerGapNote（-743）=====

/** doc1923(项目2442) 真实形态：技术100×70% + 价格100×30%，事实层合计200、折算基准100。 */
function _foldedReport() {
  return transformScoringTotalsReport({
    needs_review: false,
    note: '按卷满分与权重校验通过',
    by_volume: { technical: 100, price: 100 },
    volume_checks: {
      technical: { sum: 100, expected: 100, weight: 0.7, within: true },
      price: { sum: 100, expected: 100, weight: 0.3, within: true },
    },
    weight_sum_ok: true,
    item_count: 17,
    missing_score_count: 0,
    diagnostics: [],
  })
}

test('未通过校验时事实陈述仍须给出项数与分值构成，不得被判定门禁吞掉', () => {
  const report = _foldedReport()
  assert.notEqual(report, null)
  if (!report) return
  const summary = buildScoringTotalsSummary(report)

  const text = buildScoringHeadlineText(summary, { verified: false })

  assert.match(text, /已提取 17 个评分项/, '异常态必须仍告知提取项数')
  assert.match(text, /技术标 100分×70%/, '异常态必须仍给出分值构成')
  assert.doesNotMatch(
    text,
    /完成分值校验/,
    '未通过校验时不得声称"已完成分值校验"',
  )
})

test('通过态措辞含"已完成分值校验"且复用同一构成文案', () => {
  const report = _foldedReport()
  if (!report) return
  const summary = buildScoringTotalsSummary(report)

  assert.equal(
    buildScoringHeadlineText(summary, { verified: true }),
    '已成功提取 17 个评分项并完成分值校验，技术标 100分×70%、价格 100分×30%，折算基准100分。',
  )
})

test('多包文档按包展示合计，禁止相加成总分误导（口径继承）', () => {
  const report = transformScoringTotalsReport({
    needs_review: false,
    by_volume: { technical: 160, price: 40 },
    by_lot: { 采购包2: 100, 采购包1: 100 },
    volume_checks: {},
    item_count: 32,
    missing_score_count: 0,
    diagnostics: [],
  })
  if (!report) return

  const text = buildScoringHeadlineText(buildScoringTotalsSummary(report), {
    verified: false,
  })

  assert.match(text, /采购包1 合计 100 分、采购包2 合计 100 分/)
  assert.doesNotMatch(text, /总分 200/, '多包禁止相加成"总分 200 分"')
})

test('buildScoringHeadlineText 无报告时返回空串（横幅不渲染该行）', () => {
  assert.equal(buildScoringHeadlineText(null, { verified: true }), '')
})

/** doc1923 真实账本形态：L3 基准 100、事实层合计 200、差额 +100。 */
function _ledger(overrides: Record<string, unknown> = {}) {
  return {
    status: 'review_required',
    basisLevel: 'l3_doc_total',
    basisLowConfidence: true,
    expectedTotal: 100,
    extractedTotal: 200,
    totalGap: 100,
    groupRows: [],
    unlabeledSum: 0,
    multiSchemeSignal: '',
    duplicateExclusions: [],
    insufficiencyCount: 0,
    diagnostics: [],
    ...overrides,
  } as never
}

test('折算基准≡账本基准且差额恰等于未折算量时判定为假差额', () => {
  const report = _foldedReport()
  if (!report) return
  const summary = buildScoringTotalsSummary(report)

  // 折算基准 100(=100×0.4+100×0.6) 与账本 expectedTotal 100 一致；
  // 差额 100 恰等于 rawExpectedTotal(200) - foldedBase(100)。
  assert.equal(summary.folded?.foldedBase, 100)
  assert.equal(summary.folded?.rawExpectedTotal, 200)
  assert.equal(isLedgerGapFoldingArtifact(summary.folded, _ledger()), true)
})

test('差额偏离未折算量即为真实缺口，必须照常告警', () => {
  const report = _foldedReport()
  if (!report) return
  const summary = buildScoringTotalsSummary(report)

  // 应得 200 却只提到 190：差额 90 ≠ 折算量 100，属真实漏提。
  assert.equal(
    isLedgerGapFoldingArtifact(summary.folded, _ledger({ extractedTotal: 190, totalGap: 90 })),
    false,
    '真实漏提混入时不得被判为假差额而静默',
  )
})

test('账本基准与折算基准不一致时不判假差额（对目标分值有真分歧）', () => {
  const report = _foldedReport()
  if (!report) return
  const summary = buildScoringTotalsSummary(report)

  assert.equal(
    isLedgerGapFoldingArtifact(summary.folded, _ledger({ expectedTotal: 80, totalGap: 120 })),
    false,
  )
})

test('负差额（漏提方向）不得判为假差额', () => {
  const report = _foldedReport()
  if (!report) return
  const summary = buildScoringTotalsSummary(report)

  assert.equal(
    isLedgerGapFoldingArtifact(summary.folded, _ledger({ extractedTotal: 60, totalGap: -40 })),
    false,
  )
})

test('平铺制(无折算口径)与缺账本报告一律不判假差额', () => {
  assert.equal(isLedgerGapFoldingArtifact(null, _ledger()), false)
  const report = _foldedReport()
  if (!report) return
  assert.equal(isLedgerGapFoldingArtifact(buildScoringTotalsSummary(report).folded, null), false)
})

// =====: 需复核标题必须按真实原因分支，禁止单一文案覆盖全部原因 =====

test('doc1925(项目2443) 真实形态——gap=0 时标题绝不能说"不一致"', () => {
  // 实测形态: basis=l2_derived_group, low_conf=true, expected=100, extracted=100, gap=0
  const ledger = _ledger({
    basisLevel: 'l2_derived_group',
    basisLowConfidence: true,
    extractedTotal: 100,
    totalGap: 0,
  })

  const reason = resolveLedgerReviewReason(ledger)
  const headline = ledgerReviewHeadline(reason)

  assert.equal(reason, 'low_confidence')
  assert.doesNotMatch(
    headline,
    /不一致/,
    '分值 100 vs 100 完全对上，标题说"不一致"会被同屏数字当场否证',
  )
  // 该标题现仅用于 L4(无任何基准)；L2 的真实原因改由 basisConfidenceNotice
  // 分述（见 scoring-basis-notice.spec.ts），故此处不再断言标题含 L2 归因。
  // 对比行不得把归集值称作"招标文件要求"
  const line = ledgerTotalsLine(ledger)
  assert.match(line, /归集总分 100 分/)
  assert.doesNotMatch(line, /招标文件要求/, 'L2 是对提取结果的归集，非招标文件明写')
  // L2 下基准由提取结果推导，二者必然相等，标注"一致"会被误读为核对通过
  assert.doesNotMatch(line, /两者一致/, 'L2 同义反复不得表述为"一致"')
})

test('L1/L3 真实基准下"两者一致"才是有效结论', () => {
  const line = ledgerTotalsLine(
    _ledger({
      basisLevel: 'l1_explicit_group',
      basisLowConfidence: false,
      extractedTotal: 100,
      totalGap: 0,
    }),
  )
  assert.match(line, /招标文件要求 100 分/)
  assert.match(line, /两者一致/)
})

test('真有总分差额时才说"分值合计不一致"', () => {
  const reason = resolveLedgerReviewReason(_ledger())
  assert.equal(reason, 'total_gap')
  assert.match(ledgerReviewHeadline(reason), /分值合计与招标文件要求不一致/)
})

test('卷级缺口单独成因，文案区分于总分缺口', () => {
  const ledger = _ledger({
    extractedTotal: 100,
    totalGap: 0,
    groupRows: [{ label: '技术标', declared: 60, extractedSum: 50, gap: -10 }],
  })
  const reason = resolveLedgerReviewReason(ledger)
  assert.equal(reason, 'group_gap')
  assert.match(ledgerReviewHeadline(reason), /部分评分卷/)
})

test('多套方案 / 未归卷 / 证据不足 各有独立文案', () => {
  const multi = _ledger({ extractedTotal: 100, totalGap: 0, multiSchemeSignal: '检出2套' })
  assert.equal(resolveLedgerReviewReason(multi), 'multi_scheme')
  assert.match(ledgerReviewHeadline('multi_scheme'), /多套评分方案/)

  const unlabeled = _ledger({ extractedTotal: 100, totalGap: 0, unlabeledSum: 8 })
  assert.equal(resolveLedgerReviewReason(unlabeled), 'unlabeled')
  assert.match(ledgerReviewHeadline('unlabeled'), /分值构成需核实/)

  const insufficient = _ledger({ extractedTotal: 100, totalGap: 0, insufficiencyCount: 3 })
  assert.equal(resolveLedgerReviewReason(insufficient), 'insufficiency')
  assert.match(ledgerReviewHeadline('insufficiency'), /缺少评审标准/)
})

test('数值问题优先于元信息——同时存在时报可直接行动的那个', () => {
  const both = _ledger({ basisLowConfidence: true, multiSchemeSignal: '检出2套' })
  assert.equal(
    resolveLedgerReviewReason(both),
    'total_gap',
    'gap≠0 与低置信/多方案同时存在时，优先报分值差额',
  )
})

test('无基准(L4)时不得凭空给出"要求"值', () => {
  const ledger = _ledger({
    basisLevel: 'l4_none',
    expectedTotal: null,
    totalGap: null,
    extractedTotal: 88,
  })
  const line = ledgerTotalsLine(ledger)
  assert.equal(line, '已提取 88 分')
  assert.doesNotMatch(line, /未在招标文件中找到总分要求/)
  assert.doesNotMatch(line, /要求 \d/, '无基准时禁止编造要求分值')
})

// =====: 自洽但基准不可靠 → 提醒（绿色+蓝色 info），非警告（琥珀） =====

test('doc1925 形态判为自洽——分值闭合且唯一原因是基准推算', () => {
  const ledger = _ledger({
    basisLevel: 'l2_derived_group',
    basisLowConfidence: true,
    extractedTotal: 100,
    totalGap: 0,
  })
  assert.equal(isLedgerSelfConsistentWithSoftBasis(ledger), true)
})

test('存在真实分值差额时不判自洽（必须留在警告态）', () => {
  assert.equal(isLedgerSelfConsistentWithSoftBasis(_ledger()), false)
})

test('卷级缺口/多方案/未归卷/证据不足任一存在即不判自洽', () => {
  const base = { extractedTotal: 100, totalGap: 0, basisLowConfidence: true }
  assert.equal(
    isLedgerSelfConsistentWithSoftBasis(
      _ledger({
        ...base,
        groupRows: [{ label: '技术标', declared: 60, extractedSum: 50, gap: -10 }],
      }),
    ),
    false,
  )
  assert.equal(
    isLedgerSelfConsistentWithSoftBasis(_ledger({ ...base, multiSchemeSignal: '检出2套' })),
    false,
  )
  assert.equal(
    isLedgerSelfConsistentWithSoftBasis(_ledger({ ...base, unlabeledSum: 8 })),
    false,
  )
  assert.equal(
    isLedgerSelfConsistentWithSoftBasis(_ledger({ ...base, insufficiencyCount: 2 })),
    false,
  )
})

test('L4 无任何基准时不得宣称自洽（无从验证闭合）', () => {
  assert.equal(
    isLedgerSelfConsistentWithSoftBasis(
      _ledger({ basisLevel: 'l4_none', expectedTotal: null, totalGap: null }),
    ),
    false,
  )
})

test('缺账本报告时不判自洽', () => {
  assert.equal(isLedgerSelfConsistentWithSoftBasis(null), false)
})

test('六类原因文案两两不同，避免退化为同一句', () => {
  const reasons = [
    'total_gap',
    'group_gap',
    'multi_scheme',
    'unlabeled',
    'insufficiency',
    'low_confidence',
    'other',
  ] as const
  const texts = reasons.map((r) => ledgerReviewHeadline(r))
  assert.equal(new Set(texts).size, texts.length, '每类原因必须有独立文案')
  assert.ok(texts.every((t) => t.length > 0))
})

test('账本基准或差额缺失时不判假差额', () => {
  const report = _foldedReport()
  if (!report) return
  const summary = buildScoringTotalsSummary(report)

  assert.equal(
    isLedgerGapFoldingArtifact(summary.folded, _ledger({ expectedTotal: null })),
    false,
  )
  assert.equal(isLedgerGapFoldingArtifact(summary.folded, _ledger({ totalGap: null })), false)
})

// ===== transformRequirement + scoringMetadata =====

test('transformRequirement 映射 scoring_metadata 中的评分项结构化字段', () => {
  const raw = {
    id: 42,
    category: 'TECH',
    description: '技术评分项',
    is_mandatory: false,
    confirmed: false,
    document_id: 1,
    requirement_kind: 'scoring_item',
    score_weight: 20,
    scoring_metadata: {
      score_item_name: '技术方案',
      score_rules: ['优秀: 20分', '良好: 15分'],
      deduction_rules: ['缺少图纸扣5分'],
      volume: 'technical',
      volume_label: '技术方案评审',
      lot: '采购包1',
      volume_weight: 0.5,
      volume_max: 100,
      authoritative_description: '技术方案评分原文',
      fidelity_flag: 'ok',
      fidelity_note: null,
      fidelity_overlap: null,
    },
  }
  const req = transformRequirement(raw as Record<string, unknown>)
  assert.notEqual(req.scoringMetadata, null, 'scoringMetadata 不应为 null')
  assert.equal(req.scoringMetadata?.scoreItemName, '技术方案')
  assert.deepEqual(req.scoringMetadata?.scoreRules, ['优秀: 20分', '良好: 15分'])
  assert.deepEqual(req.scoringMetadata?.deductionRules, ['缺少图纸扣5分'])
  assert.equal(req.scoringMetadata?.volume, 'technical')
  assert.equal(req.scoringMetadata?.volumeLabel, '技术方案评审')
  assert.equal(req.scoringMetadata?.lot, '采购包1')
  assert.equal(req.scoringMetadata?.volumeWeight, 0.5)
  assert.equal(req.scoringMetadata?.volumeMax, 100)
  assert.equal(req.scoringMetadata?.authoritativeDescription, '技术方案评分原文')
  assert.equal(req.scoringMetadata?.fidelityFlag, 'ok')
  assert.equal(req.scoringMetadata?.fidelityNote, null)
  assert.equal(req.scoringMetadata?.suspectedAssessmentForm, false)
})

test('transformRequirement scoring_metadata 缺失时 scoringMetadata 为 null', () => {
  const raw = {
    id: 1,
    category: 'TECH',
    description: 'desc',
    is_mandatory: false,
    confirmed: false,
    document_id: 1,
    requirement_kind: 'normal',
    score_weight: null,
    scoring_metadata: null,
  }
  const req = transformRequirement(raw as Record<string, unknown>)
  assert.equal(req.scoringMetadata, null, 'scoring_metadata 为 null 时应映射为 null')
})

test('transformRequirement scoring_metadata 缺失规则列表时回退空数组', () => {
  const raw = {
    id: 3,
    category: 'TECH',
    description: 'desc',
    is_mandatory: false,
    confirmed: false,
    document_id: 1,
    requirement_kind: 'scoring_item',
    score_weight: 10,
    scoring_metadata: { score_item_name: '仅名称', volume: 'technical' },
  }
  const req = transformRequirement(raw as Record<string, unknown>)
  assert.deepEqual(req.scoringMetadata?.scoreRules, [], '无 score_rules 时应为空数组')
  assert.deepEqual(req.scoringMetadata?.deductionRules, [], '无 deduction_rules 时应为空数组')
})

// ===== formatScoringRules =====

test('formatScoringRules 非空列表返回含关键词的字符串', () => {
  const rules = ['优秀: 20分', '良好: 15分', '合格: 10分']
  const formatted = formatScoringRules(rules)
  assert.ok(formatted.includes('优秀'), '应包含第一条规则文本')
  assert.ok(formatted.includes('20分'), '应包含分值文本')
  assert.ok(formatted.length > 0, '非空列表应产生非空字符串')
})

test('formatScoringRules 空列表返回空字符串', () => {
  assert.equal(formatScoringRules([]), '', '空规则列表应返回空字符串')
})

// ===== formatScore 整数去小数 =====
test('formatScore 整数去小数、非整数去尾随 0、非法值兜底 0', () => {
  assert.equal(formatScore(100), '100')
  assert.equal(formatScore(100.0), '100')
  assert.equal(formatScore(99.5), '99.5')
  assert.equal(formatScore(62.5), '62.5')
  assert.equal(formatScore(Number.NaN), '0')
})

// ===== volumeCheckHint (: 方向性归因，高于/低于基准不能混用同一句话) =====

test('volumeCheckHint 达标时返回"正常"', () => {
  assert.equal(volumeCheckHint({ sum: 100, expected: 100, weight: 1, within: true }), '，正常')
})

test('volumeCheckHint 低于基准时提示"漏识别"，不得提示"重复提取"', () => {
  const hint = volumeCheckHint({ sum: 82, expected: 100, weight: 1, within: false })
  assert.match(hint, /漏识别/)
  assert.doesNotMatch(hint, /重复提取/)
})

test('volumeCheckHint 高于基准时提示"重复提取/识别错误"，不得提示"漏识别"（真实回归：103≠100）', () => {
  const hint = volumeCheckHint({ sum: 103, expected: 100, weight: 1, within: false })
  assert.match(hint, /重复提取/)
  assert.doesNotMatch(hint, /漏识别/)
})

