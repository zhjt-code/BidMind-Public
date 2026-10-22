import assert from 'node:assert/strict'
import test from 'node:test'

import { buildPairOverviewMessages } from '../src/pages/collusionResult/pairOverviewMessages.ts'

test('algorithm_only + needs_review 应提示关键信息提取不足及常见原因', () => {
  const messages = buildPairOverviewMessages({
    analysisMode: 'algorithm_only',
    decisionState: 'needs_review',
    reviewState: 'needs_review',
    similarityScore: 0,
  })

  assert.equal(messages.length, 3)
  assert.equal(messages[0].tone, 'warning')
  assert.match(messages[0].text, /当前未发现异常证据/)
  assert.match(messages[1].text, /提取到足够完整、稳定的关键信息/)
  assert.match(messages[1].text, /扫描件|文本层缺失|图片、表格/)
  assert.match(messages[2].text, /核实原文件是否清晰可选中/)
})

test('skipped 应提示尚未完成深入分析', () => {
  const messages = buildPairOverviewMessages({
    analysisMode: 'skipped',
    decisionState: 'skipped',
    reviewState: 'unreviewed',
    similarityScore: 0.2,
  })

  assert.equal(messages.length, 2)
  assert.match(messages[0].text, /尚未对这对文件完成深入分析/)
  assert.match(messages[1].text, /重新发起分析/)
})

test('failed 应提示分析未形成稳定结果', () => {
  const messages = buildPairOverviewMessages({
    analysisMode: 'failed',
    decisionState: 'failed',
    reviewState: 'failed',
    similarityScore: 0.5,
  })

  assert.equal(messages.length, 2)
  assert.equal(messages[0].tone, 'danger')
  assert.match(messages[0].text, /未能形成稳定结果/)
  assert.match(messages[1].text, /无法正常解析|提取失败|出现异常/)
})

test('decidable + 高相似度 应保留提醒性说明', () => {
  const messages = buildPairOverviewMessages({
    analysisMode: 'grouped_parallel_recovered',
    decisionState: 'decidable',
    reviewState: 'completed',
    similarityScore: 0.82,
  })

  assert.equal(messages.length, 2)
  assert.equal(messages[0].tone, 'info')
  assert.match(messages[1].text, /文本相似度约 82%/)
})
