/**
 * 职责: 按 rememberMe 把认证持久化路由到 localStorage 或 sessionStorage
 * 不做: 不发登录请求、不解析 JWT、不管理用户资料
 */

import type { StateStorage } from 'zustand/middleware'

export const AUTH_STORAGE_KEY = 'auth-storage'

export type AuthPersistSource = 'local' | 'session'

export interface AuthPersistSnapshot {
  user: unknown
  accessToken: string | null
  refreshToken: string | null
  isAuthenticated: boolean
  rememberMe: boolean
}

type ReadableStorage = Pick<Storage, 'getItem'>
type WritableStorage = Pick<Storage, 'setItem' | 'removeItem'>

export function shouldPersistAcrossBrowserSessions(serialized: string): boolean {
  try {
    return JSON.parse(serialized)?.state?.rememberMe === true
  } catch {
    return false
  }
}

export function writeAuthPersistValue(
  name: string,
  serialized: string,
  persistent: WritableStorage,
  session: WritableStorage,
): void {
  if (shouldPersistAcrossBrowserSessions(serialized)) {
    persistent.setItem(name, serialized)
    session.removeItem(name)
    return
  }
  session.setItem(name, serialized)
  persistent.removeItem(name)
}

export function readAuthPersistValue(
  name: string,
  persistent: ReadableStorage,
  session: ReadableStorage,
): { serialized: string; source: AuthPersistSource } | null {
  const localValue = persistent.getItem(name)
  if (localValue) return { serialized: localValue, source: 'local' }
  const sessionValue = session.getItem(name)
  if (sessionValue) return { serialized: sessionValue, source: 'session' }
  return null
}

export function clearAuthPersistValue(
  name: string,
  persistent: Pick<Storage, 'removeItem'>,
  session: Pick<Storage, 'removeItem'>,
): void {
  persistent.removeItem(name)
  session.removeItem(name)
}

export function parseAuthPersistState(
  serialized: string,
  source: AuthPersistSource,
): AuthPersistSnapshot | null {
  try {
    const state = JSON.parse(serialized)?.state
    if (!state?.isAuthenticated || !state?.accessToken) return null
    return {
      user: state.user || null,
      accessToken: state.accessToken,
      refreshToken: state.refreshToken || null,
      isAuthenticated: true,
      rememberMe: typeof state.rememberMe === 'boolean' ? state.rememberMe : source === 'local',
    }
  } catch {
    return null
  }
}

export function createAuthWebStorage(): StateStorage {
  return {
    getItem: (name) => {
      try {
        return readAuthPersistValue(name, localStorage, sessionStorage)?.serialized ?? null
      } catch {
        return null
      }
    },
    setItem: (name, value) => {
      try {
        writeAuthPersistValue(name, value, localStorage, sessionStorage)
      } catch {
        // 隐私模式等不可写存储：保持内存态，不打断登录
      }
    },
    removeItem: (name) => {
      try {
        clearAuthPersistValue(name, localStorage, sessionStorage)
      } catch {
        // ignore
      }
    },
  }
}
