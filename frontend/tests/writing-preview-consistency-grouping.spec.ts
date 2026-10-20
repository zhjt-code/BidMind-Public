import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildConsistencyIssueGroups,
  getConsistencyIssueGroupStats,
} from '../src/pages/writing/writingConsistencyGrouping.ts'
import type { ConsistencyIssue } from '../src/services/writingApi.ts'

function issue(params: Partial<ConsistencyIssue>): ConsistencyIssue {
  return {
    type: params.type ?? 'unknown',
    severity: params.severity ?? 'medium',
    nodeId: params.nodeId ?? null,
    message: params.message ?? '示例问题',
    detail: params.detail ?? '',
  }
}

test('固定模板相关 issue 归入同一业务组并保留原顺序', () => {
  const groups = buildConsistencyIssueGroups([
    issue({ type: 'fixed_template_field_missing', severity: 'high', message: '缺少字段' }),
    issue({ type: 'fixed_template_modified', severity: 'high', message: '相似度过低' }),
    issue({ type: 'fixed_template_signature_slot_missing', severity: 'high', message: '缺少签章' }),
  ])

  assert.equal(groups.length, 1)
  assert.equal(groups[0].key, 'fixed_template')
  assert.equal(groups[0].title, '固定模板一致性')
  assert.equal(groups[0].severity, 'high')
  assert.equal(groups[0].collapsedByDefault, false)
  assert.deepEqual(groups[0].issues.map((item) => item.message), ['缺少字段', '相似度过低', '缺少签章'])
})

test('约束覆盖 issue 独立成组, 避免与一般提醒平铺混杂', () => {
  const groups = buildConsistencyIssueGroups([
    issue({ type: 'clause_coverage', severity: 'warning', message: '已确认约束未被正文来源覆盖: A' }),
    issue({ type: 'terminology_inconsistency', severity: 'low', message: '称谓不统一' }),
  ])

  assert.deepEqual(groups.map((group) => group.key), ['clause_coverage', 'terminology'])
  assert.equal(groups[0].title, '已确认约束未被正文来源覆盖')
  assert.equal(groups[0].severity, 'warning')
  assert.equal(groups[0].collapsedByDefault, true)
})

test('统计信息同时返回总组数和含 high 的组数', () => {
  const groups = buildConsistencyIssueGroups([
    issue({ type: 'missing_content', severity: 'high' }),
    issue({ type: 'cross_reference_error', severity: 'medium' }),
    issue({ type: 'clause_coverage', severity: 'warning' }),
  ])

  assert.deepEqual(getConsistencyIssueGroupStats(groups), {
    groupCount: 3,
    highGroupCount: 1,
  })
})

test('评分项 weak_support 归入评分项响应组并默认展开', () => {
  const groups = buildConsistencyIssueGroups([
    issue({ type: 'fixed_template_field_missing', severity: 'high' }),
    issue({
      type: 'scoring_weak_support',
      severity: 'medium',
      message: "评分项'人员素质'对应章节缺素材",
    }),
    issue({ type: 'cross_reference_error', severity: 'medium' }),
  ])

  assert.deepEqual(groups.map((group) => group.key), [
    'fixed_template',
    'scoring_response',
    'cross_reference',
  ])
  const scoring = groups.find((group) => group.key === 'scoring_response')
  assert.ok(scoring)
  assert.equal(scoring.title, '评分项响应')
  assert.equal(scoring.collapsedByDefault, false)
})

test('空输入返回空分组', () => {
  assert.deepEqual(buildConsistencyIssueGroups(null), [])
  assert.deepEqual(buildConsistencyIssueGroups(undefined), [])
  assert.deepEqual(buildConsistencyIssueGroups([]), [])
})

test('未知 issue type 归入其他检查提醒', () => {
  const groups = buildConsistencyIssueGroups([
    issue({ type: 'future_issue_type', severity: 'low', message: '未来类型' }),
  ])

  assert.equal(groups.length, 1)
  assert.equal(groups[0].key, 'other')
  assert.equal(groups[0].title, '其他检查提醒')
  assert.equal(groups[0].collapsedByDefault, true)
})

test('同组混合 severity 时取最高严重度且高风险组默认展开', () => {
  const groups = buildConsistencyIssueGroups([
    issue({ type: 'cross_reference_error', severity: 'medium' }),
    issue({ type: 'cross_reference_check_degraded', severity: 'high' }),
    issue({ type: 'cross_reference_error', severity: 'low' }),
  ])

  assert.equal(groups[0].key, 'cross_reference')
  assert.equal(groups[0].severity, 'high')
  assert.equal(groups[0].collapsedByDefault, false)
})
