import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { transformRetryState } from '../src/services/writingRetryTransforms.ts'

test('串标进度页应接入完整显式重试动作', () => {
  const progressPage = readFileSync('frontend/src/pages/CollusionProgress.tsx', 'utf8')

  assert.match(progressPage, /retryAction:/)
  assert.match(progressPage, /collusionService\.retryAnalysis/)
})

test('串标上传页应为 document_process 接入显式重试面板', () => {
  const uploadPage = readFileSync('frontend/src/pages/CollusionUpload.tsx', 'utf8')

  assert.match(uploadPage, /RetryActionPanel/)
  assert.match(uploadPage, /collusionService\.getRetryState/)
  assert.match(uploadPage, /collusionService\.retryAnalysis/)
  assert.match(uploadPage, /failedNode === 'document_process'/)
})

test('RetryActionPanel 应包含串标全节点中文名称', () => {
  const panel = readFileSync('frontend/src/pages/writing/RetryActionPanel.tsx', 'utf8')

  for (const node of [
    'collusion_extract',
    'collusion_global',
    'collusion_compare',
    'collusion_cluster',
    'collusion_synthesis',
    'collusion_report',
    'collusion_analyze',
  ]) {
    assert.match(panel, new RegExp(node))
  }
})

test('串标 retry-state 字段应复用统一 retry transform', () => {
  const state = transformRetryState({
    retryable: true,
    failed_node: 'collusion_compare',
    target_id: null,
    failed_stage: 'comparing',
    retry_action_label: '从重点文件对比继续重试',
    preserved_outputs: ['成功提取的文档信息', '已成功的文件对结果'],
    reset_scope: ['失败或缺失的文件对结果'],
    last_attempt: 4,
    retry_running: false,
    last_error: 'pair failed',
  })

  assert.equal(state.failedNode, 'collusion_compare')
  assert.equal(state.failedStage, 'comparing')
  assert.equal(state.lastAttempt, 4)
  assert.equal(state.retryRunning, false)
  assert.deepEqual(state.preservedOutputs, ['成功提取的文档信息', '已成功的文件对结果'])
})
