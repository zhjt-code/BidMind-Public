import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildReviewFocusSections,
  buildReviewHeaderMetrics,
  getReviewDecisionSummaryText,
  SCORING_ESTIMATE_NOTE,
} from '../src/pages/result/reviewStatsOverview.ts'

const baseSummary = {
  totalScore: 0,
  averageScore: 0,
  weightedScore: 66.4,
  conclusion: 'fail' as const,
  conclusionReason: '存在高风险不符合项',
  totalCount: 96,
  compliantCount: 62,
  highRiskCount: 22,
  scoringItemCount: 16,
  estimatedItemCount: 16,
  estimatedTotalScore: 64,
  maxTotalScore: 100,
  estimatedScoreByVolume: {
    技术标: { estimated: 64, max: 100, count: 16, weight: 0.4 },
  },
  scoringTotalReliable: true,
  estimatedScoreByLot: {},
}

const results = [
  { category: 'TECH', score: 52, complianceLevel: 'not', riskLevel: 'high' },
  { category: 'COMM', score: 68, complianceLevel: 'partially', riskLevel: 'medium' },
  { category: 'PERF', score: 80, complianceLevel: 'fully', riskLevel: 'none' },
  { category: 'FORMAT', score: 45, complianceLevel: 'missing', riskLevel: 'high' },
]

type FocusItemLike = string | { text: string; children?: string[] }

function focusItemText(item: FocusItemLike): string {
  return typeof item === 'string' ? item : item.text
}

function focusItemTexts(items: FocusItemLike[]): string[] {
  return items.flatMap(item => [
    focusItemText(item),
    ...(typeof item === 'string' ? [] : item.children ?? []),
  ])
}

function requireStructuredItem(item: FocusItemLike | undefined): { text: string; children?: string[] } {
  assert.ok(item && typeof item !== 'string')
  return item
}

test('投标审查顶部指标应只保留轻量概览, 不重复承载最终结论', () => {
  assert.deepEqual(buildReviewHeaderMetrics(baseSummary), [
    '96 项要求',
    '符合 62 项',
    '高风险 22 项',
    '评分项 16 项',
  ])
})

test('投标审查最终结论文案应按审查结论给出下一步重点', () => {
  assert.equal(
    getReviewDecisionSummaryText(baseSummary),
    '当前项目存在高风险不符合项，建议优先核查高风险与不符合要求。'
  )
  assert.equal(
    getReviewDecisionSummaryText({ ...baseSummary, conclusion: 'pass', highRiskCount: 0 }),
    '当前项目未发现高风险不符合项，建议抽查关键要求与评分项依据。'
  )
})

test('投标审查重点应拆分主要风险、响应薄弱分类、预估评标得分和使用限制', () => {
  const sections = buildReviewFocusSections({
    summary: baseSummary,
    results,
    extractionWarnings: ['部分招标内容可能未被完整识别。'],
  })

  assert.deepEqual(sections.map(section => section.title), [
    '主要风险',
    '响应薄弱分类',
    '预估评标得分',
    '使用限制',
  ])

  assert.equal(focusItemTexts(sections[0].items).some(item => item.includes('22 项高风险')), true)
  assert.equal(focusItemTexts(sections[1].items).some(item => item.includes('格式类响应偏弱')), true)
  assert.equal(focusItemTexts(sections[1].items).some(item => item.includes('/100')), false)
  assert.equal(focusItemTexts(sections[1].items).some(item => item.includes('共 1 项，其中 1 项未响应或不符合')), true)
  assert.equal(focusItemTexts(sections[2].items).some(item => item.includes('64 / 100')), true)
  assert.equal(focusItemTexts(sections[3].items).some(item => item.includes('未被完整识别')), true)
})

test('待判定不得计入不符合或未响应', () => {
  const sections = buildReviewFocusSections({
    summary: { ...baseSummary, highRiskCount: 0, conclusion: 'pass' },
    results: [
      { category: 'QUAL', score: 0, complianceLevel: 'missing', riskLevel: 'medium', pendingJudgment: true },
      { category: 'TECH', score: 80, complianceLevel: 'fully', riskLevel: 'none' },
    ],
  })
  const risk = sections.find(section => section.title === '主要风险')
  assert.ok(risk)
  assert.equal(
    focusItemTexts(risk.items).some(item => item.includes('不符合或未响应')),
    false,
  )
})

test('投标审查使用限制会把旧废标信号提示改写成招标要求核对说明', () => {
  const sections = buildReviewFocusSections({
    summary: baseSummary,
    results,
    extractionWarnings: [
      '有 2 处废标/无效投标信号未能对应到独立审查条件（涉及第 20、23 页），请对照原文核对否决项是否漏提或被合并',
    ],
  })
  const limitation = sections.find((section) => section.title === '使用限制')
  assert.ok(limitation)
  assert.equal(
    focusItemTexts(limitation!.items).some((item) =>
      item.includes('还没有提取成单独的一条招标要求'),
    ),
    true,
  )
  assert.equal(
    focusItemTexts(limitation!.items).some((item) => item.includes('独立审查条件')),
    false,
  )
})

test('无分包预估评标得分应展示为总分主项和缩进分项, 不强行写分包名称', () => {
  const sections = buildReviewFocusSections({
    summary: baseSummary,
    results,
    extractionWarnings: [],
  })
  const scoring = sections.find(section => section.title === '预估评标得分')
  assert.ok(scoring)

  const scoreItem = requireStructuredItem(scoring!.items[0])
  assert.equal(scoreItem.text, '64 / 100')
  assert.deepEqual(scoreItem.children, [
    '技术标 64/100（16项 · 权重40%）',
  ])
  assert.equal(focusItemTexts(scoring!.items).some(item => item.includes('分包一')), false)
})

test('预估评标得分免责说明应作为板块级通用注释, 不进入编号条目', () => {
  const sections = buildReviewFocusSections({
    summary: baseSummary,
    results,
    extractionWarnings: [],
  })
  const scoring = sections.find(section => section.title === '预估评标得分')
  assert.ok(scoring)
  assert.equal(scoring!.note, SCORING_ESTIMATE_NOTE)
  assert.equal(focusItemTexts(scoring!.items).some(item => item.includes('按招标评分表估算')), false)

  const pendingSections = buildReviewFocusSections({
    summary: { ...baseSummary, estimatedItemCount: 0 },
    results,
    extractionWarnings: [],
  })
  const pendingScoring = pendingSections.find(section => section.title === '预估评标得分')
  assert.ok(pendingScoring)
  assert.equal(pendingScoring!.note, SCORING_ESTIMATE_NOTE)

  const unreliableSections = buildReviewFocusSections({
    summary: { ...baseSummary, scoringTotalReliable: false },
    results,
    extractionWarnings: [],
  })
  const unreliableScoring = unreliableSections.find(section => section.title === '预估评标得分')
  assert.ok(unreliableScoring)
  assert.equal(unreliableScoring!.note, SCORING_ESTIMATE_NOTE)
  assert.equal(focusItemTexts(unreliableScoring!.items).some(item => item.includes('按招标评分表估算')), false)
})

test('多采购包应按包播报预估得分, 禁止误报"评分结构较复杂"', () => {
  // 项目1554 实况: 两包各100分制, 修复前被混算成 200 → 错误降级"结构复杂"
  const multiLotSummary = {
    ...baseSummary,
    scoringItemCount: 35,
    estimatedItemCount: 35,
    estimatedTotalScore: 36.5,
    maxTotalScore: 200,
    scoringTotalReliable: false,
    estimatedScoreByVolume: {
      '采购包1·技术标': { estimated: 0, max: 70, count: 13 },
      '采购包1·价格': { estimated: 30, max: 30, count: 1 },
      '采购包2·技术标': { estimated: 6.5, max: 90, count: 20 },
      '采购包2·价格': { estimated: 0, max: 10, count: 1 },
    },
    estimatedScoreByLot: {
      采购包1: {
        estimated: 30, max: 100, count: 14, estimatedCount: 14,
        reliable: true, unassigned: false,
        byVolume: { 技术标: { estimated: 0, max: 70, count: 13 }, 价格: { estimated: 30, max: 30, count: 1 } },
      },
      采购包2: {
        estimated: 6.5, max: 100, count: 21, estimatedCount: 21,
        reliable: true, unassigned: false,
        byVolume: { 技术标: { estimated: 6.5, max: 90, count: 20 }, 价格: { estimated: 0, max: 10, count: 1 } },
      },
    },
  }
  const sections = buildReviewFocusSections({
    summary: multiLotSummary,
    results,
    extractionWarnings: [],
  })
  const scoring = sections.find(section => section.title === '预估评标得分')
  assert.ok(scoring)
  const items = scoring!.items
  assert.equal(focusItemTexts(items).some(item => item.includes('按采购包分别评审')), true)
  const lot1 = requireStructuredItem(items.find(item => focusItemText(item).startsWith('采购包1：')))
  const lot2 = requireStructuredItem(items.find(item => focusItemText(item).startsWith('采购包2：')))
  assert.equal(lot1.text, '采购包1：预估评标得分 30 / 100（14项）')
  assert.deepEqual(lot1.children, [
    '技术标 0/70（13项）',
    '价格 30/30（1项）',
  ])
  assert.equal(lot2.text, '采购包2：预估评标得分 6.5 / 100（21项）')
  assert.equal(lot2.children?.some(item => item.includes('技术标 6.5/90')), true)
  // 修复核心: 不得再出现跨包混算的错误归因与"200 分制"统一总分
  assert.equal(focusItemTexts(items).some(item => item.includes('评分结构较复杂（含多卷/加权/非100分制）')), false)
  assert.equal(focusItemTexts(items).some(item => item.includes('/ 200')), false)
})

test('未识别归属桶须警示播报; 单包结构复杂仅降级该包', () => {
  const summaryWithIssues = {
    ...baseSummary,
    scoringTotalReliable: false,
    estimatedScoreByLot: {
      采购包1: {
        estimated: 70, max: 100, count: 10, estimatedCount: 10,
        reliable: true, unassigned: false,
        byVolume: { 技术标: { estimated: 70, max: 100, count: 10 } },
      },
      采购包2: {
        estimated: 90, max: 150, count: 8, estimatedCount: 8,
        reliable: false, unassigned: false,
        byVolume: { 技术标: { estimated: 90, max: 150, count: 8 } },
      },
      未识别归属: {
        estimated: 3, max: 5, count: 1, estimatedCount: 1,
        reliable: false, unassigned: true,
        byVolume: { 技术标: { estimated: 3, max: 5, count: 1 } },
      },
    },
  }
  const sections = buildReviewFocusSections({
    summary: summaryWithIssues,
    results,
    extractionWarnings: [],
  })
  const scoring = sections.find(section => section.title === '预估评标得分')
  assert.ok(scoring)
  const items = scoring!.items
  assert.equal(focusItemTexts(items).some(item => item.includes('采购包1：预估评标得分 70 / 100')), true)
  assert.equal(focusItemTexts(items).some(item => item.includes('采购包2：评分结构较复杂，暂不汇总该包总分')), true)
  assert.equal(
    focusItemTexts(items).some(item => item.includes('未识别采购包归属') && item.includes('请人工核对')),
    true,
  )
})

test('投标审查使用限制应常驻显示以保持稳定信息结构', () => {
  const sections = buildReviewFocusSections({
    summary: baseSummary,
    results,
    extractionWarnings: [],
  })

  assert.deepEqual(sections.map(section => section.title), [
    '主要风险',
    '响应薄弱分类',
    '预估评标得分',
    '使用限制',
  ])
  const limitation = sections.find(section => section.title === '使用限制')
  assert.ok(limitation)
  assert.equal(focusItemTexts(limitation!.items).some(item => item.includes('AI 辅助生成')), true)
  assert.equal(focusItemTexts(limitation!.items).some(item => item.includes('综合符合度用于衡量响应质量')), true)
})
