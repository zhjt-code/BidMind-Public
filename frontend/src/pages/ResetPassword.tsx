/**
 * 重置密码页面
 *
 * 从 URL ?token= 读取重置令牌, 用户输入新密码完成重置。成功后旧会话立即失效。
 */

import React, { useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { authService } from '@/services/authApi'
import AuthLayout from '@/components/AuthLayout'

const ResetPasswordPage: React.FC = () => {
  const [params] = useSearchParams()
  const token = params.get('token') || ''
  const navigate = useNavigate()

  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [formError, setFormError] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [done, setDone] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  React.useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [])

  const validate = (): boolean => {
    if (password.length < 8) {
      setFormError('密码至少8个字符')
      return false
    }
    if (!/[a-zA-Z]/.test(password) || !/\d/.test(password)) {
      setFormError('密码必须包含字母和数字')
      return false
    }
    if (password !== confirm) {
      setFormError('两次输入的密码不一致')
      return false
    }
    setFormError('')
    return true
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!validate()) return
    setLoading(true)
    setError('')
    try {
      await authService.resetPassword(token, password)
      setDone(true)
      timerRef.current = setTimeout(() => navigate('/login'), 3000)
    } catch (err) {
      setError(err instanceof Error ? err.message : '重置失败，请重试')
    } finally {
      setLoading(false)
    }
  }

  if (!token) {
    return (
      <AuthLayout>
        <div className="text-center py-8">
          <div className="rounded-full bg-red-100 w-16 h-16 flex items-center justify-center mx-auto mb-4">
            <svg className="w-8 h-8 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </div>
          <h2 className="text-2xl font-bold text-slate-900 mb-2">链接无效</h2>
          <p className="text-slate-600 mb-4">重置链接缺少令牌或不完整，请重新发起找回密码。</p>
          <Link to="/forgot-password" className="text-primary hover:text-blue-600 font-semibold transition-colors">
            重新找回密码
          </Link>
        </div>
      </AuthLayout>
    )
  }

  if (done) {
    return (
      <AuthLayout>
        <div className="text-center py-8">
          <div className="rounded-full bg-green-100 w-16 h-16 flex items-center justify-center mx-auto mb-4">
            <svg className="w-8 h-8 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <h2 className="text-2xl font-bold text-slate-900 mb-2">密码重置成功</h2>
          <p className="text-slate-600 mb-4">请使用新密码登录，即将跳转到登录页面...</p>
          <Link to="/login" className="text-primary hover:text-blue-600 font-semibold transition-colors">
            立即登录
          </Link>
        </div>
      </AuthLayout>
    )
  }

  const inputBaseClass = "w-full pl-11 pr-4 py-3 bg-slate-50 border border-slate-200 rounded-xl text-slate-900 placeholder-slate-400 focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all duration-200 outline-hidden text-base"

  return (
    <AuthLayout>
      <div className="space-y-6">
        <div className="text-center space-y-4">
          <div className="w-12 h-12 bg-primary/10 rounded-xl flex items-center justify-center mx-auto">
            <svg className="w-6 h-6 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
          </div>
          <div className="space-y-1.5">
            <h2 className="text-2xl font-bold text-slate-900 tracking-tight">设置新密码</h2>
            <p className="text-sm text-slate-500 font-medium">重置后其他设备需重新登录</p>
          </div>
        </div>

        <form className="space-y-5" onSubmit={handleSubmit}>
          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">
              {error}
            </div>
          )}
          {formError && (
            <div className="p-3 bg-yellow-50 border border-yellow-200 rounded-xl text-yellow-700 text-sm">
              {formError}
            </div>
          )}

          <div className="space-y-1.5">
            <label htmlFor="password" className="block text-sm font-semibold text-slate-700">
              新密码
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
                type="password"
                autoComplete="new-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={inputBaseClass}
                placeholder="至少8位，包含字母和数字"
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <label htmlFor="confirm" className="block text-sm font-semibold text-slate-700">
              确认新密码
            </label>
            <div className="relative">
              <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none">
                <svg className="w-5 h-5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                </svg>
              </div>
              <input
                id="confirm"
                name="confirm"
                type="password"
                autoComplete="new-password"
                required
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                className={inputBaseClass}
                placeholder="请再次输入新密码"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full flex justify-center py-3.5 px-4 border border-transparent rounded-xl shadow-md text-base font-semibold text-white bg-primary hover:bg-blue-600 focus:outline-hidden focus:ring-2 focus:ring-offset-2 focus:ring-primary disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200"
          >
            {loading ? '提交中...' : '重置密码'}
          </button>

          <div className="text-center text-sm pt-1">
            <Link to="/login" className="font-semibold text-primary hover:text-blue-600 transition-colors">
              返回登录
            </Link>
          </div>
        </form>
      </div>
    </AuthLayout>
  )
}

export default ResetPasswordPage
