import assert from 'node:assert/strict'
import test from 'node:test'

import { PERCENT_AXIS_TICKS, toPercentShares } from '../src/pages/projectList/dashboard/percentShares.ts'

test('占比份额总和恰好为 100，刻度固定在四分位', () => {
  assert.deepEqual(PERCENT_AXIS_TICKS, [0, 25, 50, 75, 100])

  const even = toPercentShares([1, 1, 1])
  assert.equal(sum(even), 100)
  assert.ok(even.every((value) => value >= 0 && value <= 100))

  const weighted = toPercentShares([2, 3, 5, 0])
  assert.equal(sum(weighted), 100)
  assert.equal(weighted[3], 0)
  assert.ok(weighted[0] < weighted[1] && weighted[1] < weighted[2])
})

test('空份额与单一份额都不把纵轴顶出 100', () => {
  assert.deepEqual(toPercentShares([0, 0, 0, 0]), [0, 0, 0, 0])
  assert.deepEqual(toPercentShares([4]), [100])
  assert.deepEqual(toPercentShares([-1, Number.NaN, 0]), [0, 0, 0])
})

function sum(values: number[]): number {
  return Math.round(values.reduce((total, value) => total + value, 0) * 10) / 10
}
