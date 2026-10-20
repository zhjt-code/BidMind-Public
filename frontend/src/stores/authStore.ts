/**
 * 用户认证状态管理
 *
 * 管理用户登录状态、token 存储和刷新逻辑。
 * persist 按 rememberMe 写入 localStorage（7 天免登录）或 sessionStorage（关浏览器即退出）。
 *
 * 重要: Zustand v5 persist 的自动 hydrate 在全页面刷新时不可靠，
 * 因此采用手动读取初始状态 + skipHydration 的方案。
 */

import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import { clearProjectListPageMemory } from './projectListPageMemory'
import {
  AUTH_STORAGE_KEY,
  createAuthWebStorage,
  parseAuthPersistState,
  readAuthPersistValue,
} from './authStorage'

export { AUTH_STORAGE_KEY } from './authStorage'

export interface User {
  id: number
  username: string
  email: string
  role: 'user' | 'admin'
  isActive: boolean
  isOnline?: boolean
  lastLogin?: string
  createdAt: string
  welcomeLetterAcknowledgedAt?: string | null
}

interface TokenInfo {
  accessToken: string
  refreshToken: string
  tokenType: string
}

interface AuthState {
  user: User | null
  accessToken: string | null
  refreshToken: string | null
  isAuthenticated: boolean
  isLoading: boolean
  error: string | null
  rememberMe: boolean

  setUser: (user: User | null) => void
  setTokens: (tokens: TokenInfo, rememberMe?: boolean) => void
  setError: (error: string | null) => void
  setLoading: (loading: boolean) => void
  login: (user: User, tokens: TokenInfo, rememberMe?: boolean) => void
  logout: () => void
  updateUser: (user: Partial<User>) => void
  clearError: () => void
}

const EMPTY_AUTH = {
  user: null as User | null,
  accessToken: null as string | null,
  refreshToken: null as string | null,
  isAuthenticated: false,
  rememberMe: false,
}

function loadPersistedAuthState(): typeof EMPTY_AUTH {
  try {
    const stored = readAuthPersistValue(AUTH_STORAGE_KEY, localStorage, sessionStorage)
    if (!stored) return EMPTY_AUTH
    const parsed = parseAuthPersistState(stored.serialized, stored.source)
    if (!parsed) return EMPTY_AUTH
    return {
      user: (parsed.user as User | null) || null,
      accessToken: parsed.accessToken,
      refreshToken: parsed.refreshToken,
      isAuthenticated: parsed.isAuthenticated,
      rememberMe: parsed.rememberMe,
    }
  } catch {
    return EMPTY_AUTH
  }
}

const restoredState = loadPersistedAuthState()

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: restoredState.user,
      accessToken: restoredState.accessToken,
      refreshToken: restoredState.refreshToken,
      isAuthenticated: restoredState.isAuthenticated,
      rememberMe: restoredState.rememberMe,
      isLoading: false,
      error: null,

      setUser: (user) => set({ user, isAuthenticated: !!user }),

      setTokens: (tokens, rememberMe) => set((state) => ({
        accessToken: tokens.accessToken,
        refreshToken: tokens.refreshToken,
        rememberMe: rememberMe ?? state.rememberMe,
      })),

      setError: (error) => set({ error }),

      setLoading: (isLoading) => set({ isLoading }),

      login: (user, tokens, rememberMe) => set((state) => ({
        user,
        accessToken: tokens.accessToken,
        refreshToken: tokens.refreshToken,
        isAuthenticated: true,
        error: null,
        rememberMe: rememberMe ?? state.rememberMe,
      })),

      logout: () => {
        clearProjectListPageMemory()
        set({
          user: null,
          accessToken: null,
          refreshToken: null,
          isAuthenticated: false,
          error: null,
          rememberMe: false,
        })
      },

      updateUser: (userData) => set((state) => ({
        user: state.user ? { ...state.user, ...userData } : null,
      })),

      clearError: () => set({ error: null }),
    }),
    {
      name: AUTH_STORAGE_KEY,
      storage: createJSONStorage(() => createAuthWebStorage()),
      skipHydration: true,
      partialize: (state) => ({
        user: state.user,
        accessToken: state.accessToken,
        refreshToken: state.refreshToken,
        isAuthenticated: state.isAuthenticated,
        rememberMe: state.rememberMe,
      }),
    }
  )
)
