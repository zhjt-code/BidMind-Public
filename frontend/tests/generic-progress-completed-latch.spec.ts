import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  isProgressCompleted,
  nextProgressSessionStatus,
} from '../src/components/progressPageState.ts'

test('完成旗标必须跟随当前 overallStatus，completed 之后的 running 不得再显示完成', () => {
  assert.equal(isProgressCompleted('completed'), true)
  assert.equal(isProgressCompleted('done'), true)
  assert.equal(isProgressCompleted('running'), false)
  assert.equal(isProgressCompleted('processing'), false)
  assert.equal(isProgressCompleted('failed'), false)
})

test('同形态第二例：串标 overall 从 completed 回到 running 时完成旗标也必须落下', () => {
  assert.equal(isProgressCompleted('success'), true)
  assert.equal(isProgressCompleted('in_progress'), false)
})

test('事后回看成 completed 时会话标记保持 completed，避免自动跳转死循环', () => {
  assert.equal(nextProgressSessionStatus(null, 'completed'), 'completed')
  assert.equal(nextProgressSessionStatus('completed', 'completed'), 'completed')
})

test('先看到 completed 再看到 running 时会话改记 running，允许本轮真正完成后再自动跳转', () => {
  assert.equal(nextProgressSessionStatus('completed', 'running'), 'running')
  assert.equal(nextProgressSessionStatus('running', 'completed'), 'running')
  assert.equal(nextProgressSessionStatus(null, 'running'), 'running')
})

test('GenericProgressPage 必须用当前 overallStatus 推导完成态，禁止只升不降的粘滞赋值', () => {
  const source = readFileSync('src/components/GenericProgressPage.tsx', 'utf8')

  assert.match(source, /isProgressCompleted\(/)
  assert.match(source, /nextProgressSessionStatus\(/)
  assert.doesNotMatch(
    source,
    /if \(overallStatus === 'completed'\) \{\s*setIsCompleted\(true\)/,
  )
})
