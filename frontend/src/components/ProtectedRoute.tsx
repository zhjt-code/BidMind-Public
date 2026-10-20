/**
 * 路由守卫组件
 *
 * 保护需要认证的路由，未登录用户重定向到登录页。
 * 阻塞式门禁：token 验证完成前显示 loading，防止子组件用过期 token 发起请求。
 */

import React, { useEffect, useState } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '@/stores'
import { authService } from '@/services/authApi'
import { APP_HOME, LOGIN_PATH } from '@/config/routes'
import OnlinePresenceHeartbeat from './OnlinePresenceHeartbeat'
import WelcomeLetterDialog from './WelcomeLetterDialog'
import {
  canReuseAuthReadyPromise,
  isStaleAuthReadyRun,
  shouldResetAuthReadyCache,
} from './protectedRouteState'

interface ProtectedRouteProps {
  children: React.ReactNode
  requireAdmin?: boolean
}

// 模块级单例 Promise：多个 ProtectedRoute 实例共享同一次验证
let authReadyPromise: Promise<void> | null = null
let validatedAccessToken: string | null = null
let authReadyRunId = 0

const AUTH_RETRY_DELAY_MS = 3000
const AUTH_MAX_RETRIES = 10

function ensureAuthReady(): Promise<void> {
  const { isAuthenticated, accessToken } = useAuthStore.getState()
  if (!isAuthenticated || !accessToken) return Promise.resolve()
  if (canReuseAuthReadyPromise({
    isAuthenticated,
    accessToken,
    cachedToken: validatedAccessToken,
    hasPromise: !!authReadyPromise,
  })) {
    return authReadyPromise as Promise<void>
  }

  const currentRunId = ++authReadyRunId
  const expectedToken = accessToken
  validatedAccessToken = accessToken
  authReadyPromise = (async () => {
    for (let attempt = 0; attempt <= AUTH_MAX_RETRIES; attempt++) {
      try {
        const currentUser = await authService.getCurrentUser()
        const latestState = useAuthStore.getState()
        if (isStaleAuthReadyRun({
          runId: currentRunId,
          activeRunId: authReadyRunId,
          expectedToken,
          currentToken: latestState.accessToken,
          isAuthenticated: latestState.isAuthenticated,
        })) {
          return
        }
        useAuthStore.getState().setUser(currentUser as import('@/stores/authStore').User)
        return
      } catch {
        const latestState = useAuthStore.getState()
        if (isStaleAuthReadyRun({
          runId: currentRunId,
          activeRunId: authReadyRunId,
          expectedToken,
          currentToken: latestState.accessToken,
          isAuthenticated: latestState.isAuthenticated,
        })) {
          return
        }

        if (attempt < AUTH_MAX_RETRIES) {
          await new Promise(r => setTimeout(r, AUTH_RETRY_DELAY_MS))
        }
      }
    }
    const latestState = useAuthStore.getState()
    if (isStaleAuthReadyRun({
      runId: currentRunId,
      activeRunId: authReadyRunId,
      expectedToken,
      currentToken: latestState.accessToken,
      isAuthenticated: latestState.isAuthenticated,
    })) {
      return
    }
    latestState.logout()
  })()

  return authReadyPromise
}

const ProtectedRoute: React.FC<ProtectedRouteProps> = ({
  children,
  requireAdmin = false
}) => {
  const { isAuthenticated, accessToken, user } = useAuthStore()
  const location = useLocation()
  const [authChecked, setAuthChecked] = useState(false)

  useEffect(() => {
    if (!isAuthenticated) {
      setAuthChecked(true)
      return
    }

    let cancelled = false
    setAuthChecked(false)
    ensureAuthReady().finally(() => {
      if (!cancelled) setAuthChecked(true)
    })
    return () => { cancelled = true }
  }, [isAuthenticated, accessToken])

  if (!authChecked && isAuthenticated) {
    return (
      <div className="fixed inset-0 flex items-center justify-center bg-white/80">
        <svg
          className="animate-spin h-8 w-8 text-primary"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
        >
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
        </svg>
      </div>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to={LOGIN_PATH} state={{ from: location.pathname }} replace />
  }

  if (requireAdmin && user?.role !== 'admin') {
    return <Navigate to={APP_HOME} replace />
  }

  return (
    <>
      <OnlinePresenceHeartbeat />
      <WelcomeLetterDialog />
      {children}
    </>
  )
}

// token 变化或登出时重置缓存，下次访问重新执行鉴权准备
useAuthStore.subscribe((state, prevState) => {
  if (shouldResetAuthReadyCache(prevState, state)) {
    authReadyPromise = null
    validatedAccessToken = null
    authReadyRunId += 1
  }
})

export default ProtectedRoute
