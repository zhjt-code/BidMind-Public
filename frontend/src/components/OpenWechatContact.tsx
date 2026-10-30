/**
 * 职责: 用按钮打开已配置的微信二维码弹层
 * 不做: 不提交线索，不拉起微信客户端，不决定按钮出现在哪个页面
 */

import React from 'react'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import WechatQrCard from '@/components/WechatQrCard'
import { siteConfig } from '@/config/site'

interface OpenWechatContactProps {
  label: string
  title: string
  description: string
  className?: string
  labelClassName?: string
  children?: React.ReactNode
}

const OpenWechatContact: React.FC<OpenWechatContactProps> = ({
  label,
  title,
  description,
  className = '',
  labelClassName = '',
  children,
}) => {
  const [open, setOpen] = React.useState(false)

  if (!siteConfig.serviceWechatId || !siteConfig.serviceWechatQr) return null

  return (
    <>
      <button
        type="button"
        className={className}
        aria-label={label}
        title={label}
        onClick={() => setOpen(true)}
      >
        {children}
        <span className={labelClassName}>{label}</span>
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="z-60 max-w-sm">
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{description}</DialogDescription>
          </DialogHeader>
          <WechatQrCard frameClassName="h-44 w-44 border border-slate-200" alt="微信二维码" />
        </DialogContent>
      </Dialog>
    </>
  )
}

export default OpenWechatContact
