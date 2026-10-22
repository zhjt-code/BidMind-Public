import assert from 'node:assert/strict'
import test from 'node:test'

import {
  RESULT_PAGE_ROOT_CLASS,
  RESULT_PAGE_CONTENT_SHELL_CLASS,
  RESULT_PAGE_PANE_CLASS,
} from '../src/pages/resultPageLayout.ts'

test('结果页根容器不应再直接依赖 100vh 计算高度', () => {
  assert.equal(RESULT_PAGE_ROOT_CLASS.includes('100vh'), false)
  assert.equal(RESULT_PAGE_ROOT_CLASS.includes('h-full'), true)
  assert.equal(RESULT_PAGE_ROOT_CLASS.includes('min-h-0'), true)
})

test('结果页内容壳层与 pane 应显式声明 min-h-0', () => {
  assert.equal(RESULT_PAGE_CONTENT_SHELL_CLASS.includes('min-h-0'), true)
  assert.equal(RESULT_PAGE_PANE_CLASS.includes('min-h-0'), true)
  assert.equal(RESULT_PAGE_PANE_CLASS.includes('overflow-hidden'), true)
})
