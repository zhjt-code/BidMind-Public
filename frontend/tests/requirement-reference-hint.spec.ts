/**
 * 防御测试：主表分类标题「参考 N」弱提示计数契约。
 *
 * 锁定不变量：
 *   红线1: countReferenceByCategory 只统计普通参考条款，按 getCategoryLabel 归一；
 *   红线2: 风险项（isMandatory 折叠项）不得计入参考 N；
 *   红线3: RequirementTable 弱链接必须 stopPropagation，避免误触主表折叠。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

import {
  countReferenceByCategory,
  splitFoldedRequirements,
} from '../src/pages/RequirementReview/helpers.ts'
import type { Requirement } from '../src/pages/requirementReviewTransforms.ts'

const makeReq = (overrides: Partial<Requirement> = {}): Requirement => ({
  id: 1,
  category: 'QUAL',
  description: 'desc',
  isMandatory: false,
  confirmed: false,
  documentId: null,
  sourcePage: null,
  sourcePages: [],
  mergedCount: null,
  subRequirements: [],
  reviewRelevance: 'informational',
  relevanceReason: null,
  requirementKind: 'normal',
  scoreWeight: null,
  isKeyReview: false,
  memberIds: [1],
  ...overrides,
})

test('countReferenceByCategory 按分类标签聚合普通参考条款', () => {
  const items: Requirement[] = [
    makeReq({ id: 1, category: 'QUAL' }),
    makeReq({ id: 2, category: 'qualification' }),
    makeReq({ id: 3, category: 'TECH' }),
  ]
  const counts = countReferenceByCategory(items)
  assert.equal(counts['资质要求'], 2, 'QUAL 与 qualification 必须归一为资质要求')
  assert.equal(counts['技术要求'], 1)
})

test('countReferenceByCategory 空输入返回空对象', () => {
  assert.deepEqual(countReferenceByCategory([]), {})
})

test('风险项不得计入参考 N（先拆分再计数）', () => {
  const folded: Requirement[] = [
    makeReq({ id: 1, category: 'QUAL', isMandatory: false }),
    makeReq({ id: 2, category: 'QUAL', isMandatory: true, reviewRelevance: 'procedural' }),
    makeReq({ id: 3, category: 'TECH', isMandatory: false }),
  ]
  const { riskItems, referenceItems } = splitFoldedRequirements(folded)
  assert.equal(riskItems.length, 1)
  const counts = countReferenceByCategory(referenceItems)
  assert.equal(counts['资质要求'], 1, '风险项不得计入资质要求参考计数')
  assert.equal(counts['技术要求'], 1)
  assert.equal(counts['资质要求'] + (counts['技术要求'] ?? 0), 2)
})

test('RequirementTable 弱提示链接含 stopPropagation', () => {
  const source = readFileSync(
    join(
      import.meta.dirname,
      '../src/pages/RequirementReview/components/RequirementTable.tsx',
    ),
    'utf-8',
  )
  assert.ok(source.includes('referenceCountsByCategory'), '必须接收参考条款分类计数')
  assert.ok(source.includes('onJumpToReference'), '必须提供跳转回调')
  assert.ok(source.includes('参考'), '必须渲染参考弱提示文案')
  assert.ok(source.includes('stopPropagation'), '点击参考提示不得触主表分组折叠')
  assert.ok(
    source.includes('rounded-full') && source.includes('border-slate-200'),
    '参考提示须为可见胶囊样式，避免灰字隐没',
  )
  assert.ok(source.includes('expandedGroups'), '分类分组须以 expandedGroups 控制，默认折叠')
})
