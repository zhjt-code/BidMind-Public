import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { REVIEW_STATS_SCROLL_BODY_CLASS } from '../src/pages/result/reviewStatsLayout.ts'

const source = readFileSync(
  new URL('../src/pages/result/ReviewStats.tsx', import.meta.url),
  'utf8',
).replace(/\r\n/g, '\n')

const collapsedBodyStart = source.indexOf('{!collapsed && (')
const collapsedBodyEnd = source.indexOf('\n      )}\n    </div>\n  )', collapsedBodyStart)

test('审查概览的非头部提示块应随图表折叠隐藏', () => {
  assert.notEqual(collapsedBodyStart, -1)
  assert.notEqual(collapsedBodyEnd, -1)

  const decisionSummaryBlock = source.indexOf('审查最终结论')
  const focusSectionsBlock = source.indexOf('{focusSections.length > 0 && (')

  assert.ok(decisionSummaryBlock > collapsedBodyStart)
  assert.ok(decisionSummaryBlock < collapsedBodyEnd)
  assert.ok(focusSectionsBlock > collapsedBodyStart)
  assert.ok(focusSectionsBlock < collapsedBodyEnd)
})

test('审查概览卡片应裁剪折叠主体的溢出内容', () => {
  assert.equal(source.includes('bg-white rounded-lg shadow-sm mb-4 overflow-hidden'), true)
})

test('审查概览展开内容区应启用独立纵向滚动', () => {
  assert.equal(REVIEW_STATS_SCROLL_BODY_CLASS.includes('overflow-y-auto'), true)
  assert.equal(REVIEW_STATS_SCROLL_BODY_CLASS.includes('max-h-[45vh]'), true)
  assert.equal(REVIEW_STATS_SCROLL_BODY_CLASS.includes('overscroll-contain'), true)
  assert.equal(source.includes('REVIEW_STATS_SCROLL_BODY_CLASS'), true)
})

test('分类雷达图标题应使用符合度语义避免混同评标得分', () => {
  assert.equal(source.includes('分类符合度'), true)
  assert.equal(source.includes('分类平均得分'), false)
})
