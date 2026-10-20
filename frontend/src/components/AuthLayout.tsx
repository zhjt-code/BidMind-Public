/**
 * 认证页面共享布局组件
 *
 * 左侧展示面板（品牌 + 数据卡片） + 右侧表单区域。
 * 移动端隐藏左栏，仅显示右侧表单（附顶部 Logo）。
 */

import React from 'react'
import { Link } from 'react-router-dom'
import { PUBLIC_HOME } from '@/config/routes'
import { siteConfig } from '@/config/site'
import Logo from './Logo'

interface AuthLayoutProps {
  children: React.ReactNode
}

const AuthLayout: React.FC<AuthLayoutProps> = ({ children }) => {
  return (
    <div className="min-h-screen bg-slate-100 flex items-center justify-center relative overflow-hidden antialiased">
      <div className="absolute inset-0 bg-gradient-to-br from-slate-200/80 to-slate-300/60 opacity-95 z-0" />

      <div className="relative z-10 w-full max-w-[1400px] mx-auto p-4 md:p-8 lg:p-12 md:grid md:grid-cols-[3fr_minmax(380px,2fr)] md:gap-8 lg:gap-10 md:items-start">
        {/* -------- 左栏：品牌展示 -------- */}
        <section className="hidden md:flex flex-col gap-6 overflow-hidden">
          {/* Logo */}
          <Link to={PUBLIC_HOME} className="flex-shrink-0 self-start transition-opacity hover:opacity-90" aria-label="返回首页">
            <Logo size="xl" />
          </Link>

          {/* 主体内容 */}
          <div className="flex flex-col justify-center max-w-2xl">
            <div className="space-y-8">
              {/* 标语 */}
              <div className="space-y-3">
                <h1 className="text-2xl lg:text-3xl font-extrabold tracking-tight leading-snug text-slate-900">
                  AI驱动 · 精准全面 · 评审高效
                </h1>
                <p className="text-sm text-slate-500 max-w-2xl leading-relaxed">
                  以AI赋能招投标全流程，提供标书编写、投标审查和串标识别一体化解决方案
                </p>
              </div>

              {/* 功能与安全 */}
              <div className="space-y-5">
                {/* 核心功能 */}
                <div className="grid grid-cols-3 gap-3">
                  <div className="flex items-start gap-3 p-4 rounded-2xl bg-white/50 border border-white/60 backdrop-blur-xl shadow-lg">
                    <div className="w-9 h-9 rounded-xl bg-teal-50 border border-teal-100/80 flex items-center justify-center flex-shrink-0">
                      <svg className="w-[18px] h-[18px] text-teal-600" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M12 20h9" />
                        <path d="M16.5 3.5a2.12 2.12 0 013 3L7 19l-4 1 1-4L16.5 3.5z" />
                      </svg>
                    </div>
                    <div>
                      <p className="text-sm font-bold text-slate-800 tracking-tight">标书编写</p>
                      <p className="text-xs text-slate-500 leading-relaxed mt-1">智能生成标书</p>
                    </div>
                  </div>
                  <div className="flex items-start gap-3 p-4 rounded-2xl bg-white/50 border border-white/60 backdrop-blur-xl shadow-lg">
                    <div className="w-9 h-9 rounded-xl bg-blue-50 border border-blue-100/80 flex items-center justify-center flex-shrink-0">
                      <svg className="w-[18px] h-[18px] text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2" />
                        <rect x="9" y="3" width="6" height="4" rx="1" />
                        <path d="M9 14l2 2 4-4" />
                      </svg>
                    </div>
                    <div>
                      <p className="text-sm font-bold text-slate-800 tracking-tight">投标审查</p>
                      <p className="text-xs text-slate-500 leading-relaxed mt-1">判定符合程度</p>
                    </div>
                  </div>
                  <div className="flex items-start gap-3 p-4 rounded-2xl bg-white/50 border border-white/60 backdrop-blur-xl shadow-lg">
                    <div className="w-9 h-9 rounded-xl bg-orange-50 border border-orange-100/80 flex items-center justify-center flex-shrink-0">
                      <svg className="w-[18px] h-[18px] text-orange-600" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M16 4h2a2 2 0 012 2v14a2 2 0 01-2 2H6a2 2 0 01-2-2V6a2 2 0 012-2h2" />
                        <path d="M15 2H9a1 1 0 00-1 1v2a1 1 0 001 1h6a1 1 0 001-1V3a1 1 0 00-1-1z" />
                        <path d="M8 12h8M8 16h8" />
                      </svg>
                    </div>
                    <div>
                      <p className="text-sm font-bold text-slate-800 tracking-tight">串标审查</p>
                      <p className="text-xs text-slate-500 leading-relaxed mt-1">识别潜在风险</p>
                    </div>
                  </div>
                </div>

                {/* 可核验能力 */}
                <div className="pt-4 border-t border-slate-200/70 space-y-3">
                  <h2 className="text-[11px] font-semibold text-slate-400 tracking-widest uppercase">
                    Deployment &middot; Access &middot; AI Mode
                  </h2>
                  <div className="grid grid-cols-3 gap-3">
                    <div className="flex flex-col items-center gap-2 p-3 rounded-xl bg-white/25 border border-slate-200/40 backdrop-blur-sm">
                      <div className="w-7 h-7 rounded-lg bg-emerald-50 border border-emerald-100/80 flex items-center justify-center">
                        <svg className="w-3.5 h-3.5 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                          <path d="M7 11V7a5 5 0 0110 0v4" />
                        </svg>
                      </div>
                      <p className="text-[11px] font-medium text-slate-600 text-center leading-tight">支持 HTTPS 部署</p>
                    </div>
                    <div className="flex flex-col items-center gap-2 p-3 rounded-xl bg-white/25 border border-slate-200/40 backdrop-blur-sm">
                      <div className="w-7 h-7 rounded-lg bg-emerald-50 border border-emerald-100/80 flex items-center justify-center">
                        <svg className="w-3.5 h-3.5 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                          <path d="M9 12l2 2 4-4" />
                        </svg>
                      </div>
                      <p className="text-[11px] font-medium text-slate-600 text-center leading-tight">项目访问控制</p>
                    </div>
                    <div className="flex flex-col items-center gap-2 p-3 rounded-xl bg-white/25 border border-slate-200/40 backdrop-blur-sm">
                      <div className="w-7 h-7 rounded-lg bg-emerald-50 border border-emerald-100/80 flex items-center justify-center">
                        <svg className="w-3.5 h-3.5 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M10.73 5.08A10.43 10.43 0 0112 5c7 0 10 7 10 7a13.16 13.16 0 01-1.67 2.68" />
                          <path d="M6.61 6.61A13.526 13.526 0 002 12s3 7 10 7a9.74 9.74 0 005.39-1.61" />
                          <path d="M9.88 9.88a3 3 0 104.24 4.24" />
                          <line x1="2" y1="2" x2="22" y2="22" />
                        </svg>
                      </div>
                      <p className="text-[11px] font-medium text-slate-600 text-center leading-tight">AI 模式可配置</p>
                    </div>
                  </div>
                </div>
              </div>

              {/* 展示面板 */}
              <ShowcasePanel />
            </div>
          </div>

          {/* 底部 */}
          <div className="flex-shrink-0">
            <div className="flex items-center justify-between text-xs font-medium">
              <div className="flex items-center gap-1.5 text-slate-600">
                <svg className="w-3 h-3 text-primary" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                </svg>
                <span>可配置部署与访问控制</span>
              </div>
              <p className="text-slate-400">
                &copy; 2026 BidMind AI
                {siteConfig.icpRecord ? (
                  <>
                    <span className="mx-1.5 text-slate-300">&middot;</span>
                    <a
                      href="https://beian.miit.gov.cn/"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="hover:text-slate-600 transition-colors"
                    >
                      {siteConfig.icpRecord}
                    </a>
                  </>
                ) : null}
              </p>
            </div>
          </div>
        </section>

        {/* -------- 右栏：表单区域 -------- */}
        <section className="w-full bg-white p-8 md:p-10 lg:p-12 flex flex-col justify-center rounded-3xl shadow-elevated relative z-20 self-center border border-slate-100/50 min-w-0">
          {/* 移动端 Logo */}
          <div className="md:hidden flex justify-center mb-8">
            <Link to={PUBLIC_HOME} className="transition-opacity hover:opacity-90" aria-label="返回首页">
              <Logo size="xl" />
            </Link>
          </div>

          <div className="w-full max-w-md mx-auto">
            {children}
          </div>
        </section>
      </div>
    </div>
  )
}

/* ---------- 展示面板子组件 ---------- */

const ShowcasePanel: React.FC = () => (
  <div className="rounded-xl bg-white/40 border border-white/50 p-4 backdrop-blur-xl shadow-lg overflow-hidden">
    <div className="grid grid-cols-2 gap-3 relative z-10">
      {/* 总体评分 */}
      <div className="bg-white/95 rounded-lg p-3 border border-white/60 shadow-sm">
        <div className="flex items-center justify-between mb-2.5">
          <span className="text-[11px] text-slate-700 font-semibold">示例评分 Sample Score</span>
          <span className="bg-green-50 p-0.5 rounded-full">
            <svg className="w-3 h-3 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
            </svg>
          </span>
        </div>
        <div className="flex items-center gap-3">
          <div className="relative w-10 h-10 rounded-full border-2 border-slate-100 flex items-center justify-center">
            <div className="absolute inset-0 rounded-full border-2 border-primary border-t-transparent transform rotate-45" />
            <span className="text-sm font-bold text-slate-900">92</span>
          </div>
          <div className="space-y-1 flex-1">
            <div className="h-1.5 w-full bg-slate-100 rounded-full overflow-hidden">
              <div className="h-full bg-primary w-[92%] rounded-full" />
            </div>
            <div className="text-[10px] font-medium text-slate-500 text-right">Excellent</div>
          </div>
        </div>
      </div>

      {/* 风险等级 */}
      <div className="bg-white/95 rounded-lg p-3 border border-white/60 shadow-sm">
        <div className="flex items-center justify-between mb-2.5">
          <span className="text-[11px] text-slate-700 font-semibold">示例风险 Sample Risk</span>
          <span className="bg-blue-50 p-0.5 rounded-full">
            <svg className="w-3 h-3 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
            </svg>
          </span>
        </div>
        <div className="flex items-center gap-2 mb-2">
          <span className="px-2 py-0.5 bg-green-100 text-green-800 text-[10px] rounded-full border border-green-200 font-semibold">
            低风险 Low Risk
          </span>
        </div>
        <div className="space-y-1">
          <div className="h-1.5 w-full bg-slate-100 rounded-full overflow-hidden">
            <div className="h-full w-1/4 bg-green-500 rounded-full" />
          </div>
          <div className="text-[10px] font-medium text-slate-500 text-right">发现 2 个风险项</div>
        </div>
      </div>

      {/* 合规性检查 */}
      <div className="col-span-2 bg-white/95 rounded-lg p-3 border border-white/60 shadow-sm">
        <div className="flex items-center justify-between mb-2">
          <span className="text-[11px] text-slate-700 font-semibold">响应检查 Response Check</span>
          <span className="text-[10px] text-primary font-semibold">示例 Sample</span>
        </div>
        <div className="space-y-1.5">
          <div className="flex items-center gap-2">
            <svg className="w-3 h-3 text-green-600 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <div className="h-1.5 w-full bg-slate-100 rounded-full overflow-hidden">
              <div className="h-full bg-green-500 w-full rounded-full" />
            </div>
          </div>
          <div className="flex items-center gap-2">
            <svg className="w-3 h-3 text-green-600 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <div className="h-1.5 w-full bg-slate-100 rounded-full overflow-hidden">
              <div className="h-full bg-green-500 w-3/4 rounded-full" />
            </div>
          </div>
          <div className="flex items-center gap-2">
            <svg className="w-3 h-3 text-yellow-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            <div className="h-1.5 w-full bg-slate-100 rounded-full overflow-hidden">
              <div className="h-full bg-yellow-400 w-5/6 rounded-full" />
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
)

export default AuthLayout