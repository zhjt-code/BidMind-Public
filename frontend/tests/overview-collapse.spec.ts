import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  INITIAL_OVERVIEW_COLLAPSE_STATE,
  canConsumeInnerScroll,
  isDwellElapsed,
  reduceOverviewCollapse,
  resolveDetailShell,
} from '../src/lib/overviewCollapse.ts'

test('无溢出的短面板进页时不算滑到底，避免一进页就收起', () => {
  const shortPanel = { scrollTop: 0, scrollHeight: 240, clientHeight: 240 }
  assert.equal(canConsumeInnerScroll(shortPanel), false)
})

test('有溢出的长面板只有贴近底部才允许消耗', () => {
  const longPanel = { scrollTop: 0, scrollHeight: 500, clientHeight: 200 }
  assert.equal(canConsumeInnerScroll(longPanel), false)
  assert.equal(canConsumeInnerScroll({ ...longPanel, scrollTop: 284 }), true)
})

test('停留未满 dwell 不提交，满了才提交', () => {
  assert.equal(isDwellElapsed(null, 1000, 300), false)
  assert.equal(isDwellElapsed(800, 1000, 300), false)
  assert.equal(isDwellElapsed(700, 1000, 300), true)
})

test('三栏交互在展开态始终收起，内滑到底仅自动态生效', () => {
  const afterDetail = reduceOverviewCollapse(INITIAL_OVERVIEW_COLLAPSE_STATE, {
    type: 'detail_engage',
  })
  assert.deepEqual(afterDetail, { control: 'auto', collapsed: true })

  const afterInner = reduceOverviewCollapse(INITIAL_OVERVIEW_COLLAPSE_STATE, {
    type: 'inner_bottom_dwell',
  })
  assert.deepEqual(afterInner, { control: 'auto', collapsed: true })

  const expandedManual = reduceOverviewCollapse(afterDetail, { type: 'toggle' })
  assert.deepEqual(expandedManual, { control: 'manual', collapsed: false })
  assert.deepEqual(
    reduceOverviewCollapse(expandedManual, { type: 'detail_engage' }),
    { control: 'manual', collapsed: true },
  )

  const reexpanded = reduceOverviewCollapse(
    { control: 'manual', collapsed: true },
    { type: 'toggle' },
  )
  assert.deepEqual(reexpanded, { control: 'manual', collapsed: false })
  assert.deepEqual(
    reduceOverviewCollapse(reexpanded, { type: 'detail_engage' }),
    { control: 'manual', collapsed: true },
  )

  assert.deepEqual(
    reduceOverviewCollapse(expandedManual, { type: 'inner_bottom_dwell' }),
    expandedManual,
  )
})

test('详细壳取概览卡片父节点的下一兄弟，不误用卡片内部节点', () => {
  const detailShell = { id: 'detail-shell' }
  const innerChild = { id: 'inner' }
  const statsRoot = {
    parentElement: {
      nextElementSibling: detailShell,
    },
  }
  assert.equal(resolveDetailShell(statsRoot), detailShell)
  assert.notEqual(resolveDetailShell(statsRoot), innerChild)
  assert.equal(resolveDetailShell({ parentElement: null }), null)
})

const reviewStatsSource = readFileSync(
  new URL('../src/pages/result/ReviewStats.tsx', import.meta.url),
  'utf8',
)
const collusionStatsSource = readFileSync(
  new URL('../src/pages/collusionResult/CollusionStats.tsx', import.meta.url),
  'utf8',
)

test('投标与串标概览共用自动让位 hook，而不是各写专用收起', () => {
  assert.match(reviewStatsSource, /useOneShotOverviewCollapse/)
  assert.match(collusionStatsSource, /useOneShotOverviewCollapse/)
  assert.match(reviewStatsSource, /rootRef/)
  assert.match(collusionStatsSource, /rootRef/)
  assert.match(reviewStatsSource, /bodyRef/)
  assert.match(collusionStatsSource, /bodyRef/)
  assert.match(reviewStatsSource, /toggleCollapsed/)
  assert.match(collusionStatsSource, /toggleCollapsed/)
})

const resultPageSource = readFileSync(
  new URL('../src/pages/Result.tsx', import.meta.url),
  'utf8',
)
const collusionPageSource = readFileSync(
  new URL('../src/pages/CollusionResult.tsx', import.meta.url),
  'utf8',
)

test('两页都把概览包在 shrink-0 里，随后渲染三栏壳供 detail_engage 挂载', () => {
  assert.match(resultPageSource, /shrink-0 px-1[\s\S]{0,180}<ReviewStats[\s\S]{0,280}RESULT_PAGE_CONTENT_SHELL_CLASS/)
  assert.match(collusionPageSource, /shrink-0 px-1[\s\S]{0,220}<CollusionStats[\s\S]{0,360}RESULT_PAGE_CONTENT_SHELL_CLASS/)
})
