import assert from 'node:assert/strict'
import test from 'node:test'

import {
  adviceBoardWeekLabel,
  boardNodeItems,
  boardPurposeItems,
  pickLatestAdviceByTarget,
} from '../src/pages/admin/ops/weeklyAdviceBoard.ts'

function row(
  id: number,
  scope: string,
  target: string,
  weekStart: string,
): { id: number; scope: string; target: string; week_start: string } {
  return { id, scope, target, week_start: weekStart }
}

test('pickLatestAdviceByTarget 同一用途池只留最新一周', () => {
  const items = [
    row(1, 'purpose', 'vision', '2026-08-03T16:00:00+00:00'),
    row(2, 'purpose', 'vision', '2026-08-17T16:00:00+00:00'),
    row(3, 'purpose', 'extract', '2026-08-10T16:00:00+00:00'),
    row(4, 'purpose', 'vision', '2026-08-10T16:00:00+00:00'),
  ]
  const latest = pickLatestAdviceByTarget(items)
  const vision = latest.filter((i) => i.target === 'vision')
  assert.equal(vision.length, 1)
  assert.equal(vision[0].id, 2)
  assert.equal(latest.filter((i) => i.target === 'extract').length, 1)
})

test('boardPurposeItems 按视觉-提取-审查排序且去重', () => {
  const items = [
    row(10, 'purpose', 'review', '2026-08-03T16:00:00+00:00'),
    row(11, 'purpose', 'review', '2026-08-17T16:00:00+00:00'),
    row(12, 'purpose', 'vision', '2026-08-17T16:00:00+00:00'),
    row(13, 'purpose', 'extract', '2026-08-17T16:00:00+00:00'),
    row(14, 'node', 'bid_review.x', '2026-08-17T16:00:00+00:00'),
  ]
  const purposes = boardPurposeItems(items)
  assert.deepEqual(
    purposes.map((i) => i.target),
    ['vision', 'extract', 'review'],
  )
  assert.equal(purposes.find((i) => i.target === 'review')?.id, 11)
})

test('boardNodeItems 去重后按周新到旧截断', () => {
  const items = [
    row(21, 'node', 'a.old', '2026-08-03T16:00:00+00:00'),
    row(22, 'node', 'b.mid', '2026-08-10T16:00:00+00:00'),
    row(23, 'node', 'c.new', '2026-08-17T16:00:00+00:00'),
    row(24, 'node', 'a.old', '2026-08-17T16:00:00+00:00'),
    row(25, 'purpose', 'vision', '2026-08-17T16:00:00+00:00'),
  ]
  const nodes = boardNodeItems(items, 2)
  assert.equal(nodes.length, 2)
  assert.deepEqual(
    nodes.map((i) => i.target),
    ['a.old', 'c.new'],
  )
  assert.equal(nodes[0].id, 24)
})

test('adviceBoardWeekLabel 展示统计周闭区间', () => {
  const label = adviceBoardWeekLabel([
    row(1, 'purpose', 'vision', '2026-08-16T16:00:00+00:00'),
    row(2, 'purpose', 'extract', '2026-08-09T16:00:00+00:00'),
  ])
  assert.equal(label, '2026-08-17 ~ 2026-08-23')
})
