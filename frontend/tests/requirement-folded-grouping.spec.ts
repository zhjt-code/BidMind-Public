/**
 * 防御测试：参考条款折叠区分类聚合契约（groupRequirementsByCategory）。
 *
 * 锁定用户报告的三类展示问题修复后的不变量：
 *   红线1: 分组必须按分类优先级排序（QUAL>TECH>COMM>PERF>STAFF>FORMAT），不得混杂；
 *   红线2: 枚举 name 与 value（QUAL / qualification）归一后并入同一组，不得拆成两组；
 *   红线3: 组内按 id 升序稳定排列，避免后端返回顺序抖动导致展示漂移。
 * 同时保证未知分类垫底、原数组不被原地修改（浅拷贝契约）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import { groupRequirementsByCategory } from '../src/pages/RequirementReview/helpers.ts'
import type { Requirement } from '../src/pages/requirementReviewTransforms.ts'

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
  reviewRelevance: 'informational',
  relevanceReason: null,
  requirementKind: 'normal',
  scoreWeight: null,
  isKeyReview: false,
  memberIds: [1],
  ...overrides,
})

test('groupRequirementsByCategory 分组按分类优先级排序（不混杂）', () => {
  const items: Requirement[] = [
    makeReq({ id: 1, category: 'FORMAT' }),
    makeReq({ id: 2, category: 'COMM' }),
    makeReq({ id: 3, category: 'QUAL' }),
    makeReq({ id: 4, category: 'TECH' }),
  ]
  const groups = groupRequirementsByCategory(items)
  assert.deepEqual(
    groups.map((g) => g.label),
    ['资质要求', '技术要求', '商务要求', '格式要求'],
    '分组顺序应严格按分类优先级 QUAL>TECH>COMM>FORMAT',
  )
})

test('groupRequirementsByCategory 枚举 name 与 value 归一并入同一组', () => {
  const items: Requirement[] = [
    makeReq({ id: 1, category: 'QUAL' }),
    makeReq({ id: 2, category: 'qualification' }),
    makeReq({ id: 3, category: 'TECH' }),
  ]
  const groups = groupRequirementsByCategory(items)
  assert.equal(groups.length, 2, 'QUAL 与 qualification 必须合并为同一组')
  const qual = groups.find((g) => g.label === '资质要求')
  assert.ok(qual)
  assert.deepEqual(
    qual.items.map((r) => r.id),
    [1, 2],
    '同类不同枚举形式应归并到一组',
  )
})

test('groupRequirementsByCategory 组内按 id 升序稳定排列', () => {
  const items: Requirement[] = [
    makeReq({ id: 30, category: 'COMM' }),
    makeReq({ id: 10, category: 'COMM' }),
    makeReq({ id: 20, category: 'COMM' }),
  ]
  const groups = groupRequirementsByCategory(items)
  assert.equal(groups.length, 1)
  assert.deepEqual(
    groups[0].items.map((r) => r.id),
    [10, 20, 30],
    '组内必须按 id 升序，杜绝后端顺序抖动导致的展示漂移',
  )
})

test('groupRequirementsByCategory 未知分类用原标签垫底', () => {
  const items: Requirement[] = [
    makeReq({ id: 1, category: 'UNKNOWN_CAT' }),
    makeReq({ id: 2, category: 'QUAL' }),
  ]
  const groups = groupRequirementsByCategory(items)
  assert.equal(groups[0].label, '资质要求', '已知分类排在前')
  assert.equal(groups[groups.length - 1].label, 'UNKNOWN_CAT', '未知分类原样垫底')
})

test('groupRequirementsByCategory 不修改原数组（浅拷贝契约）', () => {
  const items: Requirement[] = [
    makeReq({ id: 1, category: 'FORMAT' }),
    makeReq({ id: 2, category: 'QUAL' }),
  ]
  const snapshot = items.map((r) => r.id)
  groupRequirementsByCategory(items)
  assert.deepEqual(items.map((r) => r.id), snapshot, '原数组顺序必须保留')
})

test('groupRequirementsByCategory 空输入返回空数组', () => {
  assert.deepEqual(groupRequirementsByCategory([]), [])
})
