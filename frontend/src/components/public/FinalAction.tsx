import React from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import ServiceWechatContact from '@/components/ServiceWechatContact'
import { APP_HOME, REGISTER_PATH } from '@/config/routes'
import { publicHomeContent } from '@/content/publicHome'
import { useAuthStore } from '@/stores'

const FinalAction: React.FC = () => {
  const { isAuthenticated } = useAuthStore()
  const actionPath = isAuthenticated ? APP_HOME : REGISTER_PATH
  const actionLabel = isAuthenticated ? '进入工作台' : publicHomeContent.finalAction.primaryCta

  return (
    <section className="cta-glow cta-glow--on-dark relative overflow-hidden border-t border-white/10 bg-[#0A2540] py-14 sm:py-16">
      <div className="relative z-10 mx-auto max-w-6xl px-4 text-center sm:px-6 lg:px-8">
        <p className="text-sm font-semibold text-[#8A82FF]">准备开始了吗？</p>
        <h2 className="gradient-text-on-dark mt-3 text-3xl font-semibold tracking-tight max-sm:whitespace-normal whitespace-nowrap sm:text-4xl lg:text-5xl">
          {publicHomeContent.finalAction.title}
        </h2>
        <p className="mx-auto mt-5 max-w-2xl text-base leading-7 text-white/70">
          {publicHomeContent.finalAction.description}
        </p>
        <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Link
            to={actionPath}
            className="inline-flex h-12 items-center rounded-xl bg-white px-6 text-sm font-semibold text-[#0A2540] transition-colors hover:bg-slate-100 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-[#0A2540]"
          >
            {actionLabel}
            <ArrowRight className="ml-2 h-4 w-4" aria-hidden="true" />
          </Link>
          <a
            href="#trust"
            className="inline-flex h-12 items-center rounded-xl border border-white/25 bg-white/5 px-6 text-sm font-semibold text-white transition-colors hover:border-white/40 hover:bg-white/10 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-[#0A2540]"
          >
            {publicHomeContent.finalAction.secondaryCta}
          </a>
        </div>

        <ServiceWechatContact className="mt-10" tone="dark" triggerLabel="商务合作，联系客服" />
      </div>
    </section>
  )
}

export default FinalAction
