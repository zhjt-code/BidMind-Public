import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

test('投标审查进度页应接入显式重试动作', () => {
  const progressPage = readFileSync('frontend/src/pages/Progress.tsx', 'utf8')

  assert.match(progressPage, /retryAction:/)
  assert.match(progressPage, /reviewService\.retryReview/)
})

test('共享进度页应渲染完整 RetryActionPanel 而非仅提示文案', () => {
  const genericProgress = readFileSync('frontend/src/components/GenericProgressPage.tsx', 'utf8')

  assert.match(genericProgress, /RetryActionPanel/)
  assert.match(genericProgress, /preservedOutputs/)
  assert.match(genericProgress, /resetScope/)
  assert.match(genericProgress, /statusData\?\.failedNode \|\| statusData\?\.retryable \|\| statusData\?\.retryRunning/)
})

test('投标上传页应为 document_process 接入显式重试面板', () => {
  const uploadPage = readFileSync('frontend/src/pages/FileUpload.tsx', 'utf8')

  assert.match(uploadPage, /RetryActionPanel/)
  assert.match(uploadPage, /reviewService\.getRetryState/)
  assert.match(uploadPage, /reviewService\.retryReview/)
  assert.match(uploadPage, /failedNode === 'document_process'/)
})

test('共享进度页不得把轮询 isFetching 并入重试锁', () => {
  const genericProgress = readFileSync('frontend/src/components/GenericProgressPage.tsx', 'utf8')

  assert.doesNotMatch(genericProgress, /refreshing=\{isFetching\}/)
})

test('信息性关闭不得被 retryRunning 禁用', () => {
  const panel = readFileSync('frontend/src/pages/writing/RetryActionPanel.tsx', 'utf8')
  const dismissBlock = panel.slice(
    panel.indexOf('{dismissAction &&'),
    panel.indexOf('{primaryAction &&'),
  )

  assert.match(dismissBlock, /dismissAction\.label/)
  assert.doesNotMatch(dismissBlock, /disabled=\{retryRunning\}/)
  assert.doesNotMatch(panel, /showCloseButton=\{!retryRunning\}/)
  assert.doesNotMatch(panel, /if \(retryRunning\) return/)
})

test('RetryActionPanel 应统一为紧凑入口加确认弹窗', () => {
  const panel = readFileSync('frontend/src/pages/writing/RetryActionPanel.tsx', 'utf8')

  assert.match(panel, /DialogContent/)
  assert.match(panel, /查看并重试/)
  assert.match(panel, /确认后弹窗会关闭/)
  assert.match(panel, /允许重置/)
  assert.doesNotMatch(panel, /rounded-xl border border-red-200 bg-red-50 p-4 mb-4/)
})
