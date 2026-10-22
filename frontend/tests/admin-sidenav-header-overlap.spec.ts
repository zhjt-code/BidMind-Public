/**
 * 职责: 锁定未钉住管理侧栏浮层从顶栏下方展开，避免被 Header 挡住顶部菜单
 * 不做: 不测钉住态文档流布局、不测 hover 交互运行时
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

function readSource(relativePath: string): string {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8')
}

const sidenavSource = readSource('../src/components/admin/AdminSideNav.tsx')
const headerSource = readSource('../src/components/Header.tsx')

test('控制台 Header 为 sticky z-50 且导航行高 h-16', () => {
  assert.match(headerSource, /sticky top-0 z-50/)
  assert.match(headerSource, /flex items-center h-16/)
})

test('未钉住侧栏浮层从 Header 下方开始，不以 inset-0 钻到顶栏下面', () => {
  assert.doesNotMatch(sidenavSource, /className="fixed inset-0 z-40"/)
  assert.match(sidenavSource, /fixed inset-x-0 bottom-0 top-16 z-40/)
})
