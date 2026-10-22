import { api } from './httpClient'
import type { ApiResponse } from './apiTypes'

interface UserResponse {
  id: number
  username: string
  email: string
  role: string
  is_active: boolean
  is_online?: boolean
  last_login?: string
  created_at: string
}

interface OnlineStatsResponse {
  online_count: number
  online_window_seconds: number
  degraded: boolean
}

const transformUser = (user: UserResponse) => ({
  id: user.id,
  username: user.username,
  email: user.email,
  role: user.role as 'user' | 'admin',
  isActive: user.is_active,
  isOnline: user.is_online ?? false,
  lastLogin: user.last_login,
  createdAt: user.created_at,
})

const transformOnlineStats = (stats: OnlineStatsResponse) => ({
  onlineCount: stats.online_count,
  onlineWindowSeconds: stats.online_window_seconds,
  degraded: stats.degraded,
})

export const userService = {
  createUser: async (data: { username: string; email: string; password: string; role?: 'user' | 'admin' }) => {
    const response = await api.post<ApiResponse<UserResponse>>('/users', data)
    return transformUser(response.data)
  },

  getUsers: async (params?: {
    page?: number
    pageSize?: number
    role?: string
    isActive?: boolean
    isOnline?: boolean
    search?: string
  }) => {
    const queryParams = new URLSearchParams()
    if (params?.page) queryParams.append('page', String(params.page))
    if (params?.pageSize) queryParams.append('page_size', String(params.pageSize))
    if (params?.role) queryParams.append('role', params.role)
    if (params?.isActive !== undefined) queryParams.append('is_active', String(params.isActive))
    if (params?.isOnline !== undefined) queryParams.append('is_online', String(params.isOnline))
    if (params?.search) queryParams.append('search', params.search)

    const url = `/users${queryParams.toString() ? `?${queryParams.toString()}` : ''}`
    const response = await api.get<ApiResponse<{
      items: UserResponse[]
      total: number
      page: number
      page_size: number
    }>>(url)

    return {
      items: response.data.items.map(transformUser),
      total: response.data.total,
      page: response.data.page,
      pageSize: response.data.page_size,
    }
  },

  getOnlineStats: async () => {
    const response = await api.get<ApiResponse<OnlineStatsResponse>>('/users/online-stats')
    return transformOnlineStats(response.data)
  },

  getUser: async (userId: number) => {
    const response = await api.get<ApiResponse<UserResponse>>(`/users/${userId}`)
    return transformUser(response.data)
  },

  updateUserStatus: async (userId: number, isActive: boolean) => {
    const response = await api.put<ApiResponse<UserResponse>>(`/users/${userId}/status`, {
      is_active: isActive,
    })
    return transformUser(response.data)
  },

  updateUserRole: async (userId: number, role: 'user' | 'admin') => {
    const response = await api.put<ApiResponse<UserResponse>>(`/users/${userId}/role`, { role })
    return transformUser(response.data)
  },

  resetUserPassword: async (userId: number): Promise<{ temporaryPassword: string }> => {
    const response = await api.post<ApiResponse<{ temporary_password: string }>>(`/users/${userId}/reset-password`)
    return { temporaryPassword: response.data.temporary_password }
  },

  deleteUser: async (userId: number) => {
    return api.delete(`/users/${userId}`)
  },
}
