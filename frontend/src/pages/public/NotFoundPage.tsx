import React from 'react'
import { Link } from 'react-router-dom'
import { APP_HOME, PUBLIC_HOME } from '@/config/routes'
import { useAuthStore } from '@/stores'

const NotFoundPage: React.FC = () => {
  const { isAuthenticated } = useAuthStore()

  return (
    <section className="bg-white py-20 sm:py-28">
      <div className="mx-auto max-w-2xl px-4 text-center sm:px-6 lg:px-8">
        <p className="text-sm font-semibold text-primary">404</p>
        <h1 className="mt-3 text-4xl font-bold tracking-tight text-slate-950">页面不存在</h1>
        <p className="mt-4 text-base leading-7 text-slate-600">
          你访问的页面可能已移动或不存在。可以返回公开首页，或进入已登录的工作台。
        </p>
        <div className="mt-8 flex flex-col justify-center gap-3 sm:flex-row">
          <Link
            to={PUBLIC_HOME}
            className="inline-flex h-11 items-center justify-center rounded-xl border border-slate-300 bg-white px-5 text-sm font-semibold text-slate-700 transition-colors hover:bg-slate-50"
          >
            返回首页
          </Link>
          {isAuthenticated && (
            <Link
              to={APP_HOME}
              className="inline-flex h-11 items-center justify-center rounded-xl bg-primary px-5 text-sm font-semibold text-white transition-colors hover:bg-primary/90"
            >
              进入工作台
            </Link>
          )}
        </div>
      </div>
    </section>
  )
}

export default NotFoundPage
