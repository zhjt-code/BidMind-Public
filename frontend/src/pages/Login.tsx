/**
 * 用户登录页面
 *
 * 支持用户名/邮箱登录，表单验证，弹窗错误提示。
 */

import React, { useState, useEffect } from 'react'
import { Link, useNavigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '@/stores'
import { authService } from '@/services/authApi'
import AuthLayout from '@/components/AuthLayout'
import { APP_HOME } from '@/config/routes'

type ErrorDialogType = 'password' | 'notfound' | 'locked' | 'disabled' | 'unverified' | 'general' | null

interface ErrorDialogInfo {
  type: ErrorDialogType
  title: string
  message: string
  showRegister?: boolean
  showResend?: boolean
}

const LoginPage: React.FC = () => {
  const navigate = useNavigate()
  const location = useLocation()
  const { login, isAuthenticated, setLoading, setError, isLoading, clearError } = useAuthStore()

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [rememberMe, setRememberMe] = useState(false)
  const [formError, setFormError] = useState('')
  const [errorDialog, setErrorDialog] = useState<ErrorDialogInfo | null>(null)
  const [resendState, setResendState] = useState<'idle' | 'sent'>('idle')

  const from = (location.state as { from?: string })?.from || APP_HOME

  useEffect(() => {
    if (isAuthenticated) {
      navigate(from, { replace: true })
    }
  }, [isAuthenticated, navigate, from])

  useEffect(() => {
    return () => {
      clearError()
      setLoading(false)
    }
  }, [clearError, setLoading])

  const validateForm = (): boolean => {
    if (!username.trim()) {
      setFormError('请输入用户名或邮箱')
      return false
    }
    if (!password) {
      setFormError('请输入密码')
      return false
    }
    setFormError('')
    return true
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    if (!validateForm()) return

    setLoading(true)
    setError(null)
    setFormError('')
    setErrorDialog(null)

    try {
      const tokens = await authService.login(username, password)

      useAuthStore.getState().setTokens(tokens, rememberMe)

      try {
        const user = await authService.getCurrentUser()
        login(user, tokens, rememberMe)
        navigate(from, { replace: true })
      } catch (userErr) {
        useAuthStore.getState().logout()
        throw userErr
      }
    } catch (err) {
      useAuthStore.getState().logout()

      if (err instanceof Error) {
        const errMsg = err.message

        if (errMsg.includes('尚未注册') || errMsg.includes('不存在') || errMsg.includes('未注册')) {
          setErrorDialog({
            type: 'notfound',
            title: '用户未注册',
            message: '该账户尚未注册，请先创建账户后再登录',
            showRegister: true
          })
        } else if (errMsg.includes('锁定')) {
          const minuteMatch = errMsg.match(/(\d+)分钟/)
          const minutes = minuteMatch ? minuteMatch[1] : '15'
          setErrorDialog({
            type: 'locked',
            title: '账户已锁定',
            message: `由于多次登录失败，您的账户已被临时锁定，请${minutes}分钟后再试`
          })
        } else if (errMsg.includes('禁用')) {
          setErrorDialog({
            type: 'disabled',
            title: '账户已禁用',
            message: '您的账户已被管理员禁用，如有疑问请联系管理员'
          })
        } else if (errMsg.includes('未验证') || errMsg.includes('验证邮件')) {
          setResendState('idle')
          setErrorDialog({
            type: 'unverified',
            title: '邮箱未验证',
            message: '该账户邮箱尚未验证，请查收注册邮箱的验证邮件并完成验证后再登录。',
            showResend: true,
          })
        } else if (errMsg.includes('用户名或密码错误') && !errMsg.includes('还剩')) {
          setErrorDialog({
            type: 'general',
            title: '登录失败',
            message: '用户名或密码错误，请检查后重试'
          })
        } else if (errMsg.includes('密码错误') || errMsg.includes('还剩')) {
          const retryMatch = errMsg.match(/还剩(\d+)次/)
          const retryCount = retryMatch ? retryMatch[1] : null
          setErrorDialog({
            type: 'password',
            title: '密码错误',
            message: retryCount
              ? `您输入的密码不正确，还剩 ${retryCount} 次尝试机会`
              : '您输入的密码不正确，请检查后重试'
          })
        } else {
          setErrorDialog({
            type: 'general',
            title: '登录失败',
            message: errMsg || '登录失败，请稍后重试'
          })
        }
      } else {
        setErrorDialog({
          type: 'general',
          title: '网络错误',
          message: '无法连接到服务器，请检查网络连接后重试'
        })
      }
    } finally {
      setLoading(false)
    }
  }

  const closeErrorDialog = () => {
    setErrorDialog(null)
  }

  const goToRegister = () => {
    setErrorDialog(null)
    navigate('/register')
  }

  const handleResendVerification = async () => {
    // 登录入口的输入既可能是用户名也可能是邮箱; 仅当为邮箱时可直接重发
    if (!username.includes('@')) {
      setErrorDialog((d) =>
        d ? { ...d, message: '请使用注册邮箱登录以重新发送验证邮件。' } : d,
      )
      return
    }
    try {
      await authService.resendVerification(username)
    } catch {
      // 防枚举: 后端始终返回成功, 这里也不暴露差异
    }
    setResendState('sent')
  }

  return (
    <AuthLayout>
      {/* 错误弹窗 */}
      {errorDialog && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-50">
          <div className="bg-white rounded-2xl p-6 w-96 max-w-[90vw] shadow-xl">
            <div className="flex justify-center mb-4">
              {errorDialog.type === 'password' && (
                <div className="w-16 h-16 rounded-full bg-yellow-100 flex items-center justify-center">
                  <svg className="w-8 h-8 text-yellow-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                  </svg>
                </div>
              )}
              {errorDialog.type === 'notfound' && (
                <div className="w-16 h-16 rounded-full bg-blue-100 flex items-center justify-center">
                  <svg className="w-8 h-8 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                  </svg>
                </div>
              )}
              {errorDialog.type === 'locked' && (
                <div className="w-16 h-16 rounded-full bg-red-100 flex items-center justify-center">
                  <svg className="w-8 h-8 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                  </svg>
                </div>
              )}
              {errorDialog.type === 'disabled' && (
                <div className="w-16 h-16 rounded-full bg-gray-100 flex items-center justify-center">
                  <svg className="w-8 h-8 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />
                  </svg>
                </div>
              )}
              {errorDialog.type === 'unverified' && (
                <div className="w-16 h-16 rounded-full bg-blue-100 flex items-center justify-center">
                  <svg className="w-8 h-8 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                  </svg>
                </div>
              )}
              {errorDialog.type === 'general' && (
                <div className="w-16 h-16 rounded-full bg-red-100 flex items-center justify-center">
                  <svg className="w-8 h-8 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </div>
              )}
            </div>

            <h2 className="text-xl font-bold text-center text-gray-900 mb-2">
              {errorDialog.title}
            </h2>

            <p className="text-gray-600 text-center mb-6">
              {errorDialog.message}
            </p>

            <div className="flex justify-center space-x-3">
              {errorDialog.showRegister ? (
                <>
                  <button
                    onClick={closeErrorDialog}
                    className="px-4 py-2 text-gray-600 hover:text-gray-800 border border-gray-300 rounded-xl transition-all duration-200"
                  >
                    返回
                  </button>
                  <button
                    onClick={goToRegister}
                    className="px-4 py-2 bg-primary text-white rounded-xl hover:bg-blue-600 transition-all duration-200"
                  >
                    立即注册
                  </button>
                </>
              ) : errorDialog.showResend ? (
                <>
                  <button
                    onClick={closeErrorDialog}
                    className="px-4 py-2 text-gray-600 hover:text-gray-800 border border-gray-300 rounded-xl transition-all duration-200"
                  >
                    关闭
                  </button>
                  <button
                    onClick={handleResendVerification}
                    disabled={resendState === 'sent'}
                    className="px-4 py-2 bg-primary text-white rounded-xl hover:bg-blue-600 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {resendState === 'sent' ? '已发送' : '重新发送验证邮件'}
                  </button>
                </>
              ) : (
                <button
                  onClick={closeErrorDialog}
                  className="px-6 py-2 bg-primary text-white rounded-xl hover:bg-blue-600 transition-all duration-200"
                >
                  知道了
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      <div className="space-y-8">
        {/* 标题区域 */}
        <div className="text-center space-y-4">
          <div className="w-12 h-12 bg-primary/10 rounded-xl flex items-center justify-center mx-auto">
            <svg className="w-6 h-6 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
            </svg>
          </div>
          <div className="space-y-1.5">
            <h2 className="text-2xl font-bold text-slate-900 tracking-tight">
              欢迎回来
            </h2>
            <p className="text-sm text-slate-500 font-medium">
              登录 BidMind AI 以继续使用
            </p>
          </div>
        </div>

        {/* 登录表单 */}
        <form className="space-y-6" onSubmit={handleSubmit}>
          {formError && (
            <div className="p-3 bg-yellow-50 border border-yellow-200 rounded-xl text-yellow-700 text-sm flex items-center">
              <svg className="w-5 h-5 mr-2 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
              {formError}
            </div>
          )}

          <div className="space-y-5">
            {/* 用户名/邮箱 */}
            <div className="space-y-1.5">
              <label htmlFor="username" className="block text-sm font-semibold text-slate-700">
                用户名或邮箱
              </label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none">
                  <svg className="w-5 h-5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                  </svg>
                </div>
                <input
                  id="username"
                  name="username"
                  type="text"
                  autoComplete="username"
                  required
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  onInvalid={(e) => (e.target as HTMLInputElement).setCustomValidity((e.target as HTMLInputElement).validity.valueMissing ? '请填写用户名或邮箱' : '')}
                  onInput={(e) => (e.currentTarget as HTMLInputElement).setCustomValidity('')}
                  className="w-full pl-11 pr-4 py-3 bg-slate-50 border border-slate-200 rounded-xl text-slate-900 placeholder-slate-400 focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all duration-200 outline-none text-base"
                  placeholder="请输入用户名或邮箱"
                />
              </div>
            </div>

            {/* 密码 */}
            <div className="space-y-1.5">
              <label htmlFor="password" className="block text-sm font-semibold text-slate-700">
                密码
              </label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none">
                  <svg className="w-5 h-5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                  </svg>
                </div>
                <input
                  id="password"
                  name="password"
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onInvalid={(e) => (e.target as HTMLInputElement).setCustomValidity((e.target as HTMLInputElement).validity.valueMissing ? '请填写密码' : '')}
                  onInput={(e) => (e.currentTarget as HTMLInputElement).setCustomValidity('')}
                  className="w-full pl-11 pr-12 py-3 bg-slate-50 border border-slate-200 rounded-xl text-slate-900 placeholder-slate-400 focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all duration-200 outline-none text-base"
                  placeholder="请输入密码"
                />
                <button
                  type="button"
                  aria-label={showPassword ? '隐藏密码' : '显示密码'}
                  title={showPassword ? '隐藏密码' : '显示密码'}
                  onClick={() => setShowPassword((current) => !current)}
                  className="absolute inset-y-0 right-0 pr-3.5 flex items-center text-slate-400 hover:text-slate-600 focus:outline-none focus:text-primary transition-colors"
                >
                  {showPassword ? (
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 3l18 18M10.58 10.58A2 2 0 0012 14a2 2 0 001.42-.58M9.88 4.24A9.53 9.53 0 0112 4c5.52 0 9 5.5 9 8a7.84 7.84 0 01-1.64 3.6M6.6 6.6C4.28 8.13 3 10.42 3 12c0 2.5 3.48 8 9 8a9.46 9.46 0 004.28-1.02" />
                    </svg>
                  ) : (
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M2.25 12s3.75-7 9.75-7 9.75 7 9.75 7-3.75 7-9.75 7-9.75-7-9.75-7z" />
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 15a3 3 0 100-6 3 3 0 000 6z" />
                    </svg>
                  )}
                </button>
              </div>
            </div>
          </div>

          <div className="flex items-center justify-between gap-3 -mt-2">
            <label
              htmlFor="remember-me"
              title="勾选后关闭浏览器仍保持登录，最长 7 天"
              className="inline-flex min-w-0 cursor-pointer items-center gap-2 text-sm font-medium text-slate-500 select-none"
            >
              <input
                id="remember-me"
                name="rememberMe"
                type="checkbox"
                checked={rememberMe}
                onChange={(e) => setRememberMe(e.target.checked)}
                className="h-4 w-4 shrink-0 rounded border-slate-300 text-primary accent-primary focus:ring-primary/20"
              />
              7天免登录
            </label>
            <Link
              to="/forgot-password"
              className="shrink-0 text-sm font-medium text-slate-500 hover:text-primary transition-colors"
            >
              忘记密码？
            </Link>
          </div>

          {/* 登录按钮 */}
          <button
            type="submit"
            disabled={isLoading}
            className="w-full flex justify-center py-3.5 px-4 border border-transparent rounded-xl shadow-md text-base font-semibold text-white bg-primary hover:bg-blue-600 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200 mt-2"
          >
            {isLoading ? (
              <span className="flex items-center">
                <svg className="animate-spin -ml-1 mr-3 h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                登录中...
              </span>
            ) : (
              '登录'
            )}
          </button>

          {/* 注册链接 */}
          <div className="text-center text-sm pt-2">
            <span className="text-slate-500 font-medium">还没有账户？</span>
            <Link
              to="/register"
              className="ml-1.5 font-semibold text-primary hover:text-blue-600 transition-colors"
            >
              立即注册
            </Link>
          </div>
        </form>
      </div>
    </AuthLayout>
  )
}

export default LoginPage
