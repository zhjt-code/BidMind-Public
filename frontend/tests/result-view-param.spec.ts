import assert from 'node:assert/strict'
import test from 'node:test'

import {
  RESULT_VIEW_PARAM,
  parseResultView,
  serializeResultView,
} from '../src/pages/result/resultViewParam.ts'

test('?view= 解析: quality 命中, 其余一律回退 review', () => {
  assert.equal(parseResultView('quality'), 'quality')
  assert.equal(parseResultView('review'), 'review')
  assert.equal(parseResultView(null), 'review')
  assert.equal(parseResultView(undefined), 'review')
  assert.equal(parseResultView(''), 'review')
  assert.equal(parseResultView('bogus'), 'review')
})

test('序列化: 默认视图省略参数, 质检视图写 quality', () => {
  assert.equal(serializeResultView('review'), null)
  assert.equal(serializeResultView('quality'), 'quality')
  assert.equal(RESULT_VIEW_PARAM, 'view')
})
