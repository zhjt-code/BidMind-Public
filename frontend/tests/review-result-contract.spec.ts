import assert from 'node:assert/strict'
import test from 'node:test'

import {
  formatScoringRuleText,
  getScoringStatusLabel,
} from '../src/pages/result/scoringAssessmentDisplay.ts'
import { resolveResponseDisplay } from '../src/pages/result/responseDisplay.ts'
import { transformResult, transformSummary } from '../src/pages/result/types.ts'

test('transformResult 应映射可复核原始字段与结构化列表', () => {
  const result = transformResult({
    id: 1,
    category: 'technical',
    requirement_summary: '技术方案完整',
    requirement_description: '投标人需提供完整技术实施方案与质量控制措施',
    response_summary: '已提供方案',
    response_content: '已提供实施方案，但未说明质量控制机制',
    compliance_level: 'partially',
    score: 75,
    comment: '质量控制措施不足，需补充说明',
    risk_level: 'medium',
    source_pages: '12,13',
    source_page_numbers: [12, 13],
    requirement_source_pages: [6, 7],
    requirement_keywords: ['技术方案', '质量控制'],
    requirement_sub_requirements: ['实施路径', '质量控制'],
    response_completeness: 'partial',
    response_missing_elements: ['质量控制机制'],
    response_linked_to: 8,
    response_evidence_anchor: 'TECH-2026-001',
    consistency_flag: 'needs_review',
    reconcile_note: '缺少子项覆盖，需人工复核',
  })

  assert.equal(result.requirementDescription, '投标人需提供完整技术实施方案与质量控制措施')
  assert.equal(result.responseContent, '已提供实施方案，但未说明质量控制机制')
  assert.deepEqual(result.sourcePageNumbers, [12, 13])
  assert.deepEqual(result.requirementSourcePages, [6, 7])
  assert.deepEqual(result.requirementKeywords, ['技术方案', '质量控制'])
  assert.deepEqual(result.responseMissingElements, ['质量控制机制'])
  assert.equal(result.responseLinkedTo, 8)
  assert.equal(result.responseEvidenceAnchor, 'TECH-2026-001')
  assert.equal(result.consistencyFlag, 'needs_review')
})

test('transformResult 应映射投标信封来源字段(分卷徽标与按文档页码定位)', () => {
  const result = transformResult({
    id: 3,
    category: 'commercial',
    requirement_summary: '付款条件响应',
    response_summary: '已响应',
    compliance_level: 'fully',
    score: 90,
    comment: '符合',
    risk_level: 'none',
    source_pages: '5,12',
    source_page_numbers: [5, 12],
    source_document_id: 11,
    source_parts: ['commercial', 'technical'],
    source_page_locations: [
      { document_id: 11, part: 'commercial', page: 5 },
      { document_id: 12, part: 'technical', page: 12 },
    ],
  })

  assert.equal(result.sourceDocumentId, 11)
  assert.deepEqual(result.sourceParts, ['commercial', 'technical'])
  assert.deepEqual(result.sourcePageLocations, [
    { documentId: 11, part: 'commercial', page: 5 },
    { documentId: 12, part: 'technical', page: 12 },
  ])
  assert.deepEqual(result.sourcePageExcerpts, [])
})

test('transformResult 映射页级摘录供预览高亮(同形态: 整本 snippets)', () => {
  const result = transformResult({
    id: 5,
    category: 'technical',
    requirement_summary: '业绩要求',
    response_summary: '已响应',
    compliance_level: 'fully',
    score: 90,
    comment: '符合',
    risk_level: 'none',
    source_pages: '5',
    source_page_numbers: [5],
    source_page_excerpts: [
      { document_id: 21, page: 5, excerpt: '整本投标文件业绩一览表' },
    ],
  })
  assert.deepEqual(result.sourcePageExcerpts, [
    { documentId: 21, page: 5, excerpt: '整本投标文件业绩一览表' },
  ])
})

test('transformResult 整本/历史数据信封字段回退为空(前端走单文档路径)', () => {
  const result = transformResult({
    id: 4,
    category: 'technical',
    requirement_summary: '技术要求',
    response_summary: '已响应',
    compliance_level: 'fully',
    score: 90,
    comment: '符合',
    risk_level: 'none',
    source_pages: '3',
    source_page_numbers: [3],
  })

  assert.equal(result.sourceDocumentId, undefined)
  assert.deepEqual(result.sourceParts, [])
  assert.deepEqual(result.sourcePageLocations, [])
})

test('transformResult 应清洗用户可见审查意见和逐档判定原因', () => {
  const result = transformResult({
    id: 2,
    category: 'technical',
    requirement_summary: '资质要求',
    response_summary: '未提供相关证书',
    compliance_level: 'not',
    score: 10,
    comment: '未提供建筑资质，relevance_score=10，score=10。',
    risk_level: 'high',
    reconcile_note: '根据 Golden Rule6，relevance_score: 10，需人工复核',
    response_missing_elements: ['缺少证书，relevance_score=10'],
    rule_assessments: [
      {
        rule_excerpt: '提供资质证书',
        status: 'not',
        points: 0,
        reason: '未命中，relevance_score=10，score=0',
        evidence_pages: [],
      },
    ],
  })

  assert.ok(!result.comment.toLowerCase().includes('relevance_score'))
  assert.ok(result.comment.includes('相关性评分10'))
  assert.ok(!result.comment.toLowerCase().includes('score='))
  assert.ok(!result.reconcileNote?.toLowerCase().includes('golden rule'))
  assert.ok(!result.reconcileNote?.toLowerCase().includes('relevance_score'))
  assert.ok(!result.responseMissingElements?.join(' ').toLowerCase().includes('relevance_score'))
  assert.ok(!result.ruleAssessments?.[0]?.reason?.toLowerCase().includes('relevance_score'))
})

test('transformResult 应剥除审查意见中的账本诊断串与要求主键', () => {
  const result = transformResult({
    id: 3,
    category: 'qualification',
    requirement_summary: '法定代表人身份证明',
    response_summary: '缺开户许可证',
    compliance_level: 'not',
    score: 0,
    comment:
      '投标文件第6页提供了身份证。项目证据账本已登记「法定代表人身份证复印件」（第44页，来自要求#43177、#43175），本条未发现结论与账本冲突，待复核',
    risk_level: 'high',
    consistency_flag: 'cross_conflict',
    reconcile_note:
      '项目证据账本已登记「营业执照」（第608页，来自要求#88），本条未发现结论与账本冲突，待复核',
  })

  assert.ok(result.comment.includes('第6页'))
  assert.ok(!result.comment.includes('项目证据账本'))
  assert.ok(!result.comment.includes('#43177'))
  assert.ok(!result.reconcileNote?.includes('项目证据账本'))
  assert.ok(!result.reconcileNote?.includes('#88'))
})

test('评分明细展示不应把对象型规则 JSON.stringify 给用户', () => {
  const ruleText = formatScoringRuleText({
    rule_excerpt: '提供建筑工程施工资质证书',
    internal_key: 'enterprise_qual',
  })

  assert.equal(ruleText, '提供建筑工程施工资质证书')
  assert.ok(!ruleText.includes('internal_key'))
  assert.equal(getScoringStatusLabel('unexpected_status'), '待定')
})

test('transformSummary 应优先消费 weighted_score 作为综合得分', () => {
  const summary = transformSummary({
    total_score: 210,
    average_score: 70,
    weighted_score: 82.5,
    conclusion: 'pass',
    conclusion_reason: '综合得分82.5分，符合要求',
    total_count: 3,
    compliant_count: 2,
    high_risk_count: 0,
  })

  assert.equal(summary.averageScore, 70)
  assert.equal(summary.weightedScore, 82.5)
  assert.equal(summary.conclusion, 'pass')
})

test('未确认命中应展示审查摘要而非检索原文，状态不作缺失要素', () => {
  const display = resolveResponseDisplay({
    responseSummary: '投标文件未对分包管理要求作出明确响应',
    responseContent: '【检索已命中待确认】\n目 录\n一、投标函 ........................ 1',
    responseMissingElements: ['检索已命中待确认'],
    relevanceNote: '检索已命中待确认',
  })

  assert.equal(display.body, '投标文件未对分包管理要求作出明确响应')
  assert.equal(display.statusLabel, '相关材料待核对')
  assert.equal(display.hint, '以下为审查摘要，如需核实请点击相关页码查看原文')
  assert.ok(!display.hint.includes('已找到'))
  assert.ok(!display.statusLabel.includes('检索'))
  assert.deepEqual(display.missingElements, [])
})

test('扫描件摘要包同样不得作为投标响应正文', () => {
  const dump = '【检索已命中待确认】\n[投标证明材料] 第59页提供了合同扫描件：甲方某某公司'
  const display = resolveResponseDisplay({
    responseSummary: dump,
    responseContent: dump,
    responseMissingElements: ['检索已命中待确认', '授权委托书'],
  })

  assert.equal(display.body, '相关材料待核对')
  assert.ok(!display.body.includes('扫描件'))
  assert.ok(!display.body.includes('检索'))
  assert.deepEqual(display.missingElements, ['授权委托书'])
})

test('已确认响应优先展示审查摘要', () => {
  const display = resolveResponseDisplay({
    responseSummary: '已提供一级资质证书',
    responseContent: '投标人持有建筑工程施工总承包一级资质证书，编号 A123',
  })

  assert.equal(display.body, '已提供一级资质证书')
  assert.equal(display.statusLabel, null)
  assert.equal(display.hint, null)
})
