import axios, { AxiosInstance, InternalAxiosRequestConfig, AxiosError } from 'axios'
import { toast } from 'sonner'
import { useAuthStore } from '@/stores'
import {
  ApiRequestError,
  extractStructuredApiError,
  getReadableErrorMessage,
  shouldShowMaintenanceMessage,
  SYSTEM_MAINTENANCE_MESSAGE,
} from './apiError'
import type { ApiResponse } from './apiTypes'

// /auth/refresh 接口最小可用响应；httpClient 仅在 401 流程内消费
interface TokenRefreshResponse {
  access_token: string
  refresh_token: string
  token_type: string
}

// SPA 导航句柄：由 App/main 通过 setNavigate() 注入，避免 401 时整页刷新
let navigateFn: ((path: string) => void) | null = null
export function setNavigate(fn: (path: string) => void) {
  navigateFn = fn
}
function navigateToLogin() {
  if (navigateFn) {
    navigateFn('/login')
  } else {
    window.location.href = '/login'
  }
}

// axios 响应拦截器已解包 response.data，因此各方法默认返回 T 而非 AxiosResponse<T>
export interface ApiInstance extends AxiosInstance {
  get<T = unknown>(url: string, config?: object): Promise<T>
  post<T = unknown>(url: string, data?: unknown, config?: object): Promise<T>
  put<T = unknown>(url: string, data?: unknown, config?: object): Promise<T>
  patch<T = unknown>(url: string, data?: unknown, config?: object): Promise<T>
  delete<T = unknown>(url: string, config?: object): Promise<T>
}

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
}) as ApiInstance

// 单飞 refresh：并发 401 共享同一次 /auth/refresh
let refreshingPromise: Promise<string> | null = null

// 请求拦截器：自动附加 Bearer Token
api.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const { accessToken } = useAuthStore.getState()

    if (accessToken) {
      config.headers.Authorization = `Bearer ${accessToken}`
    }

    return config
  },
  (error) => {
    return Promise.reject(error)
  },
)

// 响应拦截器：解包 + 认证刷新 + 瞬态网络重试 + 维护提示
api.interceptors.response.use(
  (response) => {
    return response.data
  },
  async (error: AxiosError) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & {
      _retry?: boolean
      _networkRetry?: boolean
    }

    const isAuthEndpoint = originalRequest?.url?.includes('/auth/login') ||
                           originalRequest?.url?.includes('/auth/register')

    if (isAuthEndpoint) {
      const statusCode = error.response?.status
      if (statusCode === 401 || statusCode === 403 || statusCode === 404 || statusCode === 400) {
        const errorData = error.response?.data as { detail?: string | { msg: string }[]; message?: string }
        let message = '操作失败'
        if (Array.isArray(errorData?.detail)) {
          message = errorData.detail.map((d) => (typeof d === 'object' ? d.msg : d)).join('; ')
        } else if (typeof errorData?.detail === 'string') {
          message = errorData.detail
        } else if (errorData?.message) {
          message = errorData.message
        }
        return Promise.reject(new Error(message))
      }
      if (!error.response) {
        const isTimeout = error.code === 'ECONNABORTED'
        const msg = isTimeout ? '登录请求超时，请检查网络后重试' : '无法连接到服务器，请检查网络连接后重试'
        return Promise.reject(new Error(msg))
      }
    }

    // 瞬态网络错误自动重试一次（覆盖后端 --reload 重启窗口）
    const isTransientError = !error.response && error.code !== 'ECONNABORTED'
    if (isTransientError && !isAuthEndpoint && !originalRequest._networkRetry) {
      originalRequest._networkRetry = true
      await new Promise(r => setTimeout(r, 2000))
      return api(originalRequest)
    }

    // 502/503/504 视为后端维护，统一 toast 提示
    const maintenanceStatus = error.response?.status
    if (shouldShowMaintenanceMessage(maintenanceStatus, error.response?.data)) {
      toast.warning(SYSTEM_MAINTENANCE_MESSAGE, {
        id: 'system-maintenance',
        duration: 8000,
      })
      return Promise.reject(new Error(SYSTEM_MAINTENANCE_MESSAGE))
    }

    // 401 处理：单飞 Promise 刷新 token
    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true
      const { refreshToken, logout } = useAuthStore.getState()

      if (!refreshToken) {
        logout()
        navigateToLogin()
        return Promise.reject(error)
      }

      if (!refreshingPromise) {
        refreshingPromise = (async () => {
          try {
            const resp = await axios.post<ApiResponse<TokenRefreshResponse>>('/api/auth/refresh', {
              refresh_token: refreshToken,
            })
            const { access_token, refresh_token: newRefresh } = resp.data.data
            useAuthStore.getState().setTokens({
              accessToken: access_token,
              refreshToken: newRefresh,
              tokenType: 'bearer',
            })
            return access_token
          } catch (refreshError) {
            useAuthStore.getState().logout()
            navigateToLogin()
            throw refreshError
          } finally {
            refreshingPromise = null
          }
        })()
      }

      try {
        const newToken = await refreshingPromise
        originalRequest.headers.Authorization = `Bearer ${newToken}`
        return api(originalRequest)
      } catch (refreshError) {
        return Promise.reject(refreshError)
      }
    }

    // 403：结构化业务错误（如余额不足）优先，其余保持权限不足语义
    if (error.response?.status === 403) {
      const structured = extractStructuredApiError(error.response.data)
      if (structured) {
        return Promise.reject(new ApiRequestError(structured.message, 403, {
          errorCode: structured.errorCode,
          details: structured.details,
        }))
      }
      const detail = (error.response.data as { detail?: string })?.detail || '权限不足'
      console.error('Permission denied:', detail)
      return Promise.reject(new Error(detail))
    }

    const message = getReadableErrorMessage(error, '请求失败')
    console.error('API Error:', message)
    return Promise.reject(new ApiRequestError(message, error.response?.status))
  },
)

export { api }
export default api
