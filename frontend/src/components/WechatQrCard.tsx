/**
 * 职责: 展示已配置的微信二维码，并复制微信号
 * 不做: 不决定弹层或折叠，不拉起微信客户端，不提交线索
 */

import React from 'react'
import { siteConfig } from '@/config/site'

interface WechatQrCardProps {
  frameClassName?: string
  alt?: string
}

const WechatQrCard: React.FC<WechatQrCardProps> = ({
  frameClassName = 'h-28 w-28',
  alt = '客服微信二维码',
}) => {
  const [copied, setCopied] = React.useState(false)
  const [imgError, setImgError] = React.useState(false)

  React.useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(false), 2000)
    return () => window.clearTimeout(timer)
  }, [copied])

  const handleCopyWechat = async () => {
    try {
      await navigator.clipboard.writeText(siteConfig.serviceWechatId)
      setCopied(true)
    } catch {
      /* clipboard API 可能在 http 下不可用，静默失败 */
    }
  }

  return (
    <div className="space-y-2 text-center">
      <div className={`mx-auto flex items-center justify-center overflow-hidden rounded-lg bg-white ${frameClassName}`}>
        {!siteConfig.serviceWechatQr || imgError ? (
          <span className="px-2 text-xs text-slate-400">二维码待配置</span>
        ) : (
          <img
            src={siteConfig.serviceWechatQr}
            alt={alt}
            className="h-full w-full object-cover"
            onError={() => setImgError(true)}
          />
        )}
      </div>
      {siteConfig.serviceWechatId && <div className="flex items-center justify-center gap-2">
        <span className="text-xs text-slate-500">微信号: {siteConfig.serviceWechatId}</span>
        <button
          type="button"
          onClick={handleCopyWechat}
          className="text-xs font-semibold text-primary transition-colors hover:text-blue-600"
        >
          {copied ? '已复制' : '复制'}
        </button>
      </div>}
    </div>
  )
}

export default WechatQrCard
