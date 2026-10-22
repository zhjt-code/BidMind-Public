/**
 * 用户注册页面
 *
 * 支持用户注册，表单验证，密码强度检查。
 */

import React, { useState, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuthStore } from '@/stores'
import { authService } from '@/services/authApi'
import AuthLayout from '@/components/AuthLayout'
import { isEmailVerificationEnabled } from '@/config/features'
import { getDeviceFingerprint } from '@/utils/fingerprint'
import { APP_HOME } from '@/config/routes'

const RegisterPage: React.FC = () => {
  const navigate = useNavigate()
  const { isAuthenticated, setLoading, setError, error, isLoading, clearError } = useAuthStore()

  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirmPassword, setShowConfirmPassword] = useState(false)
  const [formErrors, setFormErrors] = useState<Record<string, string>>({})
  const [success, setSuccess] = useState(false)
  const [resent, setResent] = useState(false)
  const redirectTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (isAuthenticated) {
      navigate(APP_HOME, { replace: true })
    }
  }, [isAuthenticated, navigate])

  useEffect(() => {
    return () => {
      clearError()
      if (redirectTimerRef.current) clearTimeout(redirectTimerRef.current)
    }
  }, [clearError])

  const validateForm = (): boolean => {
    const errors: Record<string, string> = {}

    if (!username.trim()) {
      errors.username = '请输入用户名'
    } else if (username.length < 3) {
      errors.username = '用户名至少3个字符'
    } else if (!/^[a-zA-Z][a-zA-Z0-9_]*$/.test(username)) {
      errors.username = '用户名只能包含字母、数字、下划线，且必须以字母开头'
    }

    if (!email.trim()) {
      errors.email = '请输入邮箱'
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      errors.email = '请输入有效的邮箱地址'
    }

    if (!password) {
      errors.password = '请输入密码'
    } else if (password.length < 8) {
      errors.password = '密码至少8个字符'
    } else if (!/[a-zA-Z]/.test(password)) {
      errors.password = '密码必须包含字母'
    } else if (!/\d/.test(password)) {
      errors.password = '密码必须包含数字'
    }

    if (!confirmPassword) {
      errors.confirmPassword = '请确认密码'
    } else if (password !== confirmPassword) {
      errors.confirmPassword = '两次输入的密码不一致'
    }

    setFormErrors(errors)
    return Object.keys(errors).length === 0
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    if (!validateForm()) return

    setLoading(true)
    setError(null)

    try {
      const fingerprint = await getDeviceFingerprint().catch(() => '')
      await authService.register(username, email, password, confirmPassword, fingerprint)
      setSuccess(true)
      // 开启邮箱验证时, 用户需先验证才能登录, 不自动跳转
      if (!isEmailVerificationEnabled) {
        redirectTimerRef.current = setTimeout(() => {
          navigate('/login')
        }, 3000)
      }
    } catch (err) {
      let message = '注册失败，请稍后重试'
      if (err instanceof Error) {
        const errMsg = err.message.toLowerCase()
        if (errMsg.includes('用户名已') || errMsg.includes('username')) {
          message = '该用户名已被注册'
        } else if (errMsg.includes('邮箱已') || errMsg.includes('email')) {
          message = '该邮箱已被注册'
        } else {
          message = err.message || '注册失败，请稍后重试'
        }
      }
      setError(message)
    } finally {
      setLoading(false)
    }
  }

  const handleResend = async () => {
    try {
      await authService.resendVerification(email)
      setResent(true)
    } catch {
      setResent(true)
    }
  }

  if (success) {
    // 开启邮箱验证: 提示查收验证邮件, 不自动跳转
    if (isEmailVerificationEnabled) {
      return (
        <AuthLayout>
          <div className="text-center py-8">
            <div className="rounded-full bg-blue-100 w-16 h-16 flex items-center justify-center mx-auto mb-4">
              <svg className="w-8 h-8 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
              </svg>
            </div>
            <h2 className="text-2xl font-bold text-slate-900 mb-2">验证邮件已发送</h2>
            <p className="text-slate-600 mb-2">
              我们已向 <span className="font-semibold text-slate-900">{email}</span> 发送验证邮件，
              请查收并点击链接完成验证后登录。
            </p>
            <p className="text-xs text-slate-400 mb-4">没收到？请检查垃圾邮件箱。</p>
            <div className="flex justify-center gap-4 text-sm">
              <button
                type="button"
                onClick={handleResend}
                disabled={resent}
                className="font-semibold text-primary hover:text-blue-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {resent ? '已重新发送' : '重新发送验证邮件'}
              </button>
              <Link to="/login" className="font-semibold text-slate-500 hover:text-slate-700 transition-colors">
                前往登录
              </Link>
            </div>
          </div>
        </AuthLayout>
      )
    }

    return (
      <AuthLayout>
        <div className="text-center py-8">
          <div className="rounded-full bg-green-100 w-16 h-16 flex items-center justify-center mx-auto mb-4">
            <svg className="w-8 h-8 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <h2 className="text-2xl font-bold text-slate-900 mb-2">注册成功</h2>
          <p className="text-slate-600 mb-4">您的账户已创建，即将跳转到登录页面...</p>
          <Link
            to="/login"
            className="text-primary hover:text-blue-600 font-semibold transition-colors"
          >
            立即登录
          </Link>
        </div>
      </AuthLayout>
    )
  }

  const inputBaseClass = "w-full pl-11 pr-4 py-3 bg-slate-50 border rounded-xl text-slate-900 placeholder-slate-400 focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all duration-200 outline-none text-base"
  const passwordInputClass = "w-full pl-11 pr-12 py-3 bg-slate-50 border rounded-xl text-slate-900 placeholder-slate-400 focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all duration-200 outline-none text-base"

  return (
    <AuthLayout>
      <div className="space-y-6">
        {/* 标题区域 */}
        <div className="text-center space-y-4">
          <div className="w-12 h-12 bg-primary/10 rounded-xl flex items-center justify-center mx-auto">
            <svg className="w-6 h-6 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
            </svg>
          </div>
          <div className="space-y-1.5">
            <h2 className="text-2xl font-bold text-slate-900 tracking-tight">
              创建账户
            </h2>
            <p className="text-sm text-slate-500 font-medium">
              注册 BidMind AI 以开始使用
            </p>
          </div>
        </div>

        {/* 注册表单 */}
        <form className="space-y-5" onSubmit={handleSubmit}>
          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">
              {error}
            </div>
          )}

          <div className="space-y-4">
            {/* 用户名 */}
            <div className="space-y-1.5">
              <label htmlFor="username" className="block text-sm font-semibold text-slate-700">
                用户名
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
                  onInvalid={(e) => (e.target as HTMLInputElement).setCustomValidity((e.target as HTMLInputElement).validity.valueMissing ? '请填写用户名' : '')}
                  onInput={(e) => (e.currentTarget as HTMLInputElement).setCustomValidity('')}
                  className={`${inputBaseClass} ${formErrors.username ? 'border-red-300' : 'border-slate-200'}`}
                  placeholder="以字母开头，3-50个字符"
                />
              </div>
              {formErrors.username && (
                <p className="text-sm text-red-600">{formErrors.username}</p>
              )}
            </div>

            {/* 邮箱 */}
            <div className="space-y-1.5">
              <label htmlFor="email" className="block text-sm font-semibold text-slate-700">
                邮箱
              </label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none">
                  <svg className="w-5 h-5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                  </svg>
                </div>
                <input
                  id="email"
                  name="email"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  onInvalid={(e) => {
                    const input = e.target as HTMLInputElement
                    input.setCustomValidity(input.validity.valueMissing ? '请填写邮箱' : input.validity.typeMismatch ? '请填写正确的邮箱格式' : '')
                  }}
                  onInput={(e) => (e.currentTarget as HTMLInputElement).setCustomValidity('')}
                  className={`${inputBaseClass} ${formErrors.email ? 'border-red-300' : 'border-slate-200'}`}
                  placeholder="user@example.invalid"
                />
              </div>
              {formErrors.email && (
                <p className="text-sm text-red-600">{formErrors.email}</p>
              )}
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
                  autoComplete="new-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onInvalid={(e) => (e.target as HTMLInputElement).setCustomValidity((e.target as HTMLInputElement).validity.valueMissing ? '请填写密码' : '')}
                  onInput={(e) => (e.currentTarget as HTMLInputElement).setCustomValidity('')}
                  className={`${passwordInputClass} ${formErrors.password ? 'border-red-300' : 'border-slate-200'}`}
                  placeholder="至少8位，包含字母和数字"
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
              {formErrors.password && (
                <p className="text-sm text-red-600">{formErrors.password}</p>
              )}
            </div>

            {/* 确认密码 */}
            <div className="space-y-1.5">
              <label htmlFor="confirmPassword" className="block text-sm font-semibold text-slate-700">
                确认密码
              </label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none">
                  <svg className="w-5 h-5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                  </svg>
                </div>
                <input
                  id="confirmPassword"
                  name="confirmPassword"
                  type={showConfirmPassword ? 'text' : 'password'}
                  autoComplete="new-password"
                  required
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  onInvalid={(e) => (e.target as HTMLInputElement).setCustomValidity((e.target as HTMLInputElement).validity.valueMissing ? '请再次输入密码确认' : '')}
                  onInput={(e) => (e.currentTarget as HTMLInputElement).setCustomValidity('')}
                  className={`${passwordInputClass} ${formErrors.confirmPassword ? 'border-red-300' : 'border-slate-200'}`}
                  placeholder="请再次输入密码"
                />
                <button
                  type="button"
                  aria-label={showConfirmPassword ? '隐藏确认密码' : '显示确认密码'}
                  title={showConfirmPassword ? '隐藏确认密码' : '显示确认密码'}
                  onClick={() => setShowConfirmPassword((current) => !current)}
                  className="absolute inset-y-0 right-0 pr-3.5 flex items-center text-slate-400 hover:text-slate-600 focus:outline-none focus:text-primary transition-colors"
                >
                  {showConfirmPassword ? (
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
              {formErrors.confirmPassword && (
                <p className="text-sm text-red-600">{formErrors.confirmPassword}</p>
              )}
            </div>
          </div>

          {/* 注册按钮 */}
          <button
            type="submit"
            disabled={isLoading}
            className="w-full flex justify-center py-3.5 px-4 border border-transparent rounded-xl shadow-md text-base font-semibold text-white bg-primary hover:bg-blue-600 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200"
          >
            {isLoading ? (
              <span className="flex items-center">
                <svg className="animate-spin -ml-1 mr-3 h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                注册中...
              </span>
            ) : (
              '注册'
            )}
          </button>

          {/* 登录链接 */}
          <div className="text-center text-sm pt-1">
            <span className="text-slate-500 font-medium">已有账户？</span>
            <Link
              to="/login"
              className="ml-1.5 font-semibold text-primary hover:text-blue-600 transition-colors"
            >
              立即登录
            </Link>
          </div>
        </form>
      </div>
    </AuthLayout>
  )
}

export default RegisterPage
