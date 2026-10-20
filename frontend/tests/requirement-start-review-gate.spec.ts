/**
 * 防御测试：确认审查前未查看风险告知的兜底闸门契约。
 *
 * 锁定不变量：
 *   红线1: buildRiskNoticeSummary 仅基于风险项；空风险 hasRisk=false；
 *   红线2: 主页面必须在有风险且未查看时打开确认弹窗；
 *   红线3: 弹窗必须提供「返回查看」与「继续开始审查」出口。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

import { buildRiskNoticeSummary } from '../src/pages/RequirementReview/helpers.ts'
import type { Requirement } from '../src/pages/requirementReviewTransforms.ts'

const makeReq = (overrides: Partial<Requirement> = {}): Requirement => ({
  id: 1,
  category: 'COMM',
  description: 'desc',
  isMandatory: true,
  confirmed: false,
  documentId: null,
  sourcePage: null,
  sourcePages: [],
  mergedCount: null,
  subRequirements: [],
  reviewRelevance: 'procedural',
  relevanceReason: 'behavioral_mandatory',
  requirementKind: 'normal',
  scoreWeight: null,
  isKeyReview: false,
  memberIds: [1],
  ...overrides,
})

test('buildRiskNoticeSummary 有风险项时 hasRisk=true 且 total 正确', () => {
  const summary = buildRiskNoticeSummary([
    makeReq({ id: 1 }),
    makeReq({ id: 2 }),
  ])
  assert.equal(summary.total, 2)
  assert.equal(summary.hasRisk, true)
})

test('buildRiskNoticeSummary 空风险时不需要拦截', () => {
  const summary = buildRiskNoticeSummary([])
  assert.equal(summary.total, 0)
  assert.equal(summary.hasRisk, false)
})

test('ConfirmStartReviewDialog 源码含条数与返回/继续出口', () => {
  const source = readFileSync(
    join(
      import.meta.dirname,
      '../src/pages/RequirementReview/components/ConfirmStartReviewDialog.tsx',
    ),
    'utf-8',
  )
  assert.ok(source.includes('riskCount'), '弹窗必须接收风险条数')
  assert.ok(source.includes('返回查看'), '必须提供返回查看出口')
  assert.ok(source.includes('继续开始审查'), '必须提供继续开始审查出口')
})

test('RequirementReview 主页面接线未查看风险闸门', () => {
  const source = readFileSync(
    join(import.meta.dirname, '../src/pages/RequirementReview/index.tsx'),
    'utf-8',
  )
  assert.ok(source.includes('riskNoticeViewed'), '必须持有本会话已查看状态')
  assert.ok(source.includes('openStartReviewConfirm'), '确认入口必须经闸门函数')
  assert.ok(source.includes('ConfirmStartReviewDialog'), '必须挂载确认兜底弹窗')
  assert.ok(source.includes('RiskNoticeSection'), '必须挂载独立风险告知容器')
})
