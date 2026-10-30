import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { URL } from 'node:url'

const headerSource = readFileSync(new URL('../src/components/Header.tsx', import.meta.url), 'utf8')
const logoSource = readFileSync(new URL('../src/components/Logo.tsx', import.meta.url), 'utf8')

test('顶部栏为 h-16，主导航靠左且不被推到右侧', () => {
  assert.match(headerSource, /flex items-center h-16/)
  assert.match(
    headerSource,
    /Logo size="md"[\s\S]*?h-5 w-px bg-gray-200 mx-4[\s\S]*?<nav className="flex items-center h-full gap-1"/,
  )
  assert.match(
    headerSource,
    /<\/nav>\s*<div className="flex-1" \/>/,
  )
})

test('顶栏 Logo 高度放大且不超过 h-16', () => {
  assert.doesNotMatch(logoSource, /scale-\[1\.3\]/)
  assert.match(logoSource, /md: \{ icon: 40, textHeight: 23/)
  const mdMatch = logoSource.match(/md: \{ icon: (\d+), textHeight: (\d+)/)
  assert.ok(mdMatch)
  assert.ok(Number(mdMatch[1]) < 64, 'icon 不得超过顶栏 h-16')
  assert.ok(Number(mdMatch[2]) < 64, '字标不得超过顶栏 h-16')
})

test('一级导航选中态保留圆角矩形+阴影，并叠加字重与底线', () => {
  assert.match(headerSource, /rounded-lg/)
  assert.match(headerSource, /bg-primary\/8/)
  assert.match(headerSource, /border-primary\/15/)
  assert.match(headerSource, /shadow-xs/)
  assert.match(headerSource, /font-semibold text-primary/)
  assert.match(headerSource, /font-medium text-gray-700/)
  assert.match(headerSource, /text-\[15px\]/)
  assert.match(
    headerSource,
    /absolute -bottom-3 inset-x-3 h-\[2\.5px\] bg-primary rounded-full/,
  )
  assert.doesNotMatch(headerSource, /relative flex h-9 items-center gap-1\.5 rounded-full border/)
})

test('主导航与管理入口共用同一套一级样式函数', () => {
  assert.match(headerSource, /primaryNavClassName\(active\)/)
  assert.match(headerSource, /primaryNavClassName\(isAdminActive\)/)
})
