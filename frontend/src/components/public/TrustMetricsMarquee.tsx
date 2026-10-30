import React from 'react'
import { publicHomeContent, type TrustMetric } from '@/content/publicHome'

/**
 * 职责: 在公开首页尾部以深色沉稳的横向滚动条呈现社会证明数字，强化注册转化前的品牌气势。
 * 不做: 不请求真实运营数据、不承担埋点上报、不承诺未核验的指标口径。
 */

interface TrustMetricsMarqueeProps {
  metrics?: readonly TrustMetric[]
}

const DEFAULT_METRICS = publicHomeContent.trustMetrics

const renderMarqueeCard = (metric: TrustMetric, keySuffix: string) => (
  <li
    key={`${metric.key}-${keySuffix}`}
    className="relative mr-5 flex min-w-68 shrink-0 flex-col justify-center gap-3 overflow-hidden rounded-2xl border border-white/10 bg-white/4 px-8 py-7"
  >
    <div className="pointer-events-none absolute inset-x-0 top-0 h-24 bg-linear-to-b from-white/8 to-transparent" aria-hidden="true" />
    <span className="relative bg-linear-to-r from-[#5FE0D3] via-[#8A82FF] to-[#FFB07A] bg-clip-text text-[2.5rem] font-semibold leading-none tracking-tight text-transparent">
      {metric.value}
    </span>
    <span className="relative h-px w-10 bg-white/20" aria-hidden="true" />
    <span className="relative text-sm font-medium text-white/70">{metric.label}</span>
  </li>
)

const renderMobileCard = (metric: TrustMetric) => (
  <li
    key={metric.key}
    className="relative flex flex-col gap-2 overflow-hidden rounded-2xl border border-white/10 bg-white/4 px-5 py-5"
  >
    <div className="pointer-events-none absolute inset-x-0 top-0 h-16 bg-linear-to-b from-white/8 to-transparent" aria-hidden="true" />
    <span className="relative bg-linear-to-r from-[#5FE0D3] via-[#8A82FF] to-[#FFB07A] bg-clip-text text-2xl font-semibold leading-none tracking-tight text-transparent">
      {metric.value}
    </span>
    <span className="relative text-xs font-medium text-white/70">{metric.label}</span>
  </li>
)

const TrustMetricsMarquee: React.FC<TrustMetricsMarqueeProps> = ({ metrics = DEFAULT_METRICS }) => {
  if (metrics.length === 0) return null

  return (
    <section
      aria-label="平台信任指标"
      className="relative overflow-hidden bg-[#0A2540] py-10 sm:py-12"
    >
      <div
        className="pointer-events-none absolute inset-0 opacity-70"
        style={{
          background:
            'radial-gradient(1200px 400px at 20% 0%, rgba(99,91,255,0.18), transparent 70%),' +
            'radial-gradient(1000px 380px at 80% 100%, rgba(8,166,166,0.14), transparent 70%)',
        }}
        aria-hidden="true"
      />

      {/* 桌面端：横向无限滚动 marquee（用 mr 而非 gap，保证 A/B 完全等长以支持无缝循环） */}
      <div className="trust-marquee-viewport trust-marquee-mask relative hidden overflow-hidden sm:block">
        <ul
          className="trust-marquee-track flex w-max items-stretch"
          aria-label="信任数字指标"
        >
          {metrics.map(metric => renderMarqueeCard(metric, 'a'))}
          {metrics.map(metric => renderMarqueeCard(metric, 'b'))}
        </ul>
      </div>

      {/* 移动端：静态 2 列网格 */}
      <ul className="relative grid grid-cols-2 gap-3 px-4 sm:hidden" aria-label="信任数字指标">
        {metrics.map(metric => renderMobileCard(metric))}
      </ul>
    </section>
  )
}

export default TrustMetricsMarquee
