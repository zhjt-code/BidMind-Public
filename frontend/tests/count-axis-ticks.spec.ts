import assert from 'node:assert/strict'
import test from 'node:test'

import { countAxisTicks, countAxisWidth } from '../src/pages/projectList/dashboard/countAxisTicks.ts'

test('数量轴按等步长向上取整，130 收到 150', () => {
  assert.deepEqual(countAxisTicks(130), [0, 50, 100, 150])
  assert.deepEqual(countAxisTicks(150), [0, 50, 100, 150])
})

test('更大的堆叠总数仍等距，且顶端盖住数据', () => {
  const ticks = countAxisTicks(509)
  assert.deepEqual(ticks, [0, 200, 400, 600])
  assert.ok(ticks[ticks.length - 1] >= 509)
  assertEvenStep(ticks)

  const small = countAxisTicks(8)
  assert.deepEqual(small, [0, 2, 4, 6, 8])
  assertEvenStep(small)
})

test('空数据和更长刻度都不把标签宽度收成裁切', () => {
  assert.deepEqual(countAxisTicks(0), [0, 1])
  assert.deepEqual(countAxisTicks(Number.NaN), [0, 1])
  assert.ok(countAxisWidth(countAxisTicks(10000)) > countAxisWidth(countAxisTicks(130)))
})

function assertEvenStep(ticks: number[]): void {
  assert.ok(ticks.length >= 2)
  const step = ticks[1] - ticks[0]
  for (let index = 1; index < ticks.length; index += 1) {
    assert.equal(ticks[index] - ticks[index - 1], step)
  }
}
