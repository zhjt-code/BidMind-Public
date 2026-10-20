/**
 * 职责: 锁定管理后台钉住侧栏与主内容各自独立纵向滚动
 * 不做: 不测未钉住 hover 显隐、不测运营总览页面内容
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

function readSource(relativePath: string): string {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8')
}

const layoutSource = readSource('../src/components/AdminLayout.tsx')
const sidenavSource = readSource('../src/components/admin/AdminSideNav.tsx')

test('AdminLayout 根容器固定在视口内，禁止长内容泄漏成 html 文档滚动', () => {
  assert.match(
    layoutSource,
    /<div className="fixed inset-0 bg-gray-50 flex flex-col overflow-hidden">/,
  )
  assert.doesNotMatch(
    layoutSource,
    /<div className="min-h-screen bg-gray-50 flex flex-col">/,
  )
})

test('主内容区在 flex 列内可收缩，并阻断滚轮链式传到文档', () => {
  assert.match(
    layoutSource,
    /<main ref=\{mainRef\} className="flex-1 min-h-0 overflow-auto overscroll-contain">/,
  )
})

test('钉住侧栏与导航各自形成独立滚动边界', () => {
  assert.match(
    sidenavSource,
    /<aside className="w-56 flex-shrink-0 min-h-0 overflow-hidden border-r border-gray-200\/80 bg-white">/,
  )
  assert.match(
    sidenavSource,
    /<div className="flex h-full min-h-0 flex-col overflow-hidden">/,
  )
  assert.match(
    sidenavSource,
    /<nav className="flex-1 min-h-0 space-y-6 overflow-y-auto overscroll-contain px-3 py-4">/,
  )
})

test('未钉住浮层仍从 Header 下方展开，不回归 inset-0 钻顶栏', () => {
  assert.doesNotMatch(sidenavSource, /className="fixed inset-0 z-40"/)
  assert.match(sidenavSource, /fixed inset-x-0 bottom-0 top-16 z-40/)
})
