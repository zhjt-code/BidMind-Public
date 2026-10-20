/**
 * 邮箱验证页面
 *
 * 从 URL ?token= 读取验证令牌, 调用后端校验并激活账户。
 */

import React, { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { authService } from '@/services/authApi'
import AuthLayout from '@/components/AuthLayout'

type Status = 'verifying' | 'success' | 'error'

const VerifyEmailPage: React.FC = () => {
  const [params] = useSearchParams()
  const token = params.get('token') || ''
  const [status, setStatus] = useState<Status>('verifying')
  const [message, setMessage] = useState('')
  // 防 StrictMode 双调用导致令牌被消费两次而误报失败
  const ranRef = useRef(false)

  useEffect(() => {
    if (ranRef.current) return
    ranRef.current = true

    if (!token) {
      setStatus('error')
      setMessage('验证链接无效，缺少令牌')
      return
    }

    authService
      .verifyEmail(token)
      .then(() => setStatus('success'))
      .catch((e: unknown) => {
        setStatus('error')
        setMessage(e instanceof Error ? e.message : '验证失败，请重试')
      })
  }, [token])

  return (
    <AuthLayout>
      <div className="text-center py-8">
        {status === 'verifying' && (
          <>
            <div className="rounded-full bg-blue-100 w-16 h-16 flex items-center justify-center mx-auto mb-4">
              <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
            </div>
            <h2 className="text-2xl font-bold text-slate-900 mb-2">正在验证邮箱...</h2>
            <p className="text-slate-600">请稍候</p>
          </>
        )}

        {status === 'success' && (
          <>
            <div className="rounded-full bg-green-100 w-16 h-16 flex items-center justify-center mx-auto mb-4">
              <svg className="w-8 h-8 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <h2 className="text-2xl font-bold text-slate-900 mb-2">邮箱验证成功</h2>
            <p className="text-slate-600 mb-4">您的账户已激活，现在可以登录使用了。</p>
            <Link to="/login" className="text-primary hover:text-blue-600 font-semibold transition-colors">
              前往登录
            </Link>
          </>
        )}

        {status === 'error' && (
          <>
            <div className="rounded-full bg-red-100 w-16 h-16 flex items-center justify-center mx-auto mb-4">
              <svg className="w-8 h-8 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </div>
            <h2 className="text-2xl font-bold text-slate-900 mb-2">验证失败</h2>
            <p className="text-slate-600 mb-4">{message || '验证链接无效或已过期。'}</p>
            <div className="flex justify-center gap-4 text-sm">
              <Link to="/login" className="text-primary hover:text-blue-600 font-semibold transition-colors">
                前往登录
              </Link>
              <Link to="/register" className="text-slate-500 hover:text-slate-700 font-semibold transition-colors">
                重新注册
              </Link>
            </div>
          </>
        )}
      </div>
    </AuthLayout>
  )
}

export default VerifyEmailPage
