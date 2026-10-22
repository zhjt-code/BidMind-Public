/**
 * 防御测试：风险告知独立容器与折叠拆分（P1.4 + 布局分层）。
 *
 * 锁定不变量：
 * 红线1: isMandatory=true 的折叠项必须拆入独立风险组（: 否决语义不可混排隐藏）；
 * 红线2: 普通参考条款不得混入风险组；
 * 红线3: 风险组内按 id 升序稳定排列；原数组不被原地修改；
 * 红线4: RiskNoticeSection 必须独立渲染风险标题与否决语义徽标；
 * 红线5: FoldedRequirementsSection 不得再混入风险告知（防止双份展示或回退隐藏）。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

import { splitFoldedRequirements } from '../src/pages/RequirementReview/helpers.ts'
import type { Requirement } from '../src/pages/requirementReviewTransforms.ts'

const makeReq = (overrides: Partial<Requirement> = {}): Requirement => ({
  id: 1,
  category: 'COMM',
  description: 'desc',
  isMandatory: false,
  confirmed: false,
  documentId: null,
  sourcePage: null,
  sourcePages: [],
  mergedCount: null,
  subRequirements: [],
  reviewRelevance: 'procedural',
  relevanceReason: null,
  requirementKind: 'normal',
  scoreWeight: null,
  isKeyReview: false,
  memberIds: [1],
  ...overrides,
})

test('splitFoldedRequirements 否决语义折叠项进入独立风险组', () => {
  const items: Requirement[] = [
    makeReq({ id: 1, isMandatory: false }),
    makeReq({ id: 2, isMandatory: true, description: '禁止串通投标' }),
    makeReq({ id: 3, isMandatory: false }),
    makeReq({ id: 4, isMandatory: true, description: '禁止挂靠转包' }),
  ]
  const { riskItems, referenceItems } = splitFoldedRequirements(items)
  assert.deepEqual(riskItems.map((r) => r.id), [2, 4], '否决折叠项必须全部进入风险组')
  assert.deepEqual(referenceItems.map((r) => r.id), [1, 3], '普通参考条款不得混入风险组')
})

test('splitFoldedRequirements 风险组按 id 升序且不改原数组', () => {
  const items: Requirement[] = [
    makeReq({ id: 30, isMandatory: true }),
    makeReq({ id: 10, isMandatory: true }),
    makeReq({ id: 20, isMandatory: false }),
  ]
  const snapshot = items.map((r) => r.id)
  const { riskItems } = splitFoldedRequirements(items)
  assert.deepEqual(riskItems.map((r) => r.id), [10, 30], '风险组内必须按 id 升序')
  assert.deepEqual(items.map((r) => r.id), snapshot, '原数组顺序必须保留')
})

test('splitFoldedRequirements 空输入返回双空组', () => {
  const { riskItems, referenceItems } = splitFoldedRequirements([])
  assert.deepEqual(riskItems, [])
  assert.deepEqual(referenceItems, [])
})

test('RiskNoticeSection 源码渲染独立风险分组与否决徽标', () => {
  const source = readFileSync(
    join(
      import.meta.dirname,
      '../src/pages/RequirementReview/components/RiskNoticeSection.tsx',
    ),
    'utf-8',
  )
  const rowSource = readFileSync(
    join(
      import.meta.dirname,
      '../src/pages/RequirementReview/components/FoldedRequirementRow.tsx',
    ),
    'utf-8',
  )
  assert.ok(source.includes('风险告知（不参与审查，建议核对）'), '必须渲染独立风险容器标题')
  assert.ok(source.includes('FoldedRequirementRow'), '风险条款必须复用折叠行组件')
  assert.ok(source.includes('risk'), '风险行必须以 risk 语义渲染')
  assert.ok(rowSource.includes('否决语义'), '风险条款必须带否决语义徽标')
  assert.ok(source.includes('串标审查'), '串通类风险须注明由串标审查功能交叉核验')
  assert.ok(source.includes('onViewed'), '展开或交互风险条款时必须上报已查看')
})

test('FoldedRequirementsSection 不得再混入风险告知', () => {
  const source = readFileSync(
    join(
      import.meta.dirname,
      '../src/pages/RequirementReview/components/FoldedRequirementsSection.tsx',
    ),
    'utf-8',
  )
  assert.ok(!source.includes('splitFoldedRequirements'), '参考区不得再拆分风险组')
  assert.doesNotMatch(
    source,
    /风险告知（不参与审查|含否决语义）/,
    '参考区不得再渲染风险告知标题',
  )
  assert.ok(source.includes('groupRequirementsByCategory'), '参考区仍应按分类聚合普通条款')
})
