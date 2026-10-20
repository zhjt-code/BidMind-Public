import { api } from './httpClient'
import type { ApiResponse } from './apiTypes'

interface UserResponse {
  id: number
  username: string
  email: string
  role: string
  is_active: boolean
  last_login?: string
  created_at: string
  welcome_letter_acknowledged_at?: string | null
}

interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
}

const transformUser = (user: UserResponse) => ({
  id: user.id,
  username: user.username,
  email: user.email,
  role: user.role as 'user' | 'admin',
  isActive: user.is_active,
  lastLogin: user.last_login,
  createdAt: user.created_at,
  welcomeLetterAcknowledgedAt: user.welcome_letter_acknowledged_at ?? null,
})

export const authService = {
  login: async (username: string, password: string) => {
    const response = await api.post<ApiResponse<TokenResponse>>('/auth/login', {
      username,
      password,
    }, { timeout: 15000 })
    return {
      accessToken: response.data.access_token,
      refreshToken: response.data.refresh_token,
      tokenType: response.data.token_type,
    }
  },

  register: async (
    username: string,
    email: string,
    password: string,
    confirmPassword: string,
    fingerprint?: string,
  ) => {
    const response = await api.post<ApiResponse<UserResponse>>('/auth/register', {
      username,
      email,
      password,
      confirm_password: confirmPassword,
      ...(fingerprint ? { fingerprint } : {}),
    })
    return transformUser(response.data)
  },

  verifyEmail: async (token: string) => {
    return api.post<ApiResponse<null>>('/auth/verify-email', { token })
  },

  resendVerification: async (email: string) => {
    return api.post<ApiResponse<null>>('/auth/resend-verification', { email })
  },

  forgotPassword: async (email: string) => {
    return api.post<ApiResponse<null>>('/auth/forgot-password', { email })
  },

  resetPassword: async (token: string, newPassword: string) => {
    return api.post<ApiResponse<null>>('/auth/reset-password', {
      token,
      new_password: newPassword,
      confirm_password: newPassword,
    })
  },

  refreshToken: async (refreshToken: string) => {
    const response = await api.post<ApiResponse<TokenResponse>>('/auth/refresh', {
      refresh_token: refreshToken,
    })
    return {
      accessToken: response.data.access_token,
      refreshToken: response.data.refresh_token,
      tokenType: response.data.token_type,
    }
  },

  getCurrentUser: async () => {
    const response = await api.get<ApiResponse<UserResponse>>('/auth/me')
    return transformUser(response.data)
  },

  updateCurrentUser: async (data: { email?: string; current_password?: string; new_password?: string }) => {
    const response = await api.put<ApiResponse<UserResponse>>('/auth/me', data)
    return transformUser(response.data)
  },

  changePassword: async (currentPassword: string, newPassword: string, confirmPassword: string) => {
    const response = await api.post<ApiResponse<TokenResponse>>('/auth/change-password', {
      current_password: currentPassword,
      new_password: newPassword,
      confirm_password: confirmPassword,
    })
    return {
      accessToken: response.data.access_token,
      refreshToken: response.data.refresh_token,
      tokenType: response.data.token_type,
    }
  },

  acknowledgeWelcomeLetter: async () => {
    const response = await api.put<ApiResponse<UserResponse>>(
      '/auth/me/welcome-letter-acknowledgement',
    )
    return transformUser(response.data)
  },
}
