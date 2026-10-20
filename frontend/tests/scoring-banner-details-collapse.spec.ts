/**
 * 防御测试：评分核对条明细折叠。
 *
 * 通过态收起以让出列表空间；待处理态初始展开，避免差额被藏起来。
 * 两条夹具共用同一函数：确认页通过态，以及写标/琥珀待处理态。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  initialScoringDetailsCollapsed,
  shouldShowScoringDetails,
} from '../src/pages/RequirementReview/scoringBannerDetailsCollapse.ts'

test('通过态初始收起，主结论以外的明细不渲染', () => {
  const collapsed = initialScoringDetailsCollapsed(false)
  assert.equal(collapsed, true)
  assert.equal(shouldShowScoringDetails(collapsed, true), false)
})

test('待处理态初始展开，差额明细可见', () => {
  const collapsed = initialScoringDetailsCollapsed(true)
  assert.equal(collapsed, false)
  assert.equal(shouldShowScoringDetails(collapsed, true), true)
})

test('没有明细时不渲染展开区，用户收起后待处理明细也隐藏', () => {
  assert.equal(shouldShowScoringDetails(false, false), false)
  assert.equal(shouldShowScoringDetails(true, true), false)
})

test('折叠外壳把主结论与操作放在同一行，箭头在最右且不带文字', () => {
  const chromeSource = readFileSync(
    new URL('../src/pages/RequirementReview/components/ScoringBannerChrome.tsx', import.meta.url),
    'utf8',
  )
  const row = chromeSource.indexOf('flex items-center gap-2')
  const actionsSlot = chromeSource.indexOf('{props.actions}')
  const toggle = chromeSource.indexOf('aria-label={toggleLabel}')
  const detailsGate = chromeSource.indexOf('showDetails ? props.details')
  assert.ok(row !== -1 && actionsSlot > row, '操作按钮必须与主结论同一行')
  assert.ok(toggle > actionsSlot, '折叠箭头必须在操作按钮右侧')
  assert.ok(detailsGate > toggle, '明细必须在摘要行之后，且受 showDetails 门禁')
  assert.match(chromeSource, /aria-expanded=\{showDetails\}/)
  assert.doesNotMatch(chromeSource, />展开明细</)
  assert.doesNotMatch(chromeSource, /border-t/)
})
