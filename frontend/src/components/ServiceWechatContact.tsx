/**
 * 职责: 展示客服微信二维码与微信号复制入口
 * 不做: 不提交意见反馈，不控制 FeedbackPanel 面板开闭
 */

import React from 'react'
import WechatQrCard from '@/components/WechatQrCard'
import { siteConfig } from '@/config/site'

interface ServiceWechatContactProps {
  className?: string
  /** 触发按钮所处底色，dark 用于深色区块；展开面板始终保持浅色以保证二维码可扫 */
  tone?: 'light' | 'dark'
  /** 触发按钮首行文案；倒三角始终单独居中于第二行 */
  triggerLabel?: string
}

const TRIGGER_TONES: Record<'light' | 'dark', string> = {
  light: 'text-slate-500 hover:text-slate-700',
  dark: 'text-white/60 hover:text-white/90',
}

const ServiceWechatContact: React.FC<ServiceWechatContactProps> = ({
  className = '',
  tone = 'light',
  triggerLabel = '紧急问题？联系客服',
}) => {
  const [expanded, setExpanded] = React.useState(false)

  if (!siteConfig.serviceWechatId || !siteConfig.serviceWechatQr) return null

  return (
    <div className={`text-center ${className}`.trim()}>
      <button
        type="button"
        onClick={() => setExpanded(open => !open)}
        className={`inline-flex flex-col items-center text-sm transition-colors ${TRIGGER_TONES[tone]}`}
        aria-expanded={expanded}
      >
        <span>{triggerLabel}</span>
        <span className="leading-none" aria-hidden="true">{expanded ? '▲' : '▼'}</span>
      </button>

      {expanded && (
        <div className="mx-auto mt-3 max-w-xs rounded-2xl border border-slate-200 bg-slate-50 p-4">
          <WechatQrCard />
        </div>
      )}
    </div>
  )
}

export default ServiceWechatContact
