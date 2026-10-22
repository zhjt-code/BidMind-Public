/**
 * 忘记密码页面
 *
 * 输入注册邮箱 -> 后端发送重置链接。为防账号枚举, 无论邮箱是否存在均提示已发送。
 */

import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { authService } from '@/services/authApi'
import AuthLayout from '@/components/AuthLayout'

const ForgotPasswordPage: React.FC = () => {
  const [email, setEmail] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!email.trim()) {
      setError('请输入邮箱')
      return
    }
    setLoading(true)
    setError('')
    try {
      await authService.forgotPassword(email)
    } catch {
      // 防枚举: 后端始终返回成功; 仅网络异常会到这里, 仍统一提示
    } finally {
      setLoading(false)
      setSubmitted(true)
    }
  }

  if (submitted) {
    return (
      <AuthLayout>
        <div className="text-center py-8">
          <div className="rounded-full bg-blue-100 w-16 h-16 flex items-center justify-center mx-auto mb-4">
            <svg className="w-8 h-8 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
          </div>
          <h2 className="text-2xl font-bold text-slate-900 mb-2">重置邮件已发送</h2>
          <p className="text-slate-600 mb-2">
            若 <span className="font-semibold text-slate-900">{email}</span> 已注册，
            我们已发送密码重置邮件，请查收并点击链接设置新密码。
          </p>
          <p className="text-xs text-slate-400 mb-4">没收到？请检查垃圾邮件箱。</p>
          <Link to="/login" className="text-primary hover:text-blue-600 font-semibold transition-colors">
            返回登录
          </Link>
        </div>
      </AuthLayout>
    )
  }

  const inputBaseClass = "w-full pl-11 pr-4 py-3 bg-slate-50 border border-slate-200 rounded-xl text-slate-900 placeholder-slate-400 focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all duration-200 outline-none text-base"

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
            <h2 className="text-2xl font-bold text-slate-900 tracking-tight">找回密码</h2>
            <p className="text-sm text-slate-500 font-medium">输入注册邮箱，我们将发送重置链接</p>
          </div>
        </div>

        <form className="space-y-5" onSubmit={handleSubmit}>
          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">
              {error}
            </div>
          )}

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
                className={inputBaseClass}
                placeholder="user@example.invalid"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full flex justify-center py-3.5 px-4 border border-transparent rounded-xl shadow-md text-base font-semibold text-white bg-primary hover:bg-blue-600 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200"
          >
            {loading ? '发送中...' : '发送重置邮件'}
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

export default ForgotPasswordPage
