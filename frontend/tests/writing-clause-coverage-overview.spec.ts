import assert from 'node:assert/strict'
import test from 'node:test'

import { getCoverageAction } from '../src/pages/writing/writingClauseCoverageAction.ts'

test('getCoverageAction: 只有待生成时不跳转绑定页', () => {
  assert.equal(getCoverageAction({ pendingAssignment: 0, unmatched: 0 }), null)
})

test('getCoverageAction: 待人工分配优先聚焦orphan', () => {
  assert.deepEqual(
    getCoverageAction({ pendingAssignment: 17, unmatched: 32 }),
    { label: '处理待分配约束', focus: 'pendingAssignment' },
  )
})

test('getCoverageAction: 仅未匹配时聚焦unmatched', () => {
  assert.deepEqual(
    getCoverageAction({ pendingAssignment: 0, unmatched: 32 }),
    { label: '查看未匹配约束', focus: 'unmatched' },
  )
})
