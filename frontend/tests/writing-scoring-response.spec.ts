import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildConsistencyIssueGroups,
} from '../src/pages/writing/writingConsistencyGrouping.ts'
import {
  splitIssuesBySeverity,
  summarizeScoringResponse,
} from '../src/pages/writing/writingPreviewDownloadHelpers.ts'

const scoringIssues = [
  { type: 'scoring_unresponded', severity: 'high', message: "评分项'A'（满分10分）未被任何章节响应，将直接失分" },
  { type: 'scoring_weak_support', severity: 'medium', message: "评分项'B'（满分8分）对应章节内容为占位或生成失败，需补充素材" },
  { type: 'scoring_non_body', severity: 'info', message: "评分项'C'（满分30分）属价格分，不在正文写作范围" },
]

test('buildConsistencyIssueGroups 将 scoring_* 归入独立"评分项响应"组', () => {
  const groups = buildConsistencyIssueGroups(scoringIssues)
  const scoring = groups.find((g) => g.key === 'scoring_response')
  assert.ok(scoring, '应存在 scoring_response 组')
  assert.equal(scoring?.title, '评分项响应')
  assert.equal(scoring?.issues.length, 3)
  // 组内含 high → 组严重度为 high, 不默认折叠
  assert.equal(scoring?.severity, 'high')
  assert.equal(scoring?.collapsedByDefault, false)
})

test('纯 info 评分项组严重度为 info(不被误判为 low)', () => {
  const groups = buildConsistencyIssueGroups([
    { type: 'scoring_non_body', severity: 'info', message: "评分项'C'属价格分" },
  ])
  const scoring = groups.find((g) => g.key === 'scoring_response')
  assert.equal(scoring?.severity, 'info')
})

test('splitIssuesBySeverity 将 info 从一般提醒中分离', () => {
  const { high, other, info } = splitIssuesBySeverity(scoringIssues)
  assert.equal(high.length, 1)
  assert.equal(other.length, 1) // 仅 medium 计入一般提醒
  assert.equal(info.length, 1) // info 单列
})

test('summarizeScoringResponse 统计未响应项数与合计满分', () => {
  const summary = summarizeScoringResponse(scoringIssues)
  assert.equal(summary.unrespondedCount, 1)
  assert.equal(summary.unrespondedScore, 10)
  assert.equal(summary.nonBodyCount, 1)
})
