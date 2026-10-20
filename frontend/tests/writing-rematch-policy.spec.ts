import assert from 'node:assert/strict'
import test from 'node:test'

import {
  getRematchModeContent,
  rematchResetFlag,
  type RematchMode,
} from '../src/pages/writing/writingRematchPolicy.ts'

test('默认保留模式不清空审核且非危险操作', () => {
  const content = getRematchModeContent('keep')
  assert.equal(rematchResetFlag('keep'), false)
  assert.equal(content.danger, false)
  assert.match(content.confirmLabel, /重新匹配/)
  assert.match(content.description, /保留/)
})

test('重置模式清空全部审核且为危险操作', () => {
  const content = getRematchModeContent('reset')
  assert.equal(rematchResetFlag('reset'), true)
  assert.equal(content.danger, true)
  assert.match(content.description, /清空|重置/)
})

test('两种模式文案互不相同，避免误导', () => {
  const keep = getRematchModeContent('keep')
  const reset = getRematchModeContent('reset')
  assert.notEqual(keep.description, reset.description)
  assert.notEqual(keep.confirmLabel, reset.confirmLabel)
})

test('rematchResetFlag 仅对 reset 返回 true', () => {
  const modes: RematchMode[] = ['keep', 'reset']
  assert.deepEqual(
    modes.map(rematchResetFlag),
    [false, true],
  )
})
