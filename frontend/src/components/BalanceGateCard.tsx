/**
 * 职责: 余额不足门禁卡片展示（center/inline 布局 × completed/retry 语义）
 * 不做: 发起业务请求、状态管理、查询钱包、轮询支付结果
 */
import { AlertTriangle, ArrowRight, CheckCircle } from 'lucide-react'

import { INSUFFICIENT_BALANCE_GATE_HINT, type BalanceGateCopy } from '@/lib/balanceGateCopy'

export type BalanceGateLayout = 'center' | 'inline'
export type BalanceGateSemantic = 'completed' | 'retry'

interface BalanceGateCardProps {
  /** 布局：center=整页居中完成卡；inline=工作页内嵌横幅卡 */
  layout: BalanceGateLayout
  /** 语义：completed=上一节点已完成（勾选图标）；retry=重试未启动（警告图标） */
  semantic: BalanceGateSemantic
  copy: BalanceGateCopy
  /** 兼容旧调用方；预占数字不向用户展示 */
  message?: string
  paymentEnabled: boolean
  continueBusy: boolean
  onRecharge: () => void
  onContinue: () => void
}

export default function BalanceGateCard({
  layout,
  semantic,
  copy,
  paymentEnabled,
  continueBusy,
  onRecharge,
  onContinue,
}: BalanceGateCardProps) {
  const helper = paymentEnabled
    ? '充值到账后点击继续即可。'
    : '支付功能未开启，请联系管理员补充点数后再继续。'

  if (layout === 'center') {
    return (
      <div className="text-center py-4">
        {semantic === 'completed' ? (
          <CheckCircle className="h-12 w-12 mx-auto text-amber-500 mb-4" />
        ) : (
          <AlertTriangle className="h-12 w-12 mx-auto text-amber-500 mb-4" />
        )}
        <h2 className="text-lg font-medium mb-2">{copy.title}</h2>
        <p className="text-sm text-muted-foreground mb-1">{copy.description}</p>
        <p className="text-sm text-amber-700 mb-1">{INSUFFICIENT_BALANCE_GATE_HINT}</p>
        <p className="text-xs text-muted-foreground mb-4">{helper}</p>
        <div className="flex items-center justify-center gap-3">
          {paymentEnabled && (
            <button
              type="button"
              onClick={onRecharge}
              className="px-4 py-2 text-sm rounded-md border border-amber-300 text-amber-800 bg-white hover:bg-amber-100"
            >
              去充值
            </button>
          )}
          <button
            type="button"
            onClick={onContinue}
            disabled={continueBusy}
            className={`px-4 py-2 text-sm rounded-md text-white inline-flex items-center gap-1 ${
              continueBusy ? 'bg-gray-400 cursor-not-allowed' : 'bg-primary hover:opacity-90'
            }`}
          >
            {continueBusy ? copy.busyLabel : copy.continueLabel}
            {!continueBusy && <ArrowRight className="h-4 w-4" />}
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="mb-4 p-4 bg-amber-50 border border-amber-200 rounded-lg flex items-center justify-between gap-4">
      <div className="min-w-0 flex items-start gap-3">
        {semantic === 'completed' ? (
          <CheckCircle className="h-5 w-5 text-amber-500 mt-0.5 flex-shrink-0" />
        ) : (
          <AlertTriangle className="h-5 w-5 text-amber-500 mt-0.5 flex-shrink-0" />
        )}
        <div className="min-w-0">
          <p className="text-sm text-amber-800 font-medium">{copy.title}</p>
          <p className="text-sm text-amber-700 mt-1">{copy.description}</p>
          <p className="text-sm text-amber-700 mt-1">{INSUFFICIENT_BALANCE_GATE_HINT}</p>
          <p className="text-xs text-amber-600 mt-1">{helper}</p>
        </div>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        {paymentEnabled && (
          <button
            type="button"
            onClick={onRecharge}
            className="px-3 py-1.5 text-sm rounded-md border border-amber-300 text-amber-800 bg-white hover:bg-amber-100"
          >
            去充值
          </button>
        )}
        <button
          type="button"
          onClick={onContinue}
          disabled={continueBusy}
          className={`px-3 py-1.5 text-sm rounded-md text-white ${
            continueBusy ? 'bg-gray-400 cursor-not-allowed' : 'bg-amber-600 hover:bg-amber-700'
          }`}
        >
          {continueBusy ? copy.busyLabel : copy.continueLabel}
        </button>
      </div>
    </div>
  )
}
