/**
 * 职责: 锁定 7 天免登录的存储路由（勾选→localStorage，未勾选→sessionStorage）
 * 不做: 不测 zustand hydrate、不测登录 API
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  AUTH_STORAGE_KEY,
  parseAuthPersistState,
  readAuthPersistValue,
  shouldPersistAcrossBrowserSessions,
  writeAuthPersistValue,
} from '../src/stores/authStorage.ts'

function memoryStorage(): Storage {
  const map = new Map<string, string>()
  return {
    get length() {
      return map.size
    },
    clear() {
      map.clear()
    },
    getItem(key) {
      return map.has(key) ? map.get(key)! : null
    },
    key(index) {
      return [...map.keys()][index] ?? null
    },
    removeItem(key) {
      map.delete(key)
    },
    setItem(key, value) {
      map.set(key, String(value))
    },
  }
}

function persistBlob(rememberMe?: boolean): string {
  const state: Record<string, unknown> = {
    user: { id: 1, username: 'alice' },
    accessToken: 'access',
    refreshToken: 'refresh',
    isAuthenticated: true,
  }
  if (rememberMe !== undefined) state.rememberMe = rememberMe
  return JSON.stringify({ state, version: 0 })
}

test('rememberMe=true 写入 localStorage 并清掉 sessionStorage', () => {
  const persistent = memoryStorage()
  const session = memoryStorage()
  session.setItem(AUTH_STORAGE_KEY, 'stale-session')
  writeAuthPersistValue(AUTH_STORAGE_KEY, persistBlob(true), persistent, session)
  assert.equal(persistent.getItem(AUTH_STORAGE_KEY), persistBlob(true))
  assert.equal(session.getItem(AUTH_STORAGE_KEY), null)
})

test('rememberMe=false 写入 sessionStorage 并清掉 localStorage', () => {
  const persistent = memoryStorage()
  const session = memoryStorage()
  persistent.setItem(AUTH_STORAGE_KEY, 'stale-local')
  writeAuthPersistValue(AUTH_STORAGE_KEY, persistBlob(false), persistent, session)
  assert.equal(session.getItem(AUTH_STORAGE_KEY), persistBlob(false))
  assert.equal(persistent.getItem(AUTH_STORAGE_KEY), null)
})

test('缺少 rememberMe 字段时不跨会话持久化', () => {
  assert.equal(shouldPersistAcrossBrowserSessions(persistBlob()), false)
})

test('读取时优先 localStorage，避免被同 key 的 session 覆盖', () => {
  const persistent = memoryStorage()
  const session = memoryStorage()
  persistent.setItem(AUTH_STORAGE_KEY, persistBlob(true))
  session.setItem(AUTH_STORAGE_KEY, persistBlob(false))
  const stored = readAuthPersistValue(AUTH_STORAGE_KEY, persistent, session)
  assert.equal(stored?.source, 'local')
  assert.equal(parseAuthPersistState(stored!.serialized, stored!.source)?.rememberMe, true)
})

test('存量 localStorage 无 rememberMe 仍视为已勾选 7 天免登录', () => {
  const snapshot = parseAuthPersistState(persistBlob(), 'local')
  assert.equal(snapshot?.rememberMe, true)
  assert.equal(snapshot?.isAuthenticated, true)
})

test('sessionStorage 无 rememberMe 视为关闭浏览器即退出', () => {
  const snapshot = parseAuthPersistState(persistBlob(), 'session')
  assert.equal(snapshot?.rememberMe, false)
})

test('未登录快照不恢复会话', () => {
  const serialized = JSON.stringify({
    state: { user: null, accessToken: null, isAuthenticated: false, rememberMe: true },
  })
  assert.equal(parseAuthPersistState(serialized, 'local'), null)
})
