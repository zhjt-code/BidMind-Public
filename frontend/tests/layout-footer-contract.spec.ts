import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const layoutSource = readFileSync(resolve('src/components/Layout.tsx'), 'utf8')

test('全局 Layout 不应渲染固定底部品牌 footer', () => {
  assert.doesNotMatch(layoutSource, /<footer\b/)
  assert.doesNotMatch(layoutSource, /BidMind AI - 智能标书审查系统/)
})

test('全局 Layout 根容器应固定在视口内，禁止内部滚动内容泄漏成 html 滚动', () => {
  assert.match(
    layoutSource,
    /<div className="fixed inset-0 bg-gray-100 flex flex-col overflow-hidden">/,
  )
  assert.doesNotMatch(layoutSource, /<div className="h-screen bg-gray-100 flex flex-col overflow-hidden">/)
})
