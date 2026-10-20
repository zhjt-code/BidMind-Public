/**
 * 职责: 锁定主动退出与会话失效两类登出的落地路径契约
 * 不做: 不测 authStore 清理细节，不测页面渲染
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import {
  LOGIN_PATH,
  PUBLIC_HOME,
  VOLUNTARY_LOGOUT_PATH,
} from '../src/config/routes.ts'

function readSource(relativePath: string): string {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8')
}

test('主动退出落地公开首页，且与登录页路径分离', () => {
  assert.equal(VOLUNTARY_LOGOUT_PATH, PUBLIC_HOME)
  assert.equal(VOLUNTARY_LOGOUT_PATH, '/')
  assert.notEqual(VOLUNTARY_LOGOUT_PATH, LOGIN_PATH)
})

test('Header 主动退出使用 VOLUNTARY_LOGOUT_PATH，不硬跳 /login', () => {
  const headerSource = readSource('../src/components/Header.tsx')
  assert.match(headerSource, /VOLUNTARY_LOGOUT_PATH/)
  assert.match(headerSource, /navigate\(VOLUNTARY_LOGOUT_PATH\)/)
  assert.doesNotMatch(headerSource, /navigate\(LOGIN_PATH\)/)
})

test('会话失效强制登出仍指向登录页', () => {
  const httpClientSource = readSource('../src/services/httpClient.ts')
  const protectedRouteSource = readSource('../src/components/ProtectedRoute.tsx')
  assert.match(httpClientSource, /navigateFn\('\/login'\)/)
  assert.match(httpClientSource, /href = '\/login'/)
  assert.match(protectedRouteSource, /Navigate to=\{LOGIN_PATH\}/)
})
