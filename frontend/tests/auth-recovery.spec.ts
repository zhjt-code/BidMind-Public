import assert from 'node:assert/strict'
import test from 'node:test'
import {
  canReuseAuthReadyPromise,
  isStaleAuthReadyRun,
  shouldResetAuthReadyCache,
} from '../src/components/protectedRouteState.ts'
import { getProjectListViewState } from '../src/pages/projectList/projectListViewState.ts'
import { shouldResetProjectStateForAuthChange } from '../src/stores/projectStoreState.ts'

test('token 变化时应重置 authReady 缓存', () => {
  assert.equal(
    shouldResetAuthReadyCache(
      { isAuthenticated: true, accessToken: 'old-token', refreshToken: 'refresh-a' },
      { isAuthenticated: true, accessToken: 'new-token', refreshToken: 'refresh-a' },
    ),
    true,
  )

  assert.equal(
    shouldResetAuthReadyCache(
      { isAuthenticated: true, accessToken: 'same-token', refreshToken: 'refresh-a' },
      { isAuthenticated: true, accessToken: 'same-token', refreshToken: 'refresh-b' },
    ),
    true,
  )

  assert.equal(
    shouldResetAuthReadyCache(
      { isAuthenticated: true, accessToken: 'same-token', refreshToken: 'refresh-a' },
      { isAuthenticated: true, accessToken: 'same-token', refreshToken: 'refresh-a' },
    ),
    false,
  )
})

test('只有当前 token 未变化时才复用 authReady Promise', () => {
  assert.equal(
    canReuseAuthReadyPromise({
      isAuthenticated: true,
      accessToken: 'token-a',
      cachedToken: 'token-a',
      hasPromise: true,
    }),
    true,
  )

  assert.equal(
    canReuseAuthReadyPromise({
      isAuthenticated: true,
      accessToken: 'token-b',
      cachedToken: 'token-a',
      hasPromise: true,
    }),
    false,
  )
})

test('旧 token 的鉴权重试在新 token 生效后应视为过期结果', () => {
  assert.equal(
    isStaleAuthReadyRun({
      runId: 1,
      activeRunId: 2,
      expectedToken: 'old-token',
      currentToken: 'new-token',
      isAuthenticated: true,
    }),
    true,
  )

  assert.equal(
    isStaleAuthReadyRun({
      runId: 2,
      activeRunId: 2,
      expectedToken: 'current-token',
      currentToken: 'current-token',
      isAuthenticated: true,
    }),
    false,
  )
})

test('首次未完成加载时应保持 loading 而非展示空列表', () => {
  assert.equal(
    getProjectListViewState({
      loading: false,
      hasLoadedOnce: false,
      error: null,
      projectsCount: 0,
      filteredProjectsCount: 0,
    }),
    'loading',
  )

  assert.equal(
    getProjectListViewState({
      loading: false,
      hasLoadedOnce: true,
      error: '加载失败',
      projectsCount: 0,
      filteredProjectsCount: 0,
    }),
    'load_error',
  )

  assert.equal(
    getProjectListViewState({
      loading: false,
      hasLoadedOnce: true,
      error: null,
      projectsCount: 0,
      filteredProjectsCount: 0,
    }),
    'empty_all',
  )
})

test('登录身份变化或登出时应清空项目列表状态', () => {
  assert.equal(shouldResetProjectStateForAuthChange(1, 2, true), true)
  assert.equal(shouldResetProjectStateForAuthChange(1, null, false), true)
  assert.equal(shouldResetProjectStateForAuthChange(2, 2, true), false)
})
