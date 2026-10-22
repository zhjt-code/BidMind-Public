import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  COMPLIANCE_NAV_VALUES,
  PENDING_JUDGMENT_BADGE_CLASS,
  PENDING_JUDGMENT_COLOR,
  PENDING_JUDGMENT_DOT,
  PENDING_JUDGMENT_HEX,
  PENDING_JUDGMENT_FILTER,
  PENDING_JUDGMENT_LABEL,
  PENDING_JUDGMENT_TOOLTIP,
  complianceCountKey,
  complianceSortRank,
  getComplianceDisplayLabel,
  isPendingJudgment,
  isSubstantiveGap,
  matchesComplianceFilter,
} from '../src/pages/result/pendingJudgment.ts'
import { COMPLIANCE_COLORS, COMPLIANCE_ORDER, complianceLevelLabels, transformResult } from '../src/pages/result/types.ts'

test('超时兜底由 pending_judgment 识别为待判定，不靠已剥离的 comment 标记', () => {
  const result = transformResult({
    id: 1,
    category: 'qualification',
    requirement_summary: '资质证书',
    response_summary: '',
    compliance_level: 'missing',
    score: 0,
    comment: '审查失败：模型调用超时或输出不合规，请人工复核',
    risk_level: 'high',
    pending_judgment: true,
  })

  assert.equal(result.pendingJudgment, true)
  assert.equal(isPendingJudgment(result), true)
  assert.equal(complianceCountKey(result), PENDING_JUDGMENT_FILTER)
  assert.equal(getComplianceDisplayLabel(result, complianceLevelLabels), PENDING_JUDGMENT_LABEL)
  assert.equal(matchesComplianceFilter(result, 'missing'), false)
  assert.equal(matchesComplianceFilter(result, PENDING_JUDGMENT_FILTER), true)
  assert.equal(isSubstantiveGap(result), false)
})

test('提取失败同形态也进待判定，不进未响应', () => {
  const result = transformResult({
    id: 2,
    category: 'performance',
    requirement_summary: '业绩合同',
    response_summary: '',
    compliance_level: 'missing',
    score: 0,
    comment: '投标响应提取失败：输出不合规，请人工复核',
    risk_level: 'medium',
    pending_judgment: true,
  })

  assert.equal(complianceCountKey(result), PENDING_JUDGMENT_FILTER)
  assert.equal(matchesComplianceFilter(result, 'missing'), false)
  assert.equal(matchesComplianceFilter(result, PENDING_JUDGMENT_FILTER), true)
})

test('真未响应与仅需复核的有效结论不得进待判定', () => {
  const missing = transformResult({
    id: 3,
    category: 'technical',
    requirement_summary: '技术方案',
    response_summary: '',
    compliance_level: 'missing',
    score: 0,
    comment: '投标文件未找到对应响应',
    risk_level: 'high',
    pending_judgment: false,
  })
  const reviewed = transformResult({
    id: 4,
    category: 'commercial',
    requirement_summary: '付款条件',
    response_summary: '已响应',
    compliance_level: 'fully',
    score: 90,
    comment: '已响应，但交叉证据需人工复核一致性',
    risk_level: 'low',
    consistency_flag: 'needs_review',
    pending_judgment: false,
  })

  assert.equal(isPendingJudgment(missing), false)
  assert.equal(complianceCountKey(missing), 'missing')
  assert.equal(matchesComplianceFilter(missing, 'missing'), true)
  assert.equal(matchesComplianceFilter(missing, PENDING_JUDGMENT_FILTER), false)
  assert.equal(isSubstantiveGap(missing), true)
  assert.equal(getComplianceDisplayLabel(missing, complianceLevelLabels), '未响应')

  assert.equal(isPendingJudgment(reviewed), false)
  assert.equal(complianceCountKey(reviewed), 'fully')
  assert.equal(matchesComplianceFilter(reviewed, PENDING_JUDGMENT_FILTER), false)
  assert.equal(isSubstantiveGap(reviewed), false)
})

test('待判定排序应先于不符合和未响应', () => {
  const pending = { pendingJudgment: true, complianceLevel: 'missing' }
  const missing = { pendingJudgment: false, complianceLevel: 'missing' }
  const not = { pendingJudgment: false, complianceLevel: 'not' }

  assert.ok(complianceSortRank(pending, COMPLIANCE_ORDER) < complianceSortRank(not, COMPLIANCE_ORDER))
  assert.ok(complianceSortRank(pending, COMPLIANCE_ORDER) < complianceSortRank(missing, COMPLIANCE_ORDER))
})

test('符合程度导航应把待判定放在全部之后、未响应之前', () => {
  const src = readFileSync(new URL('../src/pages/result/CategoryNav.tsx', import.meta.url), 'utf8')
  assert.match(src, /PENDING_JUDGMENT_FILTER/)
  assert.match(src, /PENDING_JUDGMENT_TOOLTIP/)
  assert.deepEqual([...COMPLIANCE_NAV_VALUES], ['pending', 'not', 'partially', 'fully', 'missing'])
  assert.equal(PENDING_JUDGMENT_LABEL, '待判定')
  assert.match(PENDING_JUDGMENT_TOOLTIP, /不代表投标文件未响应/)
})

test('待判定色应为皇家紫，并与部分符合的黄、不符合的正红区分', () => {
  assert.equal(PENDING_JUDGMENT_HEX, '#722169')
  assert.equal(PENDING_JUDGMENT_COLOR, '#722169')
  assert.notEqual(PENDING_JUDGMENT_COLOR, COMPLIANCE_COLORS.partially)
  assert.notEqual(PENDING_JUDGMENT_COLOR, COMPLIANCE_COLORS.not)
  assert.match(PENDING_JUDGMENT_DOT, /#722169/)
  assert.match(PENDING_JUDGMENT_BADGE_CLASS, /#722169/)
})
