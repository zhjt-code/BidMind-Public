import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

test('空原因 + retryRunning 应归为瞬时态而非业务失败', () => {
  const adapter = readFileSync('frontend/src/services/writingRetryAdapter.ts', 'utf8')

  assert.match(adapter, /runningWithoutReason/)
  assert.match(adapter, /任务仍在处理，无需再次重试/)
  assert.match(adapter, /runningWithoutReason \? 'transient'/)
  assert.match(adapter, /formatFailureMessage\(state\.lastError\)/)
})

test('空原因但未在运行时不得走瞬时文案', () => {
  const adapter = readFileSync('frontend/src/services/writingRetryAdapter.ts', 'utf8')

  assert.match(adapter, /state\.retryRunning && emptyReason/)
})
